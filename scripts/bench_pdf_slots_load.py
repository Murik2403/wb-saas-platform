"""Load scenario for the shared PDF slots: N real tenants, API and background processes busy at once, slots = 2.

A LOCAL Docker engine only, synthetic demo data, internal network. Every tenant is a real container (nginx + API +
resident background worker) mounting ONE shared volume at /pdf-slots with WB_SAAS_PDF_SLOTS=<slots>. In one window:

  * the API of every tenant takes dashboard traffic (several requests in flight per tenant) plus /healthz probes and a
    request for a PDF built inside the request (it may legitimately answer 503 + Retry-After while the slots are busy);
  * every tenant gets a REAL queued PDF run (all report metrics: pandas + matplotlib), which its resident
    background_worker builds under the shared slots.

The deadline is DECLARED before anything starts: ceil(N / slots) * pdf_seconds + margin. The scenario passes only if all
of the following hold (nothing is averaged away, a stand limit is a failure, never a pass):

  deadline         every run finished ``ok`` no later than the declared deadline, as a real PDF (header %PDF-, size)
  slots            at most <slots> PDFs ran at once (run intervals AND sampled lock files) and, when N >= slots,
                   all <slots> slots were actually used (otherwise the run proved nothing)
  health           /healthz never failed; its p95 under load within --health-p95-ms
  api              no 5xx on dashboard endpoints; the in-request PDF answered 200, or 503 with Retry-After, never anything else
  kernel memory    memory.events high / max / oom / oom_kill all unchanged, no container OOM flag, no restart
  stand            VM MemAvailable stayed at or above --min-vm-available-mib (below it = stand limit reached = FAIL)
  admission        the real tenant_budget decision for the next tenant is ``admitted``, taken BEFORE the load starts (a refusal is
                   REFUSED_NOT_RUN), and it saw all N live tenants
  jobs             every tenant got exactly one job: its own enqueue run id and definition id are recorded, the definition holds
                   exactly the 21 report metric codes of the image, that very run finished ok, no duplicate run, no delivery
  image            the image carries labels whose source trees equal this commit's tenant-app and tenant-api trees, and the key
                   files inside every container hash to this commit's blobs
  topology         every container: that image id, uid 10001, read-only root, no extra capabilities, the unchanged memory limit,
                   its own data volume plus the ONE shared /pdf-slots volume (rw), WB_SAAS_PDF_SLOTS = slots, API + resident
                   background_worker + nginx processes alive with the same PIDs before and after; the slot directory and every
                   lock file have the same device/inode in every tenant, owner 10001, exactly <slots> lock files
  observations     per-tenant driver, kernel and sampler evidence is complete; a sampler or driver error is a failure
  cleanup          only the run's own objects, recorded by full ID, were removed; none remains; nothing unrecorded carries the
                   run label; every object that existed before the run still exists

A run that would start below the floor is recorded REFUSED_NOT_RUN. The result carries the git HEAD it ran from and refuses
to run from a dirty tree (``--allow-dirty`` marks it).

    python scripts/bench_pdf_slots_load.py --image bench-tenant:pdfload --tenants 4 --slots 2 --out act.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "provisioner"))
import bench_pdf_budget as pdfb  # noqa: E402
import bench_tenant_density as base  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MIB = 1048576
MIN_REAL_PDF_BYTES = 10_000

STATE = r'''
import json, os, sqlite3, sys
sys.path.insert(0, "/app/tenant-app")
from config import DB_PATH
with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30) as conn:
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT id, definition_id, status, started_at, finished_at, attempts, delivery_requested, file_path, error FROM report_runs ORDER BY id")]
for row in rows:
    path = row.pop("file_path", None)
    if path and os.path.isfile(path):
        with open(path, "rb") as handle:
            row["pdf_header_ok"] = handle.read(5) == b"%PDF-"
        row["pdf_bytes"] = os.path.getsize(path)
slots = {}
try:
    from reports import pdf_budget
    slots = pdf_budget.status()
except Exception as exc:
    slots = {"error": type(exc).__name__}
print(json.dumps({"runs": rows, "slots": slots}))
'''

# One job per tenant: a NEW definition holding every report metric, and its own run. The ids are returned so the final state
# can be matched to exactly this enqueue (never "the latest run").
ENQUEUE_JOB = r'''
import json, sqlite3, sys
sys.path.insert(0, "/app/tenant-app")
from config import DB_PATH
from reports.store import ReportStore
from reports.catalog import METRIC_LABELS
with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30) as conn:
    before = {"runs": conn.execute("SELECT COUNT(*) FROM report_runs").fetchone()[0], "definitions": conn.execute("SELECT COUNT(*) FROM report_definitions").fetchone()[0]}
store = ReportStore(DB_PATH)
definition_id = store.add_definition(name="slots-load-" + sys.argv[1], metrics=list(METRIC_LABELS), schedule_type="daily", schedule_time="23:59")
saved = [d for d in store.list_definitions() if d["id"] == definition_id][0]
run_id = store.enqueue_run(definition_id)
print(json.dumps({"run_id": run_id, "definition_id": definition_id, "metrics": sorted(json.loads(saved["metrics"])), "before": before}))
'''
METRIC_CODES = r'''
import json, sys
sys.path.insert(0, "/app/tenant-app")
from reports.catalog import METRIC_LABELS
print(json.dumps(sorted(METRIC_LABELS)))
'''
LOCKS = r'''
import glob, json, os
def stat(path):
    info = os.stat(path)
    return {"dev": info.st_dev, "ino": info.st_ino, "uid": info.st_uid, "mode": info.st_mode & 0o7777}
print(json.dumps({"dir": stat("/pdf-slots"), "files": {os.path.basename(p): stat(p) for p in sorted(glob.glob("/pdf-slots/pdf-slot-*.lock"))}}))
'''
FILE_HASHES = r'''
import hashlib, json, sys
out = {}
for path in sys.argv[1:]:
    with open(path, "rb") as handle:
        out[path] = hashlib.sha256(handle.read().replace(b"\r\n", b"\n")).hexdigest()
print(json.dumps(out))
'''
# (path inside the container, path in the repository): the files that decide how a PDF is queued, leased and built
SOURCE_FILES = (("/app/tenant-app/reports/pdf_budget.py", "tenant-app/reports/pdf_budget.py"),
                ("/app/tenant-app/reports/report_scheduler.py", "tenant-app/reports/report_scheduler.py"),
                ("/app/tenant-app/reports/store.py", "tenant-app/reports/store.py"),
                ("/app/tenant-app/reports/pdf_builder.py", "tenant-app/reports/pdf_builder.py"),
                ("/app/tenant-app/reports/catalog.py", "tenant-app/reports/catalog.py"),
                ("/app/tenant-app/background_worker.py", "tenant-app/background_worker.py"),
                ("/app/tenant-app/api_server.py", "mh-redesign-lab/tenant-api/api_server.py"))
TENANT_MEMORY_BYTES = 1536 * 1048576  # the limit pdfb.Arm gives every tenant; the scenario never raises it

# Runs until SIGTERM, then prints one JSON line. Per tenant: PARALLEL workers loop over the dashboard endpoints, one probe
# loop for /healthz, and a loop that asks for a PDF built inside the request every PDF_EVERY seconds.
DRIVER = r'''
import json, signal, sys, threading, time, urllib.request, urllib.error
targets, paths, parallel, pdf_every = json.loads(sys.argv[1]), json.loads(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4])
stop, lock, out = threading.Event(), threading.Lock(), {}
worker_errors = []
def thread_failure(args):
    worker_errors.append(type(args.exc_value).__name__)
    stop.set()
threading.excepthook = thread_failure
signal.signal(signal.SIGTERM, lambda *a: stop.set())
def hit(host, path, timeout=180):
    started = time.perf_counter()
    try:
        request = urllib.request.Request(f"http://{host}:8501{path}", headers={"Host": host})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
            return response.status, None, (time.perf_counter() - started) * 1000
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Retry-After"), (time.perf_counter() - started) * 1000
    except Exception:
        return 0, None, (time.perf_counter() - started) * 1000
def worker(host, bucket):
    while not stop.is_set():
        for path in paths:
            if stop.is_set():
                break
            status, _, ms = hit(host, path)
            with lock:
                bucket["calc_ms"].append(ms)
                key = f"{status} {path.split('?')[0]}"
                bucket["calc_codes"][key] = bucket["calc_codes"].get(key, 0) + 1
def health(host, bucket):
    while not stop.is_set():
        status, _, ms = hit(host, "/healthz", 30)
        with lock:
            if status == 200:
                bucket["health_ms"].append(ms)
            else:
                bucket["health_fail"] += 1
        stop.wait(0.5)
def api_pdf(host, bucket):
    while not stop.is_set():
        status, retry_after, ms = hit(host, "/api/v1/operations/reports/pdf?days=30", 90)
        with lock:
            bucket["api_pdf"].append({"status": status, "retry_after": retry_after, "ms": round(ms)})
        stop.wait(pdf_every)
def tenant(host):
    bucket = {"calc_ms": [], "calc_codes": {}, "health_ms": [], "health_fail": 0, "api_pdf": []}
    out[host] = bucket
    threads = [threading.Thread(target=worker, args=(host, bucket)) for _ in range(parallel)]
    threads += [threading.Thread(target=health, args=(host, bucket)), threading.Thread(target=api_pdf, args=(host, bucket))]
    [t.start() for t in threads]
    [t.join() for t in threads]
threads = [threading.Thread(target=tenant, args=(t,)) for t in targets]
[t.start() for t in threads]
[t.join() for t in threads]
print(json.dumps({"buckets": out, "worker_errors": worker_errors}))
sys.exit(2 if worker_errors else 0)
'''


# ---- pure logic (unit-tested) ---------------------------------------------------------------------------------------

def declared_deadline(tenants: int, slots: int, pdf_seconds: float, margin_seconds: float) -> float:
    """The time every run may take from the moment the jobs are queued, fixed before the run starts."""
    if tenants < 1 or slots < 1 or pdf_seconds <= 0 or margin_seconds < 0:
        raise ValueError("tenants, slots and pdf_seconds must be positive")
    return math.ceil(tenants / slots) * pdf_seconds + margin_seconds


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, max(0, int(round(fraction * len(ordered))) - 1))]


def observation_failures(names, paths, buckets, before, after, samples, sampler_errors, driver_errors):
    """Missing observations are failures, never inferred zero load/OOM/peak."""
    failed = []
    if driver_errors:
        failed.append("drivers: subprocess or worker failed")
    if set(buckets) != set(names):
        failed.append("coverage: every tenant driver bucket is required")
    required_paths = {path.split("?")[0] for path in paths}
    for name in names:
        bucket = buckets.get(name, {})
        if not bucket.get("calc_ms") or not bucket.get("health_ms") or not bucket.get("api_pdf"):
            failed.append("coverage: tenant lacks dashboard, health or PDF observations: " + name)
        codes = bucket.get("calc_codes", {})
        seen = {key.split(" ", 1)[1] for key, count in codes.items() if " " in key and count > 0}
        if not required_paths <= seen or not any(key.startswith("200 ") and count > 0 for key, count in codes.items()):
            failed.append("coverage: tenant lacks requested paths or successful calculation: " + name)
        if any(key.split(" ", 1)[0] not in {"200", "409", "422"} for key in codes):
            failed.append("coverage: unexpected dashboard response: " + name)
    required = {"cur", "peak", "ev_high", "ev_max", "ev_oom", "ev_oom_kill", "cpu_nr_throttled", "cpu_nr_periods"}
    for label, observed in (("before", before), ("after", after)):
        if set(observed) != set(names):
            failed.append("kernel: incomplete tenant probes " + label)
        for name in names:
            row = observed.get(name, {})
            if not required <= set(row) or any(type(row.get(key)) is not int or row[key] < 0 for key in required):
                failed.append("kernel: missing counters or real peak " + label + " " + name)
            elif row["peak"] <= 0 or row["cur"] <= 0:
                failed.append("kernel: nonpositive memory observation " + name)
    if sampler_errors or not samples:
        failed.append("sampler: failed or no load-window observations")
    if any(type(memory) is not int or memory <= 0 or type(busy) is not int or busy < 0 for memory, busy in samples):
        failed.append("sampler: missing memory or real lock observations")
    return failed


def job_failures(names: list[str], jobs: dict, runs: dict, expected_metrics: list[str]) -> list[str]:
    """Each tenant's run is identified by the ids returned when it was enqueued, never by position or recency."""
    failed = []
    for name in names:
        job, rows = jobs.get(name), runs.get(name)
        if not isinstance(job, dict) or type(job.get("run_id")) is not int or type(job.get("definition_id")) is not int:
            failed.append("jobs: no enqueue ids recorded for " + name)
            continue
        if job.get("metrics") != sorted(expected_metrics) or len(expected_metrics) != len(set(expected_metrics)):
            failed.append(f"jobs: {name} definition does not hold exactly the {len(expected_metrics)} report metric codes of the image")
        if not isinstance(rows, list) or not rows:
            failed.append("jobs: no final state read for " + name)
            continue
        mine = [row for row in rows if row.get("id") == job["run_id"]]
        if len(mine) != 1:
            failed.append(f"jobs: {name} run {job['run_id']} not found exactly once")
            continue
        row = mine[0]
        if row.get("status") != "ok" or row.get("definition_id") != job["definition_id"]:
            failed.append(f"jobs: {name} run {job['run_id']} ended {row.get('status')} (definition {row.get('definition_id')}, enqueued {job['definition_id']})")
        if row.get("delivery_requested"):
            failed.append(f"jobs: {name} run requested a delivery (email/Telegram), which a benchmark must never do")
        if sum(1 for other in rows if other.get("definition_id") == job["definition_id"]) != 1 or len(rows) != job.get("before", {}).get("runs", -1) + 1:
            failed.append(f"jobs: {name} has duplicate or foreign runs ({len(rows)} rows, {job.get('before', {}).get('runs')} before)")
    return failed


def process_roles(rows: list[dict]) -> dict:
    roles: dict = {"api": [], "worker": [], "nginx": []}
    for row in rows:
        text = row.get("args", "")
        if "background_worker" in text:
            roles["worker"].append(row["pid"])
        elif "uvicorn" in text or "api_server" in text:
            roles["api"].append(row["pid"])
        elif "nginx: master" in text:
            roles["nginx"].append(row["pid"])
    return roles


def topology_failures(names: list[str], expected: dict, before: dict, after: dict) -> list[str]:
    """expected: image_id, slot_volume, slots, memory_bytes. before/after: per tenant {"inspect": <docker inspect>, "processes": [...],
    "locks": {...} (after only)}. Every property is attested from the engine, none is assumed from the command line."""
    failed = []
    data_volumes = []
    for name in names:
        row = before.get(name)
        if not row or not isinstance(row.get("inspect"), dict) or name not in after or not isinstance(after[name].get("inspect"), dict):
            failed.append("topology: no inspect data for " + name)
            continue
        info = row["inspect"]
        config, host, state = info.get("Config", {}), info.get("HostConfig", {}), info.get("State", {})
        if info.get("Image") != expected["image_id"]:
            failed.append(f"topology: {name} runs image {info.get('Image')}, expected {expected['image_id']}")
        if config.get("User") != "10001:10001":
            failed.append(f"topology: {name} runs as {config.get('User')}, expected 10001:10001")
        if host.get("ReadonlyRootfs") is not True or host.get("Privileged") or "ALL" not in (host.get("CapDrop") or []) or host.get("CapAdd"):
            failed.append(f"topology: {name} is not read-only/unprivileged/capability-free")
        if host.get("Memory") != expected["memory_bytes"]:
            failed.append(f"topology: {name} memory limit {host.get('Memory')} differs from {expected['memory_bytes']}")
        environment = dict(item.split("=", 1) for item in config.get("Env", []) if "=" in item)
        if environment.get("WB_SAAS_PDF_SLOTS") != str(expected["slots"]) or environment.get("WB_SAAS_PDF_SLOT_DIR") != "/pdf-slots":
            failed.append(f"topology: {name} slot environment is {environment.get('WB_SAAS_PDF_SLOTS')}@{environment.get('WB_SAAS_PDF_SLOT_DIR')}")
        mounts = {m.get("Destination"): m for m in info.get("Mounts", [])}
        slot_mount = mounts.get("/pdf-slots")
        if not slot_mount or slot_mount.get("Type") != "volume" or slot_mount.get("Name") != expected["slot_volume"] or slot_mount.get("RW") is not True:
            failed.append(f"topology: {name} does not mount the shared slot volume read-write at /pdf-slots")
        data_mount = mounts.get("/app/data")
        if not data_mount or data_mount.get("Type") != "volume" or data_mount.get("Name") != f"{name}-data" or data_mount.get("RW") is not True:
            failed.append(f"topology: {name} does not own its data volume")
        else:
            data_volumes.append(data_mount["Name"])
        if len(mounts) != 2 + sum(1 for d in mounts if d.startswith(("/tmp", "/var/", "/run"))):
            failed.append(f"topology: {name} has unexpected mounts {sorted(mounts)}")
        roles_before, roles_after = process_roles(row.get("processes", [])), process_roles(after[name].get("processes", []))
        for role in ("api", "worker", "nginx"):
            if not roles_before[role] or roles_before[role] != roles_after[role]:
                failed.append(f"topology: {name} {role} process missing or restarted ({roles_before[role]} -> {roles_after[role]})")
        end = after[name]["inspect"].get("State", {})
        if not end.get("Running") or end.get("OOMKilled") or end.get("RestartCount", 0) != state.get("RestartCount", 0) or end.get("StartedAt") != state.get("StartedAt"):
            failed.append(f"topology: {name} was restarted, stopped or OOM-killed during the load")
    if len(set(data_volumes)) != len(data_volumes):
        failed.append("topology: two tenants share a data volume")
    locks = {name: after.get(name, {}).get("locks") for name in names}
    reference = next((value for value in locks.values() if isinstance(value, dict)), None)
    wanted = {f"pdf-slot-{index:02d}.lock" for index in range(expected["slots"])}
    for name, value in locks.items():
        if not isinstance(value, dict) or not isinstance(value.get("dir"), dict):
            failed.append("topology: no lock identity read for " + name)
            continue
        if len(value.get("files", {})) != expected["slots"] or not set(value["files"]) <= wanted:
            failed.append(f"topology: {name} sees lock files {sorted(value.get('files', {}))} for {expected['slots']} slots")
        if value["dir"].get("uid") != 10001 or any(f.get("uid") != 10001 for f in value["files"].values()):
            failed.append(f"topology: {name} slot directory or locks are not owned by 10001")
        if value["dir"].get("mode", 0) & 0o007 or any(f.get("mode", 0) & 0o007 for f in value["files"].values()):
            failed.append(f"topology: {name} slot directory or locks are accessible to others")
        if reference is not None and (value["dir"].get("dev"), value["dir"].get("ino")) != (reference["dir"].get("dev"), reference["dir"].get("ino")):
            failed.append(f"topology: {name} slot directory is not the same directory as the other tenants'")
        if reference is not None and {k: (v.get("dev"), v.get("ino")) for k, v in value["files"].items()} != {k: (v.get("dev"), v.get("ino")) for k, v in reference["files"].items()}:
            failed.append(f"topology: {name} lock files are not the same files as the other tenants'")
    return failed


def image_failures(image: dict, trees: dict, tenants_hashes: dict, blobs: dict) -> list[str]:
    """image: {"id", "labels"}; trees: this commit's tenant-app/tenant-api trees and the trees of the commit the image says it was built from;
    tenants_hashes: per tenant {container path: sha256}; blobs: {container path: sha256 of this commit's blob, CRLF-normalised}."""
    failed = []
    if not image.get("id") or not str(image["id"]).startswith("sha256:"):
        failed.append("image: no image id")
    if (not trees.get("built_from") or not all(trees.get(key) for key in ("head_tenant_app", "head_tenant_api", "built_from_tenant_app", "built_from_tenant_api"))
            or trees["built_from_tenant_app"] != trees["head_tenant_app"] or trees["built_from_tenant_api"] != trees["head_tenant_api"]):
        failed.append("image: the commit the image was built from has different tenant-app/tenant-api sources than this commit "
                      f"({trees.get('built_from')}: {trees.get('built_from_tenant_app')} / {trees.get('built_from_tenant_api')})")
    for name, hashes in tenants_hashes.items():
        if hashes != blobs:
            wrong = sorted(path for path in blobs if hashes.get(path) != blobs[path])
            failed.append(f"image: {name} runs files that differ from this commit: {wrong}")
    if not tenants_hashes:
        failed.append("image: no file hashes read from any tenant")
    return failed


def cleanup_failures(cleanup) -> list[str]:
    if not isinstance(cleanup, dict):
        return ["cleanup: no ownership report"]
    failed = []
    owned = cleanup.get("owned", {})
    total = sum(owned.values()) if isinstance(owned, dict) else 0
    if total == 0 or owned.get("container", 0) == 0:
        failed.append("cleanup: the run recorded no objects it created")
    if cleanup.get("removed_by_full_id") != total or not cleanup.get("removed_ids_all_full"):
        failed.append(f"cleanup: {cleanup.get('removed_by_full_id')} of {total} recorded objects were removed by full ID")
    if cleanup.get("owned_remaining"):
        failed.append(f"cleanup: own objects remain: {cleanup['owned_remaining']}")
    if cleanup.get("unexpected_labelled"):
        failed.append(f"cleanup: objects carrying the run label were not recorded and were left alone: {cleanup['unexpected_labelled']}")
    if cleanup.get("foreign_missing"):
        failed.append(f"cleanup: objects that existed before the run are gone: {cleanup['foreign_missing']}")
    return failed


def evaluate(result: dict, criteria: dict) -> tuple[str, list[str]]:
    """PASS only when every named check holds; the failed checks are listed, never softened."""
    failed: list[str] = []
    observations = result.get("observation_failures")
    if type(observations) is not list or observations:
        failed.append("observability: complete per-tenant load/kernel/driver/sampler evidence required")
    attestation = result.get("attestation_failures")
    if type(attestation) is not list or attestation:
        failed.append("attestation: image, topology and job identity must be proven: " + "; ".join(attestation) if type(attestation) is list else "attestation: not recorded")
    n, slots = result["tenants"], result["slots"]
    runs = result["runs"]
    if len(runs) != n or any(r["status"] != "ok" for r in runs):
        failed.append("runs: not every tenant finished its PDF run with status ok")
    if result["seconds_to_last_run"] is None or result["seconds_to_last_run"] > criteria["deadline_seconds"]:
        failed.append(f"deadline: last run finished after {result['seconds_to_last_run']} s, declared {criteria['deadline_seconds']} s")
    if any(not (r.get("pdf_header_ok") and r.get("pdf_bytes", 0) >= MIN_REAL_PDF_BYTES) for r in runs):
        failed.append("pdf: a run did not leave a real PDF (header %PDF- and a plausible size)")
    if result["peak_concurrent_pdfs_from_intervals"] > slots:
        failed.append(f"slots: {result['peak_concurrent_pdfs_from_intervals']} PDFs overlapped in time with {slots} slots")
    busy = result.get("peak_busy_slots_sampled")
    if busy is not None and busy > slots:
        failed.append(f"slots: {busy} slot files were held at once with {slots} slots")
    if n >= slots and max(result["peak_concurrent_pdfs_from_intervals"], busy or 0) < slots:
        failed.append(f"slots: never more than {max(result['peak_concurrent_pdfs_from_intervals'], busy or 0)} PDFs at once: the run did not use all {slots} slots")
    health = result["healthz"]
    if health["failures"] or health["n"] == 0:
        failed.append(f"health: {health['failures']} failed /healthz probes ({health['n']} answered)")
    elif health["p95_ms"] > criteria["health_p95_ms"]:
        failed.append(f"health: /healthz p95 {health['p95_ms']} ms above {criteria['health_p95_ms']} ms")
    if result["api"]["dashboard_5xx_or_no_answer"]:
        failed.append(f"api: {result['api']['dashboard_5xx_or_no_answer']} dashboard requests failed with 5xx or no answer")
    if result["api"]["pdf_in_request_bad"]:
        failed.append(f"api: {result['api']['pdf_in_request_bad']} in-request PDF answers were neither 200 nor 503 with Retry-After")
    memory = result["kernel_memory"]
    for key in ("high", "max", "oom", "oom_kill"):
        if memory["events"][key]:
            failed.append(f"kernel memory: memory.events {key} grew by {memory['events'][key]}")
    if memory["oom_killed_containers"] or memory["restarts"]:
        failed.append(f"kernel memory: {memory['oom_killed_containers']} OOM-killed containers, {memory['restarts']} restarts")
    if result["vm_min_available_mib"] < criteria["floor_mib"]:
        failed.append(f"stand: VM MemAvailable fell to {result['vm_min_available_mib']} MiB, below the {criteria['floor_mib']} MiB floor (stand limit reached)")
    admission = result.get("admission", {})
    if not admission.get("decision"):
        failed.append("admission: no decision was recorded")
    elif admission["decision"] != "admitted":
        failed.append(f"admission: the real budget {admission['decision']} the next tenant ({admission.get('reason')}): not an acceptance")
    elif not (type(admission.get("decided_at")) is float and type(result.get("load_started_at")) is float and admission["decided_at"] < result["load_started_at"]):
        failed.append("admission: the decision was not taken before the load started")
    elif admission.get("running_tenants") != n or not admission.get("reserved_mib"):
        failed.append(f"admission: the decision saw {admission.get('running_tenants')} running tenants and {admission.get('reserved_mib')} MiB reserved for {n} live tenants "
                      "(it did not measure the real engine state)")
    residual = result.get("cleanup_residual_counts")
    if residual is None or any(residual.values()):
        failed.append(f"cleanup: resources remain or were not verified: {residual}")
    failed += cleanup_failures(result.get("cleanup"))
    return ("PASS" if not failed else "FAIL"), failed


def git_state() -> dict:
    def run(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return {"head": run("rev-parse", "HEAD"), "branch": run("rev-parse", "--abbrev-ref", "HEAD"), "dirty": bool(run("status", "--porcelain"))}


# ---- the scenario ---------------------------------------------------------------------------------------------------

def vm_available(via: str) -> int:
    return base.vm_available_mib(via)


def admission_decision(arm: pdfb.Arm, reservation: str = "384m") -> dict:
    """The real tenant_budget code against the real engine state: would one MORE tenant be admitted right now?"""
    import docker as sdk
    from tenant_budget import CapacityExhausted, TenantBudget, parse_size
    client, budget = sdk.DockerClient(base_url=base._LOCAL_ENDPOINT), TenantBudget()
    via = arm.names[0]
    keys = ("budget_mib", "reserved_mib", "free_mib", "running_tenants", "host_available_mib")
    need = parse_size(reservation)
    try:
        budget.acquire(client, "bench-next-tenant", need=need, configured="", fraction=0.7, min_available=parse_size("1g"), wait_seconds=0,
                       host_available=lambda: vm_available(via) * MIB)
        budget.release("bench-next-tenant")
        snapshot = budget.snapshot(client, "bench-next-tenant", need=need, configured="", fraction=0.7, min_available=parse_size("1g"),
                                   host_available=lambda: vm_available(via) * MIB)
        return {"decision": "admitted", **{k: snapshot.get(k) for k in keys}}
    except CapacityExhausted as exc:
        return {"decision": "refused", "reason": exc.info.get("reason"), **{k: exc.info.get(k) for k in keys}}


def residual_counts(report: dict) -> dict:
    remaining = report.get("owned_remaining", {})
    unexpected = report.get("unexpected_labelled", [])
    return {kind: len(remaining.get(kind, [])) + sum(1 for item in unexpected if item.startswith(kind + ":")) for kind in ("container", "volume", "network")}


def inspect_tenant(name: str) -> dict:
    return json.loads(base.docker("inspect", name))[0]


def tenant_processes(name: str) -> list[dict]:
    rows = []
    for line in base.docker("top", name, "-eo", "pid,user,args", check=False).splitlines()[1:]:
        fields = line.split(None, 2)
        if len(fields) == 3:
            rows.append({"pid": fields[0], "user": fields[1], "args": fields[2]})
    return rows


def collect_topology(arm: pdfb.Arm, with_locks: bool) -> dict:
    out = {}
    for name in arm.names:
        row = {"inspect": inspect_tenant(name), "processes": tenant_processes(name)}
        if with_locks:
            row["locks"] = json.loads(base.docker("exec", name, "python3", "-c", LOCKS, check=False).splitlines()[-1])
        out[name] = row
    return out


def git_trees(revision: str | None) -> dict:
    def tree(rev, path):
        return subprocess.run(["git", "rev-parse", f"{rev}:{path}"], cwd=ROOT, capture_output=True, text=True).stdout.strip() or None
    return {"head_tenant_app": tree("HEAD", "tenant-app"), "head_tenant_api": tree("HEAD", "mh-redesign-lab/tenant-api"), "built_from": revision,
            "built_from_tenant_app": tree(revision, "tenant-app") if revision else None, "built_from_tenant_api": tree(revision, "mh-redesign-lab/tenant-api") if revision else None}


def repository_blobs() -> dict:
    out = {}
    for container_path, repo_path in SOURCE_FILES:
        data = subprocess.run(["git", "show", f"HEAD:{repo_path}"], cwd=ROOT, capture_output=True).stdout
        out[container_path] = hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest() if data else None
    return out


def collect_image(arm: pdfb.Arm, args) -> tuple[dict, dict, dict, dict, list[str]]:
    info = json.loads(base.docker("image", "inspect", args.image))[0]
    image = {"reference": args.image, "id": info.get("Id"), "labels": info.get("Config", {}).get("Labels") or {}}
    revision = image["labels"].get("wb-saas.bench-head") or image["labels"].get("org.opencontainers.image.revision")
    hashes = {}
    for name in arm.names:
        out = base.docker("exec", name, "python3", "-c", FILE_HASHES, *[path for path, _ in SOURCE_FILES], check=False).splitlines()
        hashes[name] = json.loads(out[-1]) if out else {}
    codes = json.loads(base.docker("run", "--rm", "--network", "none", "--entrypoint", "python3", args.image, "-c", METRIC_CODES).splitlines()[-1])
    return image, git_trees(revision), hashes, repository_blobs(), codes


def enqueue_jobs(arm: pdfb.Arm) -> dict:
    def one(name):
        out = base.docker("exec", name, "python3", "-c", ENQUEUE_JOB, arm.run_id).splitlines()
        return name, json.loads(out[-1])
    with ThreadPoolExecutor(max_workers=len(arm.names)) as pool:
        return dict(pool.map(one, arm.names))


def run_drivers(arm: pdfb.Arm, args, paths: list[str]) -> list[str]:
    names = []
    groups = [arm.names[i::args.drivers] for i in range(args.drivers)]
    for k, group in enumerate(groups):
        if not group:
            continue
        name = f"{args.prefix}-load{k}-{arm.run_id}"
        arm.adopt("container", base.docker("run", "-d", "--name", name, "--network", arm.network, *arm.label(), "-u", "10001:10001", "--cap-drop", "ALL", "--read-only",
                                           "--memory", "256m", "--oom-score-adj", "1000", "--entrypoint", "python3", args.image, "-c", DRIVER, json.dumps(group), json.dumps(paths),
                                           str(args.api_parallel), str(args.api_pdf_every)), name)
        names.append(name)
    return names


def collect_drivers(names: list[str]) -> tuple[dict, list[str]]:
    merged: dict = {}
    errors = []
    for name in names:
        base.docker("kill", "-s", "TERM", name, check=False)
    for name in names:
        exit_code = base.docker("wait", name, check=False, timeout=240)
        if exit_code != "0":
            errors.append("DriverExit")
        text = base.docker("logs", name, check=False).strip().splitlines()
        payload = json.loads(text[-1]) if text else {}
        if set(payload) != {"buckets", "worker_errors"} or type(payload["buckets"]) is not dict or type(payload["worker_errors"]) is not list:
            errors.append("InvalidDriverResult")
            continue
        errors.extend(payload["worker_errors"])
        for tenant, bucket in payload["buckets"].items():
            if tenant in merged:
                errors.append("DuplicateTenantBucket")
            merged[tenant] = bucket
    return merged, errors


def summarize_api(buckets: dict) -> tuple[dict, dict]:
    calc, health, codes, pdf = [], [], {}, []
    health_fail = 0
    for bucket in buckets.values():
        calc += bucket["calc_ms"]
        health += bucket["health_ms"]
        health_fail += bucket["health_fail"]
        pdf += bucket["api_pdf"]
        for key, count in bucket["calc_codes"].items():
            codes[key] = codes.get(key, 0) + count
    bad5xx = sum(c for k, c in codes.items() if k.startswith("0 ") or k.startswith("5"))
    ok_pdf = [p for p in pdf if p["status"] == 200 or (p["status"] == 503 and p["retry_after"])]
    api = {"dashboard_requests": sum(codes.values()), "dashboard_p50_ms": round(percentile(calc, .5)), "dashboard_p95_ms": round(percentile(calc, .95)),
           "dashboard_max_ms": round(max(calc, default=0)), "dashboard_5xx_or_no_answer": bad5xx, "dashboard_status_by_endpoint": codes,
           "pdf_in_request_total": len(pdf), "pdf_in_request_200": sum(p["status"] == 200 for p in pdf), "pdf_in_request_503_retry_after": sum(p["status"] == 503 and bool(p["retry_after"]) for p in pdf),
           "pdf_in_request_bad": len(pdf) - len(ok_pdf), "pdf_in_request_max_ms": max((p["ms"] for p in pdf), default=0)}
    return api, {"n": len(health), "failures": health_fail, "p50_ms": round(percentile(health, .5)), "p95_ms": round(percentile(health, .95)), "max_ms": round(max(health, default=0))}


def scenario(args) -> dict:
    git = git_state()
    if git["dirty"] and not args.allow_dirty:
        raise SystemExit("Refusing to run from a dirty working tree: commit first so the act names the commit it ran from (or pass --allow-dirty).")
    deadline = declared_deadline(args.tenants, args.slots, args.pdf_seconds, args.margin_seconds)
    criteria = {"deadline_seconds": deadline, "health_p95_ms": args.health_p95_ms, "floor_mib": args.min_vm_available_mib}
    result: dict = {"scenario": "shared PDF slots under simultaneous API and background load", "git": git, "tenants": args.tenants, "slots": args.slots,
                    "declared_before_start": {**criteria, "formula": "ceil(tenants/slots)*pdf_seconds+margin", "pdf_seconds": args.pdf_seconds, "margin_seconds": args.margin_seconds,
                                              "api_parallel_per_tenant": args.api_parallel, "api_pdf_request_every_seconds": args.api_pdf_every},
                    "started": time.strftime("%Y-%m-%d %H:%M:%S"), "image": args.image, "topology": "nginx + API + resident background_worker per tenant, shared slot volume"}
    base.assert_local_engine()
    arm = pdfb.Arm(args, "load", args.slots)
    drivers: list[str] = []
    stop = watcher = None
    try:
        arm.start()
        arm.wait_healthy()
        time.sleep(args.settle)
        via = arm.names[0]
        idle = {n: base.probe(n) for n in arm.names}
        result["idle_vm_available_mib"] = vm_available(via)
        image, trees, hashes, blobs, metric_codes = collect_image(arm, args)
        result["image"] = {"reference": args.image, "id": image["id"], "labels": {k: v for k, v in image["labels"].items() if k.startswith(("wb-saas.", "org.opencontainers.image.revision"))},
                           "source_trees": trees, "key_file_sha256_in_every_tenant": blobs}
        topology_before = collect_topology(arm, False)
        result["admission"] = admission_decision(arm) | {"decided_at": time.time()}
        if result["admission"]["decision"] != "admitted":
            result.update(verdict="REFUSED_NOT_RUN", failed_checks=[f"admission: the real tenant budget {result['admission']['decision']} the next tenant "
                                                                    f"({result['admission'].get('reason')}) before the load started"])
            return result
        per_tenant = statistics.mean(base.mib(v.get("cur", 0)) for v in idle.values())
        need = args.slots * args.pdf_peak_delta_mib + args.tenants * args.api_delta_mib
        if result["idle_vm_available_mib"] - need < args.min_vm_available_mib:
            result.update(verdict="REFUSED_NOT_RUN", failed_checks=[f"stand: starting the load would leave {result['idle_vm_available_mib'] - need} MiB, below the "
                                                                    f"{args.min_vm_available_mib} MiB floor (per tenant idle {per_tenant:.0f} MiB)"])
            return result
        paths = pdfb_paths(args)
        samples: list[tuple[int, int | None]] = []
        sampler_errors = []
        stop = threading.Event()

        def sample():
            while not stop.is_set():
                try:
                    samples.append((vm_available(via), arm.state(via)["slots"].get("host_busy")))
                except Exception as error:
                    sampler_errors.append(type(error).__name__)
                stop.wait(1.0)
        watcher = threading.Thread(target=sample, daemon=True)
        watcher.start()
        t0 = time.time()
        result["load_started_at"] = t0
        drivers = run_drivers(arm, args, paths)
        jobs = enqueue_jobs(arm)
        deadline_at = t0 + deadline
        while time.time() < deadline_at + args.grace_seconds and not finished(arm):
            time.sleep(2)
        seconds_to_last = None
        runs = {n: state_runs(arm, n) for n in arm.names}
        mine = {n: next((r for r in runs[n] if r["id"] == jobs[n]["run_id"]), None) for n in arm.names}
        if all(r and r["status"] in ("ok", "error") and r["finished_at"] for r in mine.values()):
            seconds_to_last = round(max(pdfb.parse_ts(r["finished_at"]) for r in mine.values()) - t0, 1)
        stop.set()
        watcher.join(timeout=5)
        buckets, driver_errors = collect_drivers(drivers)
        after = {n: base.probe(n) for n in arm.names}
        topology_after = collect_topology(arm, True)
        flat = [mine[n] or {"status": "missing", "started_at": None, "finished_at": None} for n in arm.names]
        attestation = (image_failures(image, trees, hashes, blobs) + topology_failures(arm.names, {"image_id": image["id"], "slot_volume": arm.slot_volume, "slots": args.slots,
                       "memory_bytes": TENANT_MEMORY_BYTES}, topology_before, topology_after) + job_failures(arm.names, jobs, runs, metric_codes))
        if len(metric_codes) != args.expect_metrics:
            attestation.append(f"jobs: the image has {len(metric_codes)} report metric codes, {args.expect_metrics} expected")
        intervals = [(pdfb.parse_ts(r["started_at"]), pdfb.parse_ts(r["finished_at"])) for r in flat if r["status"] == "ok" and r["finished_at"]]
        topology = {n: {"image": topology_after[n]["inspect"].get("Image"), "user": topology_after[n]["inspect"]["Config"].get("User"),
                        "mounts": sorted((m.get("Destination"), m.get("Type"), m.get("Name")) for m in topology_after[n]["inspect"].get("Mounts", [])),
                        "roles_before": process_roles(topology_before[n]["processes"]), "roles_after": process_roles(topology_after[n]["processes"]),
                        "locks": topology_after[n]["locks"]} for n in arm.names}
        api, health = summarize_api(buckets)
        result.update(
            jobs=jobs, topology=topology, metric_codes=metric_codes, attestation_failures=attestation,
            observation_failures=observation_failures(arm.names, paths, buckets, idle, after, samples, sampler_errors, driver_errors),
            runs=[{k: r.get(k) for k in ("status", "attempts", "pdf_header_ok", "pdf_bytes", "error")} | {"started_at": r["started_at"], "finished_at": r["finished_at"]} for r in flat],
            seconds_to_last_run=seconds_to_last, peak_concurrent_pdfs_from_intervals=pdfb.peak_concurrency(intervals),
            peak_busy_slots_sampled=max((s[1] or 0 for s in samples), default=None), vm_min_available_mib=min((s[0] for s in samples), default=result["idle_vm_available_mib"]),
            vm_drop_mib=result["idle_vm_available_mib"] - min((s[0] for s in samples), default=result["idle_vm_available_mib"]),
            api=api, healthz=health,
            kernel_memory={"events": {k: sum(v.get(f"ev_{k}", 0) for v in after.values()) - sum(v.get(f"ev_{k}", 0) for v in idle.values()) for k in ("high", "max", "oom", "oom_kill")},
                           "oom_killed_containers": sum(base.docker("inspect", "--format", "{{.State.OOMKilled}}", n, check=False) == "true" for n in arm.names),
                           "restarts": sum(int(base.docker("inspect", "--format", "{{.RestartCount}}", n, check=False) or 0) for n in arm.names),
                           "container_peak_mib_max": max(base.mib(v.get("peak", 0)) for v in after.values()),
                           "cpu_throttled_pct": round(100 * (sum(v.get("cpu_nr_throttled", 0) for v in after.values()) - sum(v.get("cpu_nr_throttled", 0) for v in idle.values()))
                                                      / max(1, sum(v.get("cpu_nr_periods", 0) for v in after.values()) - sum(v.get("cpu_nr_periods", 0) for v in idle.values())), 1)})
    except Exception as error:  # a harness fault is a failed act with its reason, never a traceback and never a pass
        result.update(verdict="FAIL", failed_checks=[f"harness: {type(error).__name__}: {str(error)[:300]}"])
    finally:
        if stop is not None:
            stop.set()
        if watcher is not None:
            watcher.join(timeout=5)
            if watcher.is_alive():
                result.setdefault("observation_failures", []).append("sampler: did not stop before cleanup")
        arm.stop()
        result["cleanup"] = arm.cleanup_report()
        result["cleanup_residual_counts"] = residual_counts(result["cleanup"])
    if "verdict" not in result:
        result["verdict"], result["failed_checks"] = evaluate(result, criteria)
    elif result["verdict"] == "FAIL":
        result["failed_checks"] += [f"cleanup: {line}" for line in cleanup_failures(result.get("cleanup"))]
    result["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return result


def state_runs(arm: pdfb.Arm, name: str) -> list[dict]:
    return json.loads(base.docker("exec", name, "python3", "-c", STATE, check=False).splitlines()[-1])["runs"]


def finished(arm: pdfb.Arm) -> bool:
    runs = [state_runs(arm, n) for n in arm.names]
    return all(r and all(x["status"] in ("ok", "error") for x in r) for r in runs)


def pdfb_paths(args) -> list[str]:
    demo = json.loads(Path(args.endpoints_from).read_text(encoding="utf-8"))
    paths = sorted({f"/api/v1/{k.split('@')[0]}" for k in demo if k.split("@")[0].startswith(("dashboard/", "control/")) and "__" not in k})
    return paths + [p + "?period=90d" for p in paths[:args.heavy_variants]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", required=True)
    parser.add_argument("--tenants", type=int, default=4)
    parser.add_argument("--slots", type=int, default=2)
    parser.add_argument("--pdf-seconds", type=float, default=60.0, help="declared time budget of ONE PDF under this load (observed ~28-45 s)")
    parser.add_argument("--margin-seconds", type=float, default=60.0)
    parser.add_argument("--grace-seconds", type=float, default=30.0, help="how long past the declared deadline the harness keeps waiting, only to record how late a failing run was")
    parser.add_argument("--health-p95-ms", type=float, default=3000.0)
    parser.add_argument("--min-vm-available-mib", type=int, default=1500)
    parser.add_argument("--pdf-peak-delta-mib", type=int, default=250, help="memory one more PDF is assumed to add, for the pre-run feasibility gate")
    parser.add_argument("--api-delta-mib", type=int, default=40, help="memory the API load adds per tenant, for the pre-run feasibility gate")
    parser.add_argument("--api-parallel", type=int, default=3)
    parser.add_argument("--api-pdf-every", type=float, default=20.0)
    parser.add_argument("--drivers", type=int, default=2)
    parser.add_argument("--heavy-variants", type=int, default=6)
    parser.add_argument("--settle", type=int, default=30)
    parser.add_argument("--prefix", default="bench")
    parser.add_argument("--expect-metrics", type=int, default=21, help="how many report metric codes the image must define (and every job must request)")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--endpoints-from", default=str(ROOT / "mobile" / "scripts" / "demo-cabinet.json"))
    parser.add_argument("--out", default="bench-pdf-slots-load.json")
    args = parser.parse_args(argv)
    result = scenario(args)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result.get(k) for k in ("verdict", "failed_checks", "seconds_to_last_run", "peak_concurrent_pdfs_from_intervals", "vm_min_available_mib")}, ensure_ascii=False))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
