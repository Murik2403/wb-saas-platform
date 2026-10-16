"""Fault scenarios for the shared PDF slots and durable report-run leases, on real LOCAL tenant containers (2-4, demo data).

Every scenario uses the real resident ``background_worker`` of each tenant, one shared slot volume (WB_SAAS_PDF_SLOTS=2) and real
PDF builds; faults are real (``docker kill`` = SIGKILL) or, where a real wait would take the 300 s lease, an explicit and recorded
injection into the tenant's own demo database.

  S1 sigkill   with every slot busy and a tenant POSITIVELY OBSERVED waiting for one (otherwise nothing is killed and the act fails),
               the tenant that holds a slot is SIGKILLed mid-build; the waiting tenant is then observed taking over: the kernel frees the slot at once and the queued tenant takes it;
               after the restart the dead owner's lease is expired (injected: the real lease lasts 300 s) and the worker requeues the run
               (attempt 2) and finishes it; the other tenants' PDFs are untouched; a duplicate enqueue returns the same run
  S2 lease     a run whose owner stopped renewing is requeued by the live worker WITHOUT a restart and finished (attempt 2); the stale
               owner is fenced: its late finish/heartbeat are rejected and change nothing, while the new attempt runs and after it ended
  S3 poison    a job whose worker dies on every attempt is SIGKILLed three times; after the third interruption it is closed with the
               "interrupted 3 times" error and never requeued again; another tenant has a build RUNNING at each of the three kills and
               completes all of them
  S4 cancel    there is no cancel API for a run; the cancel equivalent is deleting the definition. A queued run vanishes and never
               builds; a running build survives the deletion, the worker keeps living, nothing is resurrected, the slot is released and
               the next job of that tenant completes

The act is ``PASS`` only if every named check of every scenario holds, the real admission gates every tenant before its creation, and
memory and OOM observations are complete (any unreadable sample or OOM flag is a failure) and the cleanup of everything the run created is confirmed by the engine (same fail-closed ownership rules as bench_pdf_slots_load.py).
Local engine only; refuses a dirty tree. 20/25/30 are not exercised.

    python scripts/bench_pdf_faults.py --image bench-tenant:fc-xxxx --tenants 3 --out docs/benchmarks/pdf-faults-3.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "provisioner"))
import bench_pdf_budget as pdfb  # noqa: E402
import bench_pdf_slots_load as load  # noqa: E402
import bench_tenant_density as base  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
POISON_TEXT = "прервана 3 раза"
SCENARIO_KEYS = ("S1_sigkill_slot_holder", "S2_lease_expiry_and_fencing", "S3_poison_job", "S4_cancel_by_deleting_the_definition")

OPS = r'''
import json, sqlite3, sys, os
sys.path.insert(0, "/app/tenant-app")
from config import DB_PATH
from reports.store import ReportStore
from reports.catalog import METRIC_LABELS
op, arg = sys.argv[1], json.loads(sys.argv[2])
store = ReportStore(DB_PATH)
def rows():
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        out = [dict(r) for r in conn.execute("SELECT id, definition_id, status, attempts, lease_owner, lease_expires_at, started_at, finished_at, error, file_path FROM report_runs ORDER BY id")]
    for row in out:
        path = row.pop("file_path", None)
        row["file_present"] = bool(path and os.path.isfile(path))
        row["pdf_header_ok"] = False
        if row["file_present"]:
            with open(path, "rb") as handle:
                row["pdf_header_ok"] = handle.read(5) == b"%PDF-"
            row["pdf_bytes"] = os.path.getsize(path)
        row["file_recorded"] = bool(path)
    return out
def run_row(run_id):
    return next((r for r in rows() if r["id"] == run_id), None)
if op == "define":
    print(json.dumps({"definition_id": store.add_definition(name=arg["name"], metrics=list(METRIC_LABELS), schedule_type="daily", schedule_time="23:59")}))
elif op == "enqueue":
    print(json.dumps({"run_id": store.enqueue_run(arg["definition_id"])}))
elif op == "rows":
    print(json.dumps({"rows": rows()}))
elif op == "dead_run":  # a run whose owner (a worker that no longer exists) stopped renewing its lease
    run_id = store.start_run(arg["definition_id"], owner="dead-worker")
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        conn.execute("UPDATE report_runs SET lease_expires_at='2000-01-01T00:00:00' WHERE id=?", (run_id,))
    print(json.dumps({"run_id": run_id}))
elif op == "expire_leases":  # what 300 s of silence from a dead owner would do
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        count = conn.execute("UPDATE report_runs SET lease_expires_at='2000-01-01T00:00:00' WHERE status='running'").rowcount
    print(json.dumps({"expired": count}))
elif op == "stale_owner":  # the dead owner wakes up and tries to report
    before = run_row(arg["run_id"])
    finished = store.finish_run(arg["run_id"], status="ok", file_path="/tmp/stale.pdf", owner=arg["owner"], attempt=arg["attempt"])
    beat = store.heartbeat(arg["run_id"], arg["owner"], attempt=arg["attempt"])
    print(json.dumps({"finish_accepted": finished, "heartbeat_accepted": beat, "before": before, "after": run_row(arg["run_id"])}))
elif op == "delete_definition":
    store.delete_definition(arg["definition_id"])
    print(json.dumps({"deleted": True}))
elif op == "slots":
    from reports import pdf_budget
    print(json.dumps(pdf_budget.status()))
'''


# ---- pure verdict logic (unit-tested) --------------------------------------------------------------------------------------

def check(name: str, ok: bool, detail: str = "") -> dict:
    return {"check": name, "ok": bool(ok), "detail": detail}


SAMPLE_GAP_LIMIT_SECONDS = 10.0


def sample_coverage(samples: list, window: tuple, limit: float = SAMPLE_GAP_LIMIT_SECONDS) -> dict:
    """Samples are (time, MiB). A scenario window is covered only if samples touch both ends within ``limit`` and no gap exceeds it."""
    start, end = window
    inside = sorted(t for t, _ in samples if start - limit <= t <= end + limit)
    points = [start, *inside, end]
    gaps = [b - a for a, b in zip(points, points[1:])]
    return {"count": len([t for t in inside if start <= t <= end]), "max_gap_seconds": round(max(gaps), 1) if gaps else None}


def resource_failures(result: dict) -> list[str]:
    """Memory samples and OOM flags must be complete, typed and error-free for every scenario and every tenant: a failed read is
    UNKNOWN, never a quiet zero."""
    failed = []
    resources = result.get("resource_observation")
    if not isinstance(resources, dict):
        return ["resources: the memory observation was not recorded"]
    if resources.get("errors") or not isinstance(resources.get("errors"), list):
        failed.append(f"resources: {len(resources.get('errors') or [])} memory reads failed or the error list is missing (UNKNOWN): {str(resources.get('errors'))[:200]}")
    if type(resources.get("samples")) is not int or resources["samples"] < 1 or type(resources.get("min_mib")) is not int:
        failed.append("resources: no typed memory sample was read")
    windows = resources.get("windows")
    if not isinstance(windows, dict) or set(windows) != set(SCENARIO_KEYS):
        failed.append("resources: memory coverage is not recorded for exactly the four scenarios")
    else:
        for key in SCENARIO_KEYS:
            window = windows[key]
            if not isinstance(window, dict) or type(window.get("count")) is not int or window["count"] < 2 or window.get("max_gap_seconds") is None or window["max_gap_seconds"] > SAMPLE_GAP_LIMIT_SECONDS:
                failed.append(f"resources: {key} is not covered by memory samples (count {window.get('count') if isinstance(window, dict) else None}, largest gap {window.get('max_gap_seconds') if isinstance(window, dict) else None} s)")
    oom = result.get("oom")
    names = result.get("tenant_names")
    if not isinstance(oom, dict) or not isinstance(names, list) or not names or oom.get("errors") != []:
        failed.append(f"oom: the OOM flags were not read cleanly (UNKNOWN): {str(oom.get('errors') if isinstance(oom, dict) else oom)[:200]}")
        return failed
    sections = {"final": oom.get("final"), **{f"after {key}": (oom.get("per_scenario") or {}).get(key) for key in SCENARIO_KEYS}}
    for label, flags in sections.items():
        if not isinstance(flags, dict) or set(flags) != set(names) or any(type(value) is not bool for value in flags.values()):
            failed.append(f"oom: {label} does not hold a typed OOM flag for every tenant")
        elif any(flags.values()):
            failed.append(f"kernel memory: {label}: OOM-killed {[n[-12:] for n, v in flags.items() if v]}")
    return failed


def failed_checks(result: dict) -> list[str]:
    """PASS needs every check of every scenario, a complete set of scenarios, an admitted gate per tenant and a confirmed cleanup."""
    failed = []
    scenarios = result.get("scenarios")
    expected = SCENARIO_KEYS
    if not isinstance(scenarios, dict) or set(scenarios) != set(expected):
        failed.append(f"scenarios: expected exactly {list(expected)}, got {sorted(scenarios) if isinstance(scenarios, dict) else scenarios}")
        scenarios = scenarios if isinstance(scenarios, dict) else {}
    for name in expected:
        entry = scenarios.get(name)
        if not isinstance(entry, dict) or not entry.get("checks"):
            failed.append(f"{name}: no checks recorded")
            continue
        if entry.get("error"):
            failed.append(f"{name}: the scenario itself failed: {entry['error']}")
        failed += [f"{name}: {c['check']} ({c['detail']})" for c in entry["checks"] if not c.get("ok")]
    gate = result.get("start_gate")
    tenants = result.get("tenants")
    if (not isinstance(gate, list) or len(gate) != tenants or any(not isinstance(g, dict) or g.get("decision") != "admitted" or type(g.get("at")) is not float
            or type(g.get("create_started_at")) is not float or not g["at"] < g["create_started_at"] for g in gate)):
        failed.append("admission: every tenant must have been admitted by the real budget and memory floor BEFORE its first object was created")
    if type(result.get("vm_min_available_mib")) is not int or result["vm_min_available_mib"] < result.get("floor_mib", 1500):
        failed.append(f"stand: VM MemAvailable fell to {result.get('vm_min_available_mib')} MiB (stand limit reached or not measured)")
    failed += resource_failures(result)
    failed += load.cleanup_failures(result.get("cleanup"))
    return failed


# ---- harness -------------------------------------------------------------------------------------------------------------

def op(tenant: str, operation: str, **arg):
    out = base.docker("exec", tenant, "python3", "-c", OPS, operation, json.dumps(arg), timeout=120).splitlines()
    return json.loads(out[-1])


def wait_until(predicate, timeout: float, interval: float = 1.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def run_by_id(name: str, run_id: int):
    return next((r for r in op(name, "rows")["rows"] if r["id"] == run_id), None)


def host_busy(name: str) -> int:
    return op(name, "slots").get("host_busy", -1)


def wait_healthy(arm: pdfb.Arm, name: str, timeout: int = 120) -> bool:
    return bool(wait_until(lambda: base.docker("inspect", "--format", "{{.State.Health.Status}}", name, check=False) == "healthy", timeout, 2))


def observe_oom(names: list[str]) -> tuple[dict, list[str]]:
    """{container: OOMKilled} read with its exit status; an unavailable, empty or non-boolean answer is an error, never False."""
    flags: dict = {}
    errors: list[str] = []
    for name in names:
        code, out, err = base.docker_status("inspect", "--format", "{{.State.OOMKilled}}", name)
        if code == 0 and out in ("true", "false"):
            flags[name] = out == "true"
        else:
            errors.append(f"{name[-12:]}: status {code}, answer {out[:40]!r} {err[:80]}")
    return flags, errors


def new_job(name: str, label: str) -> dict:
    definition = op(name, "define", name=label)["definition_id"]
    first = op(name, "enqueue", definition_id=definition)["run_id"]
    return {"definition_id": definition, "run_id": first, "second_enqueue_run_id": op(name, "enqueue", definition_id=definition)["run_id"]}


def peak_overlap(rows: list[dict]) -> int:
    intervals = [(pdfb.parse_ts(r["started_at"]), pdfb.parse_ts(r["finished_at"])) for r in rows if r["status"] == "ok" and r["finished_at"] and r["started_at"]]
    return pdfb.peak_concurrency(intervals)


def s1_sigkill(arm: pdfb.Arm, args) -> dict:
    names = arm.names
    checks, notes = [], {}
    jobs = {n: new_job(n, "faults-s1") for n in names}
    checks.append(check("a duplicate enqueue returns the same run in every tenant", all(j["run_id"] == j["second_enqueue_run_id"] for j in jobs.values()), str({n[-12:]: j for n, j in jobs.items()})))

    def status_of(tenant):
        return (run_by_id(tenant, jobs[tenant]["run_id"]) or {}).get("status")

    def contended():
        """All slots busy AND at least one tenant waiting: only then is a kill a test of the hand-over."""
        states = {n: status_of(n) for n in names}
        running = [n for n in names if states[n] == "running"]
        waiting = [n for n in names if states[n] == "queued"]
        if len(running) >= args.slots and waiting:
            busy = host_busy(next(n for n in names if n != running[0]))
            if busy == args.slots:
                return {"victim": running[0], "contenders": waiting, "busy": busy}
        return None
    ready = wait_until(contended, 90, 0.5)
    if not ready:
        return {"checks": checks + [check("every slot busy and a tenant observed waiting for one before the kill", False, "never observed: nothing was killed")], "notes": notes}
    victim = ready["victim"]
    others = [n for n in names if n != victim]
    still_waiting = [n for n in others if status_of(n) == "queued"]  # re-read at the kill instant
    checks.append(check("every slot busy and a tenant observed waiting for one at the moment of the kill", bool(still_waiting) and ready["busy"] == args.slots,
                        f"busy {ready['busy']}, waiting {[n[-12:] for n in still_waiting]}"))
    if not still_waiting:
        return {"checks": checks, "notes": notes}
    killed_at = time.time()
    base.docker("kill", victim)
    exit_code = base.docker("inspect", "--format", "{{.State.ExitCode}}", victim, check=False)
    took_over = wait_until(lambda: next((n for n in still_waiting if status_of(n) in ("running", "ok")), None), 60, 0.25)
    seconds_to_takeover = round(time.time() - killed_at, 1)
    notes.update(victim=victim[-12:], slots_busy_before_kill=ready["busy"], waiting_before_kill=[n[-12:] for n in still_waiting], exit_code=exit_code,
                 waiting_tenant_seen_running_after_kill=took_over and took_over[-12:], seconds_until_waiting_tenant_running=seconds_to_takeover)
    checks += [check("the container died by SIGKILL (exit 137)", exit_code == "137", f"exit code {exit_code}"),
               check("the tenant that was waiting took the freed slot within 15 s (observed queued before, running after)", bool(took_over) and seconds_to_takeover <= 15, f"{seconds_to_takeover} s")]
    base.docker("start", victim)
    healthy = wait_healthy(arm, victim)
    checks.append(check("the killed tenant became healthy again", healthy, ""))
    injected = op(victim, "expire_leases") if healthy else {"expired": 0}
    checks.append(check("the dead owner's lease was expired by injection (the real lease lasts 300 s) on exactly its running run", injected["expired"] == 1, str(injected)))
    done = wait_until(lambda: all((run_by_id(n, jobs[n]["run_id"]) or {}).get("status") in ("ok", "error") for n in names), args.fault_timeout, 2)
    final = {n: run_by_id(n, jobs[n]["run_id"]) for n in names}
    checks += [check("every tenant's run finished", bool(done), str({n[-12:]: (r or {}).get("status") for n, r in final.items()})),
               check("every run finished ok with a real PDF", all(r and r["status"] == "ok" and r["pdf_header_ok"] and r.get("pdf_bytes", 0) >= load.MIN_REAL_PDF_BYTES for r in final.values()), ""),
               check("the killed tenant's run was requeued and finished on attempt 2", bool(final[victim]) and final[victim]["attempts"] == 2, f"attempts {final[victim] and final[victim]['attempts']}"),
               check("the other tenants' runs were not repeated (attempt 1)", all(final[n] and final[n]["attempts"] == 1 for n in others), ""),
               check("at most 2 builds overlapped in time", peak_overlap([r for r in final.values() if r]) <= args.slots, f"peak {peak_overlap([r for r in final.values() if r])}")]
    notes["final"] = {n[-12:]: {k: r[k] for k in ("status", "attempts", "started_at", "finished_at")} for n, r in final.items() if r}
    return {"checks": checks, "notes": notes}


def s2_lease(arm: pdfb.Arm, args) -> dict:
    name = arm.names[1]
    checks, notes = [], {}
    definition = op(name, "define", name="faults-s2")["definition_id"]
    run_id = op(name, "dead_run", definition_id=definition)["run_id"]
    taken = wait_until(lambda: (lambda r: r if r and r["status"] == "running" and r["attempts"] == 2 else None)(run_by_id(name, run_id)), 90, 1)
    checks.append(check("the live worker took the abandoned run over without a restart (attempt 2)", bool(taken), str(taken and {k: taken[k] for k in ("status", "attempts", "lease_owner")})))
    if taken:
        stale = op(name, "stale_owner", run_id=run_id, owner="dead-worker", attempt=1)
        notes["stale_owner_while_running"] = {k: stale[k] for k in ("finish_accepted", "heartbeat_accepted")}
        checks += [check("the dead owner's late finish is rejected", stale["finish_accepted"] is False, str(stale["finish_accepted"])),
                   check("the dead owner's late heartbeat is rejected", stale["heartbeat_accepted"] is False, str(stale["heartbeat_accepted"])),
                   check("the run is unchanged by the stale owner (still running, not the dead owner's lease, no file)",
                         stale["after"] and stale["after"]["status"] == "running" and stale["after"]["lease_owner"] != "dead-worker" and not stale["after"]["file_recorded"], str(stale["after"]))]
    final = wait_until(lambda: (lambda r: r if r and r["status"] in ("ok", "error") else None)(run_by_id(name, run_id)), args.fault_timeout, 2)
    checks.append(check("the run finished ok on attempt 2 with a real PDF", bool(final) and final["status"] == "ok" and final["attempts"] == 2 and final["pdf_header_ok"], str(final and {k: final[k] for k in ("status", "attempts", "pdf_bytes")})))
    if final:
        late = op(name, "stale_owner", run_id=run_id, owner="dead-worker", attempt=1)
        checks += [check("after completion a stale finish/heartbeat still changes nothing", late["finish_accepted"] is False and late["heartbeat_accepted"] is False
                         and late["after"]["status"] == "ok" and late["after"]["file_present"], str(late["after"]))]
    return {"checks": checks, "notes": notes}


def s3_poison(arm: pdfb.Arm, args) -> dict:
    """Each cycle first starts a build in ANOTHER tenant and waits until it is running; only then is the poison run (re)claimed and killed,
    so the kill always lands while the neighbour is building and before the poison build can finish by itself."""
    name, other = arm.names[2], arm.names[0]
    checks, notes = [], {"cycles": []}
    definition = op(name, "define", name="faults-s3")["definition_id"]
    run_id = None
    neighbours = []
    for cycle in (1, 2, 3):
        job = new_job(other, f"faults-s3-neighbour-{cycle}")
        neighbours.append(job)
        neighbour_running = wait_until(lambda: (run_by_id(other, job["run_id"]) or {}).get("status") == "running", 90, 0.5)
        if cycle == 1:
            run_id = op(name, "enqueue", definition_id=definition)["run_id"]  # the poison run enters only after the neighbour is building
            injected = 1
        else:
            injected = op(name, "expire_leases")["expired"]  # the previous kill's dead lease ends now: the worker requeues and re-claims
        claimed = wait_until(lambda: (lambda r: r if r and r["status"] == "running" and r["attempts"] == cycle else None)(run_by_id(name, run_id)), 90, 0.5)
        if not claimed:
            checks.append(check(f"attempt {cycle} was claimed by the worker", False, str(run_by_id(name, run_id))))
            break
        at_kill = (run_by_id(other, job["run_id"]) or {}).get("status")
        base.docker("kill", name)
        base.docker("start", name)
        healthy = wait_healthy(arm, name)
        notes["cycles"].append({"attempt": cycle, "killed": True, "healthy_again": healthy, "lease_expired_by_injection_before_this_attempt": injected, "neighbour_status_at_kill": at_kill})
        checks += [check(f"attempt {cycle}: claimed after the other tenant was building, SIGKILL, restart", healthy and injected == 1, str(notes["cycles"][-1])),
                   check(f"attempt {cycle}: the other tenant had a build RUNNING at the moment of the kill", bool(neighbour_running) and at_kill == "running", f"status at kill {at_kill}")]
    last = op(name, "expire_leases")["expired"]  # the third dead lease ends: recovery must close the run instead of requeueing it
    checks.append(check("the third dead lease was expired by injection on exactly the poison run", last == 1, str(last)))
    final = wait_until(lambda: (lambda r: r if r and r["status"] == "error" else None)(run_by_id(name, run_id)), 60, 1)
    checks += [check("after the third interruption the run is closed with an error", bool(final) and final["attempts"] == 3, str(final and {k: final[k] for k in ("status", "attempts")})),
               check("the error says it was interrupted 3 times", bool(final) and POISON_TEXT in (final.get("error") or ""), str(final and final.get("error"))),
               check("no PDF was recorded for the poisoned run", bool(final) and not final["file_recorded"], "")]
    settled = []
    for _ in range(6):  # the poisoned run must stay closed and never be requeued while the worker keeps ticking
        time.sleep(5)
        row = run_by_id(name, run_id)
        settled.append(row and (row["status"], row["attempts"]))
    checks.append(check("the poisoned run stays closed (not requeued) while the worker keeps ticking", all(s == ("error", 3) for s in settled), str(settled)))
    finals = [wait_until(lambda j=j: (lambda r: r if r and r["status"] in ("ok", "error") else None)(run_by_id(other, j["run_id"])), args.fault_timeout, 2) for j in neighbours]
    checks.append(check("every build of the other tenant, running through the kill cycles, completed ok on attempt 1 with a real PDF",
                        len(finals) == 3 and all(f and f["status"] == "ok" and f["attempts"] == 1 and f["pdf_header_ok"] for f in finals), str([f and (f["status"], f["attempts"]) for f in finals])))
    return {"checks": checks, "notes": notes}


def s4_cancel(arm: pdfb.Arm, args) -> dict:
    name, helper = arm.names[1], arm.names[0]
    checks, notes = [], {}
    first = new_job(name, "faults-s4-first")
    queued = new_job(name, "faults-s4-queued")  # waits behind the first: the worker takes one run at a time
    queued_state = (run_by_id(name, queued["run_id"]) or {}).get("status")
    op(name, "delete_definition", definition_id=queued["definition_id"])
    checks.append(check("the second run was still queued when its definition was deleted", queued_state == "queued", str(queued_state)))
    done = wait_until(lambda: (lambda r: r if r and r["status"] in ("ok", "error") else None)(run_by_id(name, first["run_id"])), args.fault_timeout, 2)
    rows = op(name, "rows")["rows"]
    checks += [check("the first job finished ok", bool(done) and done["status"] == "ok", str(done and done["status"])),
               check("the deleted queued run never built and left no row", all(r["id"] != queued["run_id"] for r in rows), str([r["id"] for r in rows]))]
    doomed = new_job(name, "faults-s4-running")
    running = wait_until(lambda: (lambda r: r if r and r["status"] == "running" else None)(run_by_id(name, doomed["run_id"])), 60, 0.5)
    started_at = base.docker("inspect", "--format", "{{.State.StartedAt}}", name, check=False)
    op(name, "delete_definition", definition_id=doomed["definition_id"])
    gone = run_by_id(name, doomed["run_id"]) is None
    checks += [check("the build was running when its definition was deleted", bool(running), ""), check("the run row disappeared with its definition", gone, "")]
    time.sleep(args.pdf_seconds)  # let the in-flight build end: it must not recreate or corrupt anything
    resurrected = run_by_id(name, doomed["run_id"]) is not None
    alive = base.docker("inspect", "--format", "{{.State.Running}} {{.State.StartedAt}}", name, check=False) == f"true {started_at}"
    slots_after = host_busy(helper)
    checks += [check("nothing was resurrected after the in-flight build ended", not resurrected, ""),
               check("the container and its worker survived (same start time)", alive, ""),
               check("the slot was released", slots_after == 0, f"host_busy {slots_after}")]
    follow = new_job(name, "faults-s4-after")
    follow_final = wait_until(lambda: (lambda r: r if r and r["status"] in ("ok", "error") else None)(run_by_id(name, follow["run_id"])), args.fault_timeout, 2)
    checks.append(check("the tenant's next job completes ok on attempt 1", bool(follow_final) and follow_final["status"] == "ok" and follow_final["attempts"] == 1 and follow_final["pdf_header_ok"], str(follow_final and follow_final["status"])))
    notes["note"] = "there is no cancel API for a report run; deleting the definition is the cancel equivalent"
    return {"checks": checks, "notes": notes}


def run_scenarios(arm: pdfb.Arm, args) -> dict:
    out = {}
    oom_after: dict = {}
    oom_errors: list[str] = []
    for key, fn in (("S1_sigkill_slot_holder", s1_sigkill), ("S2_lease_expiry_and_fencing", s2_lease), ("S3_poison_job", s3_poison), ("S4_cancel_by_deleting_the_definition", s4_cancel)):
        started = time.time()
        try:
            out[key] = fn(arm, args)
        except Exception as error:  # noqa: BLE001 - a scenario fault is a failed scenario with its reason
            out[key] = {"checks": [check("scenario completed", False, "")], "error": f"{type(error).__name__}: {str(error)[:300]}"}
        out[key]["seconds"] = round(time.time() - started, 1)
        out[key]["window"] = [started, time.time()]
        flags, errors = observe_oom(arm.names)
        oom_after[key] = flags
        oom_errors += [f"after {key}: {e}" for e in errors]
    out["_oom_after_each_scenario"] = {"per_scenario": oom_after, "errors": oom_errors}
    return out


def scenario(args) -> dict:
    git = load.git_state()
    if git["dirty"] and not args.allow_dirty:
        raise SystemExit("Refusing to run from a dirty working tree: commit first so the act names the commit it ran from (or pass --allow-dirty).")
    result: dict = {"scenario": "PDF slot and lease faults on real tenants", "git": git, "tenants": args.tenants, "slots": args.slots, "image": args.image,
                    "floor_mib": args.min_vm_available_mib, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    base.assert_local_engine()
    arm = pdfb.Arm(args, "faults", args.slots)
    gates: list[dict] = []
    samples: list[tuple[float, int]] = []
    sample_errors: list[dict] = []
    stop = threading.Event()
    watcher = None
    try:
        import docker as sdk
        from tenant_budget import TenantBudget
        try:
            arm.start(gate=load.make_gate(args, TenantBudget(), sdk.DockerClient(base_url=base._LOCAL_ENDPOINT), gates))
        except pdfb.StartRefused as refusal:
            for entry in gates:
                entry["create_started_at"] = arm.create_started.get(entry["name"])
            result.update(verdict="REFUSED_NOT_RUN", start_gate=gates, failed_checks=[f"admission: refused tenant {refusal.index + 1} of {args.tenants} before it was created ({refusal.info.get('reason')})"])
            raise load._Stop
        for entry in gates:
            entry["create_started_at"] = arm.create_started.get(entry["name"])
        result["start_gate"] = gates
        arm.wait_healthy()
        time.sleep(args.settle)
        image, trees, hashes, blobs, _codes = load.collect_image(arm, args)
        result["image_attestation"] = load.image_failures(image, trees, hashes, blobs, arm.names)
        result["image"] = {"reference": args.image, "id": image["id"], "source_trees": trees}

        def sample():
            while not stop.is_set():
                try:
                    samples.append((time.time(), load.vm_available_helper()))  # not through a tenant: S1 and S3 kill tenants
                except Exception as error:  # noqa: BLE001 - a failed read is an observation error, never a missing zero
                    sample_errors.append({"at": time.time(), "error": f"{type(error).__name__}: {str(error)[:100]}"})
                stop.wait(args.sample_interval)
        watcher = threading.Thread(target=sample, daemon=True)
        watcher.start()
        scenarios = run_scenarios(arm, args)
        oom = scenarios.pop("_oom_after_each_scenario")
        stop.set()
        watcher.join(timeout=5)
        if watcher.is_alive():
            sample_errors.append({"at": time.time(), "error": "the sampler did not stop"})
        final_flags, final_errors = observe_oom(arm.names)
        windows = {key: {**sample_coverage(samples, tuple(entry["window"])), "window": entry["window"]} for key, entry in scenarios.items() if isinstance(entry, dict) and "window" in entry}
        result.update(scenarios=scenarios, tenant_names=list(arm.names), vm_min_available_mib=min((m for _, m in samples), default=None),
                      resource_observation={"samples": len(samples), "min_mib": min((m for _, m in samples), default=None), "interval_seconds": args.sample_interval,
                                            "errors": sample_errors, "windows": windows, "sample_times": [round(t, 1) for t, _ in samples], "sample_mib": [m for _, m in samples]},
                      oom={"final": final_flags, "per_scenario": oom["per_scenario"], "errors": oom["errors"] + [f"final: {e}" for e in final_errors]})
    except load._Stop:
        pass
    except Exception as error:  # noqa: BLE001
        result.update(verdict="FAIL", failed_checks=[f"harness: {type(error).__name__}: {str(error)[:300]}"])
    finally:
        stop.set()
        arm.stop()
        result["cleanup"] = arm.cleanup_report()
    if "verdict" not in result:
        problems = failed_checks(result) + [f"image: {line}" for line in result.get("image_attestation", ["image attestation missing"])]
        result["verdict"], result["failed_checks"] = ("PASS" if not problems else "FAIL"), problems
    elif result["verdict"] == "REFUSED_NOT_RUN":
        problems = load.cleanup_failures(result["cleanup"])
        if problems:
            result["verdict"], result["failed_checks"] = "FAIL", result["failed_checks"] + problems
    result["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", required=True)
    parser.add_argument("--tenants", type=int, default=3, choices=(3, 4), help="at least 3: two builders and one queued tenant for S1, a poison tenant for S3")
    parser.add_argument("--slots", type=int, default=2)
    parser.add_argument("--pdf-seconds", type=float, default=45.0, help="how long S4 waits for an in-flight build to end")
    parser.add_argument("--fault-timeout", type=float, default=240.0)
    parser.add_argument("--min-vm-available-mib", type=int, default=1500)
    parser.add_argument("--settle", type=int, default=20)
    parser.add_argument("--sample-interval", type=float, default=2.0, help="pause between memory reads (each read starts a throwaway container)")
    parser.add_argument("--prefix", default="bench")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--out", default="bench-pdf-faults.json")
    args = parser.parse_args(argv)
    result = scenario(args)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result.get(k) for k in ("verdict", "failed_checks")}, ensure_ascii=False))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
