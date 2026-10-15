"""Measured evidence for the shared PDF budget on a LOCAL Docker engine: real tenant containers, real kernel locks.

Two arms with the same tenants and the same synthetic data (demo mode, no marketplace calls, internal network):

  unbudgeted   no slot directory: every tenant's resident report worker renders its queued PDF as soon as it sees it
  budgeted     all tenants mount one shared volume at /pdf-slots with WB_SAAS_PDF_SLOTS=<slots>: the same workers must
               take a slot first, so the PDFs queue (durably, in each tenant's database) and render <slots> at a time

One report run is enqueued in every tenant at the same moment (the way "Generate now" does it); the resident
background_worker of each tenant, i.e. the production path, builds it. Measured per arm: start/finish of each run,
concurrency profile (from the run intervals and from sampling the lock files), VM MemAvailable minimum, per-tenant
cgroup memory peak and OOM counters, CPU throttling, wall-clock to the last PDF.

Then, in the budgeted arm, crash recovery: a tenant container is SIGKILLed while it holds a slot and has a running run.
Checked: the slot is released by the kernel at once, another tenant's queued run takes it, and after the killed tenant
restarts its interrupted run is requeued (attempt 2) and completes.

Refuses a non-local engine, touches only ``<prefix>-*`` objects it labelled, makes its containers first in line for the
OOM killer, never raises a limit and removes everything it created.

    python scripts/bench_pdf_budget.py --image bench-tenant:pdfbudget --tenants 6 --slots 2 --out evidence.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_tenant_density as base  # noqa: E402  (its local-engine checks and docker wrapper are the reviewed ones)

LABEL = base.LABEL
ENQUEUE = r'''
import json, sys
sys.path.insert(0, "/app/tenant-app")
from config import DB_PATH
from reports.store import ReportStore
from reports.catalog import METRIC_LABELS
store = ReportStore(DB_PATH)
defs = store.list_definitions()
did = defs[0]["id"] if defs else store.add_definition(name="evidence", metrics=list(METRIC_LABELS), schedule_type="daily", schedule_time="23:59")
print(json.dumps({"run_id": store.enqueue_run(did)}))
'''
STATE = r'''
import json, sqlite3, sys
sys.path.insert(0, "/app/tenant-app")
from config import DB_PATH
with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30) as conn:
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT id, status, started_at, finished_at, attempts, lease_owner, error FROM report_runs ORDER BY id")]
slots = {}
try:
    from reports import pdf_budget
    slots = pdf_budget.status()
except Exception as exc:
    slots = {"error": type(exc).__name__}
print(json.dumps({"runs": rows, "slots": slots}))
'''


def parse_ts(text: str) -> float:
    return time.mktime(time.strptime(text, "%Y-%m-%dT%H:%M:%S"))


def peak_concurrency(intervals: list[tuple[float, float]]) -> int:
    events = sorted([(s, 1) for s, _ in intervals] + [(e, -1) for _, e in intervals], key=lambda x: (x[0], x[1]))  # ends before starts on ties
    depth = peak = 0
    for _, step in events:
        depth += step
        peak = max(peak, depth)
    return peak


class Arm:
    def __init__(self, args, name: str, slots: int | None):
        self.args, self.name, self.slots = args, name, slots
        self.run_id = uuid.uuid4().hex[:8]
        self.network = f"{args.prefix}-{name}-net-{self.run_id}"
        self.slot_volume = f"{args.prefix}-{name}-slots-{self.run_id}"
        self.names = [f"{args.prefix}-{name}-t{i:02d}-{self.run_id}" for i in range(1, args.tenants + 1)]

    def label(self):
        return ["--label", f"{LABEL}={self.run_id}"]

    def start(self) -> None:
        d = base.docker
        d("network", "create", "--internal", *self.label(), self.network)
        if self.slots:
            d("volume", "create", *self.label(), self.slot_volume)
            d("run", "--rm", "-u", "0", "--network", "none", "--cap-drop", "ALL", "--cap-add", "CHOWN", "--cap-add", "FOWNER", "--memory", "64m",
              "-v", f"{self.slot_volume}:/slots", base.HELPER_IMAGE, "sh", "-c", "chown 10001:10001 /slots && chmod 770 /slots")
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(self.create, range(len(self.names))))

    def create(self, i: int) -> None:
        d, name = base.docker, self.names[i]
        volume = f"{name}-data"
        d("volume", "create", *self.label(), volume)
        d("run", "--rm", "-u", "0", "--network", "none", "--cap-drop", "ALL", "--cap-add", "CHOWN", "--memory", "64m", "-v", f"{volume}:/data",
          base.HELPER_IMAGE, "chown", "10001:10001", "/data")
        d("run", "--rm", "--network", "none", "-u", "10001:10001", "--read-only", "--cap-drop", "ALL", "--memory", "1536m", "--oom-score-adj", "1000",
          "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "-v", f"{volume}:/app/data", *self.label(), *base.isolated_env_args(), "-e", "WB_SAAS_DATA_DIR=/app/data",
          "--entrypoint", "python3", "-w", "/app/tenant-app", self.args.image, "-c", "import seed_demo_data; seed_demo_data.main()", timeout=600)
        tmpfs = []
        for path, size in (("/tmp", "64m"), ("/var/lib/nginx", "64m"), ("/var/cache/nginx", "16m"), ("/var/log/nginx", "16m"), ("/run", "16m")):
            tmpfs += ["--tmpfs", f"{path}:rw,noexec,nosuid,size={size},uid=10001,gid=10001,mode=0777"]
        slot_args = ["-v", f"{self.slot_volume}:/pdf-slots", "-e", "WB_SAAS_PDF_SLOT_DIR=/pdf-slots", "-e", f"WB_SAAS_PDF_SLOTS={self.slots}"] if self.slots else []
        d("run", "-d", "--name", name, "--network", self.network, "--network-alias", name, *self.label(),
          "--label", f"wb-saas.slug={name}",  # the label the real admission code counts running tenants by
          "-u", "10001:10001", "--read-only",
          "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--memory", "1536m", "--memory-reservation", "384m", "--cpus", "1.0",
          "--oom-score-adj", "1000", "--pids-limit", "256", "-v", f"{volume}:/app/data", *tmpfs, *slot_args, *base.isolated_env_args(),
          "-e", f"MH_ALLOWED_HOSTS=localhost,127.0.0.1,{name}", "-e", "MPLCONFIGDIR=/tmp/matplotlib", "-e", "HOME=/app", self.args.image)

    def wait_healthy(self, names=None, timeout=300) -> None:
        deadline, pending = time.time() + timeout, set(names or self.names)
        while pending and time.time() < deadline:
            for name in list(pending):
                if base.docker("inspect", "--format", "{{.State.Health.Status}}", name, check=False) == "healthy":
                    pending.discard(name)
            time.sleep(3)
        if pending:
            raise RuntimeError(f"not healthy: {sorted(pending)}")

    def state(self, name: str) -> dict:
        return json.loads(base.docker("exec", name, "python3", "-c", STATE, check=False).splitlines()[-1])

    def enqueue_all(self) -> None:
        with ThreadPoolExecutor(max_workers=len(self.names)) as pool:
            list(pool.map(lambda n: base.docker("exec", n, "python3", "-c", ENQUEUE), self.names))

    def finished(self) -> bool:
        states = [self.state(n)["runs"] for n in self.names]
        return all(runs and all(r["status"] in ("ok", "error") for r in runs) for runs in states)

    def stop(self) -> None:
        ids = base.docker("ps", "-aq", "--filter", f"label={LABEL}={self.run_id}", check=False).split()
        if ids:
            base.docker("rm", "-f", *ids, check=False, timeout=600)
        for v in base.docker("volume", "ls", "-q", "--filter", f"label={LABEL}={self.run_id}", check=False).split():
            base.docker("volume", "rm", "-f", v, check=False)
        base.docker("network", "rm", self.network, check=False)


def vm_available(via: str) -> int:
    return base.vm_available_mib(via)


def run_arm(args, name: str, slots: int | None, crash: bool) -> dict:
    arm = Arm(args, name, slots)
    result: dict = {"arm": name, "slots": slots, "tenants": args.tenants}
    try:
        arm.start()
        arm.wait_healthy()
        time.sleep(args.settle)
        via = arm.names[0]
        idle = {n: base.probe(n) for n in arm.names}
        result["idle_vm_available_mib"] = vm_available(via)
        samples: list[tuple[float, int, int | None]] = []
        stop = threading.Event()

        def sample():
            while not stop.is_set():
                try:
                    busy = arm.state(via)["slots"].get("host_busy") if slots else None
                    samples.append((time.time(), vm_available(via), busy))
                except Exception:
                    pass
                stop.wait(1.0)
        watcher = threading.Thread(target=sample, daemon=True)
        watcher.start()
        started = time.time()
        arm.enqueue_all()
        deadline = time.time() + args.timeout
        while time.time() < deadline and not arm.finished():
            time.sleep(2)
        wall = time.time() - started
        stop.set()
        watcher.join(timeout=5)
        runs = {n: arm.state(n)["runs"] for n in arm.names}
        intervals = [(parse_ts(r["started_at"]), parse_ts(r["finished_at"])) for rs in runs.values() for r in rs if r["status"] == "ok" and r["finished_at"]]
        after = {n: base.probe(n) for n in arm.names}
        result.update(
            wall_seconds=round(wall, 1), runs_ok=sum(r["status"] == "ok" for rs in runs.values() for r in rs),
            runs_error=sum(r["status"] == "error" for rs in runs.values() for r in rs), attempts_max=max(r["attempts"] for rs in runs.values() for r in rs),
            peak_concurrent_pdfs_from_run_intervals=peak_concurrency(intervals),
            peak_busy_host_slots_sampled=max((s[2] or 0 for s in samples), default=None) if slots else None,
            vm_min_available_mib=min((s[1] for s in samples), default=None),
            vm_drop_mib=result["idle_vm_available_mib"] - min((s[1] for s in samples), default=result["idle_vm_available_mib"]),
            per_run_seconds_p50=round(statistics.median(e - s for s, e in intervals), 1) if intervals else None,
            container_peak_mib_max=max(base.mib(v.get("peak", 0)) for v in after.values()),
            container_peak_mib_sum=round(sum(base.mib(v.get("peak", 0)) for v in after.values()), 1),
            memory_events={k: sum(v.get(f"ev_{k}", 0) for v in after.values()) - sum(v.get(f"ev_{k}", 0) for v in idle.values()) for k in ("high", "max", "oom", "oom_kill")},
            cpu_throttled_pct=round(100 * (sum(v.get("cpu_nr_throttled", 0) for v in after.values()) - sum(v.get("cpu_nr_throttled", 0) for v in idle.values()))
                                    / max(1, sum(v.get("cpu_nr_periods", 0) for v in after.values()) - sum(v.get("cpu_nr_periods", 0) for v in idle.values())), 1),
            oom_killed_containers=sum(base.docker("inspect", "--format", "{{.State.OOMKilled}}", n, check=False) == "true" for n in arm.names),
            run_intervals=sorted((n, r["started_at"], r["finished_at"], r["status"], r["attempts"]) for n, rs in runs.items() for r in rs),
        )
        if crash:
            result["crash_recovery"] = crash_recovery(arm, args)
    finally:
        arm.stop()
    return result


def crash_recovery(arm: Arm, args) -> dict:
    """SIGKILL a tenant that holds a slot while its run is 'running'; the kernel must release the slot, the queue must move on,
    and after the restart the interrupted run must be requeued (attempt 2) and finish."""
    out: dict = {}
    arm.enqueue_all()  # second wave: every tenant has a new queued run
    victim = None
    deadline = time.time() + 120
    while time.time() < deadline and victim is None:
        for name in arm.names:
            runs = arm.state(name)["runs"]
            if runs and runs[-1]["status"] == "running":
                victim = name
                break
        time.sleep(0.5)
    if victim is None:
        return {"error": "no run ever reached running"}
    holder_run = arm.state(victim)["runs"][-1]
    busy_before = arm.state(next(n for n in arm.names if n != victim))["slots"]["host_busy"]
    base.docker("kill", victim)
    killed = time.time()
    out.update(victim=victim.rsplit("-", 2)[-2], victim_run_attempts_before_kill=holder_run["attempts"], host_busy_before_kill=busy_before)
    other = next(n for n in arm.names if n != victim)
    freed_after = None
    while time.time() - killed < 30:
        if arm.state(other)["slots"]["host_busy"] < busy_before:
            freed_after = round(time.time() - killed, 1)
            break
        time.sleep(0.25)
    out["slot_freed_seconds_after_kill"] = freed_after
    base.docker("start", victim)
    arm.wait_healthy([victim])
    deadline = time.time() + args.timeout
    while time.time() < deadline and not arm.finished():
        time.sleep(2)
    final = arm.state(victim)["runs"][-1]
    others = [arm.state(n)["runs"][-1]["status"] for n in arm.names if n != victim]
    out.update(victim_run_after_restart={k: final[k] for k in ("status", "attempts", "error")}, other_tenants_last_run_status=others,
               every_run_finished_ok=final["status"] == "ok" and all(s == "ok" for s in others))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", required=True)
    parser.add_argument("--tenants", type=int, default=6)
    parser.add_argument("--slots", type=int, default=2)
    parser.add_argument("--settle", type=int, default=30)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--prefix", default="bench")
    parser.add_argument("--out", default="bench-pdf-budget.json")
    args = parser.parse_args()
    base.assert_local_engine()
    report = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "image": args.image, "arms": []}
    for name, slots, crash in (("unbudgeted", None, False), ("budgeted", args.slots, True)):
        report["arms"].append(run_arm(args, name, slots, crash))
        print(json.dumps({k: report["arms"][-1].get(k) for k in ("arm", "wall_seconds", "peak_concurrent_pdfs_from_run_intervals", "vm_min_available_mib", "runs_ok", "runs_error")}), flush=True)
    report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
