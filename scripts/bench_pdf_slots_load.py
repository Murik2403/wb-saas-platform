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
  admission        the real tenant_budget decision for the next tenant is recorded with its numbers
  cleanup          nothing created by the run remains

A run that would start below the floor is recorded REFUSED_NOT_RUN. The result carries the git HEAD it ran from and refuses
to run from a dirty tree (``--allow-dirty`` marks it).

    python scripts/bench_pdf_slots_load.py --image bench-tenant:pdfload --tenants 4 --slots 2 --out act.json
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import sys
import threading
import time
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
    rows = [dict(r) for r in conn.execute("SELECT id, status, started_at, finished_at, attempts, file_path, error FROM report_runs ORDER BY id")]
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

# Runs until SIGTERM, then prints one JSON line. Per tenant: PARALLEL workers loop over the dashboard endpoints, one probe
# loop for /healthz, and a loop that asks for a PDF built inside the request every PDF_EVERY seconds.
DRIVER = r'''
import json, signal, sys, threading, time, urllib.request, urllib.error
targets, paths, parallel, pdf_every = json.loads(sys.argv[1]), json.loads(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4])
stop, lock, out = threading.Event(), threading.Lock(), {}
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
print(json.dumps(out))
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


def evaluate(result: dict, criteria: dict) -> tuple[str, list[str]]:
    """PASS only when every named check holds; the failed checks are listed, never softened."""
    failed: list[str] = []
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
    if not result.get("admission", {}).get("decision"):
        failed.append("admission: no decision was recorded")
    residual = result.get("cleanup_residual_counts")
    if residual is None or any(residual.values()):
        failed.append(f"cleanup: resources remain or were not verified: {residual}")
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


def verify_cleanup(arm: pdfb.Arm, prefix: str) -> dict:
    counts = {}
    for kind, args in (("container", ("ps", "-aq")), ("volume", ("volume", "ls", "-q")), ("network", ("network", "ls", "-q"))):
        owned = 0
        for reference in base.docker(*args, "--filter", f"label={base.LABEL}={arm.run_id}").split():
            labels = ".Config.Labels" if kind == "container" else ".Labels"
            fields = base.docker(kind, "inspect", "--format", '{{.Name}} {{index ' + labels + ' "' + base.LABEL + '"}}', reference).split()
            if len(fields) == 2 and fields[0].lstrip("/").startswith(prefix + "-") and fields[1] == arm.run_id:
                owned += 1
        counts[kind] = owned
    return counts


def run_drivers(arm: pdfb.Arm, args, paths: list[str]) -> list[str]:
    names = []
    groups = [arm.names[i::args.drivers] for i in range(args.drivers)]
    for k, group in enumerate(groups):
        if not group:
            continue
        name = f"{args.prefix}-load{k}-{arm.run_id}"
        base.docker("run", "-d", "--name", name, "--network", arm.network, *arm.label(), "-u", "10001:10001", "--cap-drop", "ALL", "--read-only",
                    "--memory", "256m", "--oom-score-adj", "1000", "--entrypoint", "python3", args.image, "-c", DRIVER, json.dumps(group), json.dumps(paths),
                    str(args.api_parallel), str(args.api_pdf_every))
        names.append(name)
    return names


def collect_drivers(names: list[str]) -> dict:
    merged: dict = {}
    for name in names:
        base.docker("kill", "-s", "TERM", name, check=False)
    for name in names:
        base.docker("wait", name, check=False, timeout=240)
        text = base.docker("logs", name, check=False).strip().splitlines()
        merged.update(json.loads(text[-1]) if text else {})
    return merged


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
    try:
        arm.start()
        arm.wait_healthy()
        time.sleep(args.settle)
        via = arm.names[0]
        idle = {n: base.probe(n) for n in arm.names}
        result["idle_vm_available_mib"] = vm_available(via)
        result["admission"] = admission_decision(arm)
        per_tenant = statistics.mean(base.mib(v.get("cur", 0)) for v in idle.values())
        need = args.slots * args.pdf_peak_delta_mib + args.tenants * args.api_delta_mib
        if result["idle_vm_available_mib"] - need < args.min_vm_available_mib:
            result.update(verdict="REFUSED_NOT_RUN", failed_checks=[f"stand: starting the load would leave {result['idle_vm_available_mib'] - need} MiB, below the "
                                                                    f"{args.min_vm_available_mib} MiB floor (per tenant idle {per_tenant:.0f} MiB)"])
            return result
        paths = pdfb_paths(args)
        samples: list[tuple[int, int | None]] = []
        stop = threading.Event()

        def sample():
            while not stop.is_set():
                try:
                    samples.append((vm_available(via), arm.state(via)["slots"].get("host_busy")))
                except Exception:
                    pass
                stop.wait(1.0)
        watcher = threading.Thread(target=sample, daemon=True)
        watcher.start()
        t0 = time.time()
        drivers = run_drivers(arm, args, paths)
        arm.enqueue_all()
        deadline_at = t0 + deadline
        while time.time() < deadline_at + args.grace_seconds and not finished(arm):
            time.sleep(2)
        seconds_to_last = None
        runs = {n: state_runs(arm, n) for n in arm.names}
        if all(r and r[-1]["status"] in ("ok", "error") for r in runs.values()):
            seconds_to_last = round(max(pdfb.parse_ts(r[-1]["finished_at"]) for r in runs.values() if r[-1]["finished_at"]) - t0, 1)
        stop.set()
        watcher.join(timeout=5)
        buckets = collect_drivers(drivers)
        drivers = []
        after = {n: base.probe(n) for n in arm.names}
        flat = [r[-1] for r in runs.values()]
        intervals = [(pdfb.parse_ts(r["started_at"]), pdfb.parse_ts(r["finished_at"])) for r in flat if r["status"] == "ok" and r["finished_at"]]
        api, health = summarize_api(buckets)
        result.update(
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
    finally:
        for name in drivers:
            base.docker("rm", "-f", name, check=False)
        arm.stop()
        result["cleanup_residual_counts"] = verify_cleanup(arm, args.prefix)
    if "verdict" not in result:
        result["verdict"], result["failed_checks"] = evaluate(result, criteria)
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
