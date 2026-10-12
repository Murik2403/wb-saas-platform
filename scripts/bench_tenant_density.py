"""Isolated tenant-density acceptance: N real tenant containers on a LOCAL Docker engine, synthetic data, no production.

What it does
  * starts N tenant containers from one image with the provisioner's hardening (read-only root, cap_drop ALL, uid 10001,
    hard mem limit, soft reservation, 1 CPU) on a private internal network, each with its own volume seeded by the
    project's fictional demo seeder (demo mode: read-only API, no marketplace calls, no credentials);
  * at each step asks the REAL admission code (provisioner/tenant_budget.py) whether one more tenant would be admitted,
    against the real engine state, and records the decision (admitted / refused + reason + numbers);
  * runs four measured phases with ALL tenants at the same time (no waves):
      calc_serial    one in-flight dashboard request per tenant (cross-tenant contention only)
      calc_parallel  PARALLEL in-flight requests per tenant (the per-tenant work guard queues them)
      background     one pass of the three background jobs (reports, sync, agents) in every tenant
      combined       calc_parallel and background together (the worst moment)
    and records per phase: latency p50/p95/max, 5xx and no-answer failures, /healthz latency while loaded, per-tenant
    memory (cgroup current / peak / anon), memory.events deltas (high, max, oom, oom_kill), CPU (usage, throttling),
    container OOM flags and restarts, and the VM's MemAvailable minimum;
  * never grows past --min-vm-available-mib: the step is recorded as REFUSED with the numbers, not run.

Safety: refuses a non-local engine, touches only objects named ``<prefix>-*`` that carry the label
``wb-saas.benchmark=<run id>``, makes its containers the first choice of the kernel OOM killer (oom_score_adj 1000),
never raises any limit, and removes everything it created when it ends.

    python scripts/bench_tenant_density.py --image bench-tenant:overlay --steps 20,25,30 --probe-max --out bench.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "provisioner"))

LABEL = "wb-saas.benchmark"
HELPER_IMAGE = "alpine:3.20"  # present wherever the provisioner runs; used only to chown a fresh volume
MIB = 1048576
_LOCAL_ENDPOINT: str | None = None


def docker(*args: str, check: bool = True, timeout: int = 300) -> str:
    command = ["docker", *(["--host", _LOCAL_ENDPOINT] if _LOCAL_ENDPOINT else []), *args]
    environment = os.environ.copy()
    if _LOCAL_ENDPOINT:
        # A user's context switch during the run must not redirect load or cleanup.
        environment.pop("DOCKER_CONTEXT", None)
        environment.pop("DOCKER_HOST", None)
    done = subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=environment)
    if check and done.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args[:3])} failed: {done.stderr.strip()[:300]}")
    return done.stdout.strip()


def assert_local_engine() -> None:
    global _LOCAL_ENDPOINT
    _LOCAL_ENDPOINT = None
    if os.environ.get("DOCKER_HOST"):
        raise SystemExit("Refusing to run: DOCKER_HOST is set, the engine may be remote.")
    context = docker("context", "show")
    endpoint = docker("context", "inspect", context, "--format", "{{.Endpoints.docker.Host}}")
    try:
        address = urlsplit(endpoint)
        local_unix = (address.scheme == "unix" and not address.netloc and address.path.startswith("/")
                      and not address.path.startswith("//") and "\\" not in address.path
                      and address.path != "/" and not address.query and not address.fragment)
    except ValueError:
        local_unix = False
    local_pipe = re.fullmatch(r"npipe:////\./pipe/[A-Za-z0-9_.-]+", endpoint) is not None
    if (not (local_unix or local_pipe) or any(char.isspace() for char in endpoint)
            or any(word in context.lower() for word in ("staging", "prod", "marketshelper"))):
        # Do not echo remote URLs: their user-info may contain credentials.
        raise SystemExit("Refusing to run: only a local Unix socket or local Windows named pipe is allowed.")
    _LOCAL_ENDPOINT = endpoint


def mib(value: int) -> float:
    return round(value / MIB, 1)


def pct(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    return sorted_values[min(len(sorted_values) - 1, max(0, int(round(fraction * len(sorted_values))) - 1))]


def summarize(ms: list[float]) -> dict:
    ordered = sorted(ms)
    return {"n": len(ordered), "p50_ms": round(pct(ordered, 0.5)), "p95_ms": round(pct(ordered, 0.95)),
            "max_ms": round(ordered[-1]) if ordered else 0}


# One probe script per container: cgroup v2 numbers (v1 fallback for memory only).
PROBE = (
    "f=/sys/fs/cgroup; "
    "if [ -f $f/memory.current ]; then "
    "echo cur $(cat $f/memory.current); echo peak $(cat $f/memory.peak 2>/dev/null || echo 0); "
    "awk '$1==\"anon\"{print \"anon\",$2} $1==\"file\"{print \"file\",$2}' $f/memory.stat; "
    "awk '{print \"ev_\"$1,$2}' $f/memory.events; "
    "awk '$1==\"usage_usec\"||$1==\"nr_throttled\"||$1==\"throttled_usec\"||$1==\"nr_periods\"{print \"cpu_\"$1,$2}' $f/cpu.stat; "
    "else echo cur $(cat $f/memory/memory.usage_in_bytes); echo peak $(cat $f/memory/memory.max_usage_in_bytes); fi; "
    "echo pids $(cat $f/pids.current 2>/dev/null || echo 0)"
)


def probe(name: str) -> dict[str, int]:
    out = docker("exec", name, "sh", "-c", PROBE, check=False)
    values = {}
    for line in out.splitlines():
        key, _, number = line.partition(" ")
        if number.strip().isdigit():
            values[key] = int(number)
    return values


def vm_available_mib(via: str) -> int:
    for line in docker("exec", via, "cat", "/proc/meminfo").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    raise RuntimeError("MemAvailable not found")


def vm_loadavg(via: str) -> float:
    return float(docker("exec", via, "cat", "/proc/loadavg", check=False).split()[0] or 0)


# Runs inside a driver container on the benchmark network. Per tenant: PARALLEL workers walk the endpoint list ROUNDS
# times (that is the in-flight depth), plus one /healthz probe loop that must never wait for the work guard.
DRIVER = r'''
import json, sys, time, threading, urllib.request, urllib.error
targets, paths, rounds, parallel = json.loads(sys.argv[1]), json.loads(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
out, lock = {}, threading.Lock()
def one(host, path, bucket):
    t0 = time.perf_counter()
    kind = "ok"
    try:
        req = urllib.request.Request(f"http://{host}:8501{path}", headers={"Host": host})
        with urllib.request.urlopen(req, timeout=180) as r:
            r.read()
    except urllib.error.HTTPError as e:
        kind = "refused" if e.code < 500 else "fail"
        key = f"{e.code} {path.split('?')[0]}"
        bucket["codes"][key] = bucket["codes"].get(key, 0) + 1
    except Exception:
        kind = "fail"
    bucket[kind] += 1
    return (time.perf_counter() - t0) * 1000
def worker(host, bucket, calc):
    for _ in range(rounds):
        for path in paths:
            ms = one(host, path, bucket)
            with lock:
                calc.append(ms)
def health(host, stop, hs, bucket):
    while not stop.is_set():
        t0 = time.perf_counter()
        try:
            urllib.request.urlopen(urllib.request.Request(f"http://{host}:8501/healthz", headers={"Host": host}), timeout=30).read()
            hs.append((time.perf_counter() - t0) * 1000)
        except Exception:
            bucket["health_fail"] += 1
        stop.wait(0.5)
def tenant(host):
    bucket = {"ok": 0, "refused": 0, "fail": 0, "health_fail": 0, "codes": {}}
    calc, hs, stop = [], [], threading.Event()
    h = threading.Thread(target=health, args=(host, stop, hs, bucket)); h.start()
    ws = [threading.Thread(target=worker, args=(host, bucket, calc)) for _ in range(parallel)]
    [w.start() for w in ws]; [w.join() for w in ws]
    stop.set(); h.join()
    out[host] = {"bucket": bucket, "calc": calc, "health": hs}
ts = [threading.Thread(target=tenant, args=(t,)) for t in targets]
[t.start() for t in ts]; [t.join() for t in ts]
print(json.dumps(out))
'''

# One pass of the three background jobs the worker runs on a schedule, in an extra interpreter inside the tenant
# (an upper bound for memory: the real worker already holds these imports). The report job does real work: a queued run
# of every report metric builds a PDF (pandas + matplotlib). sync and agents need a WB token and are no-ops in the demo
# data (expected, recorded as "needs_token"); a real marketplace sync cannot be exercised on an isolated stand.
BACKGROUND = r'''
import json, sys, time, importlib
sys.path.insert(0, "/app/tenant-app")
res = {}
def timed(name, fn):
    t0 = time.perf_counter()
    try:
        out = fn(); res[name] = {"ok": True, "s": round(time.perf_counter() - t0, 2), "out": out if isinstance(out, (int, dict, str)) else None}
    except RuntimeError as e:
        needs = "токен" in str(e)
        res[name] = {"ok": needs, "needs_token": needs, "s": round(time.perf_counter() - t0, 2), "err": "" if needs else type(e).__name__}
    except BaseException as e:
        res[name] = {"ok": False, "s": round(time.perf_counter() - t0, 2), "err": type(e).__name__}
def reports():
    from config import DB_PATH
    from reports.store import ReportStore
    from reports.catalog import METRIC_LABELS
    store = ReportStore(DB_PATH)
    defs = store.list_definitions()
    did = defs[0]["id"] if defs else store.add_definition(name="bench", metrics=list(METRIC_LABELS), schedule_type="daily", schedule_time="23:59")
    store.enqueue_run(did)
    return importlib.import_module("report_scheduler").check_once(deliver=False)
timed("reports", reports)
timed("sync", lambda: importlib.import_module("sync").sync_all())
timed("agents", lambda: importlib.import_module("agent_sync").run_once())
print(json.dumps(res))
'''


class Bench:
    def __init__(self, args):
        self.args, self.run_id = args, uuid.uuid4().hex[:8]
        self.prefix = args.prefix
        self.network = f"{self.prefix}-net-{self.run_id}"
        self.names: list[str] = []
        self.footprint: float | None = None
        self.bg_delta_mib: float | None = None
        self.report: dict = {"image": args.image, "run": self.run_id, "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                             "limits": {"mem": args.limit_mem, "reservation": args.reservation, "cpus": 1.0},
                             "floor_mib": args.min_vm_available_mib, "steps": [], "notes": []}

    def label(self) -> list[str]:
        return ["--label", f"{LABEL}={self.run_id}"]

    def tenant(self, index: int) -> tuple[str, str]:
        return f"{self.prefix}-t{index:02d}-{self.run_id}", f"{self.prefix}-data-{index:02d}-{self.run_id}"

    # ---- tenants --------------------------------------------------------------------------------------------------
    def create_tenant(self, index: int) -> str:
        name, volume = self.tenant(index)
        docker("volume", "create", *self.label(), volume)
        if not self.owns("volume", volume):
            raise RuntimeError("Refusing to seed a volume without this benchmark's ownership label")
        docker("run", "--rm", "--name", f"{self.prefix}-chown{index}-{self.run_id}", *self.label(), "-u", "0", "--network", "none", "--cap-drop", "ALL", "--cap-add", "CHOWN", "-v", f"{volume}:/data",
               HELPER_IMAGE, "chown", "10001:10001", "/data")
        docker("run", "--rm", "--name", f"{self.prefix}-seed{index}-{self.run_id}", *self.label(), "--network", "none", "-u", "10001:10001", "--read-only", "--cap-drop", "ALL",
               "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "-v", f"{volume}:/app/data",
               "-e", "WB_SAAS_IS_DEMO=1", "-e", "WB_SAAS_TENANT_SLUG=demo", "-e", "WB_SAAS_DATA_DIR=/app/data",  # the seeder only runs for slug demo
               "--entrypoint", "python3", "-w", "/app/tenant-app", self.args.image, "seed_demo_data.py", timeout=300)
        tmpfs = []
        for path, size in (("/tmp", "64m"), ("/var/lib/nginx", "64m"), ("/var/cache/nginx", "16m"), ("/var/log/nginx", "16m"), ("/run", "16m")):
            tmpfs += ["--tmpfs", f"{path}:rw,noexec,nosuid,size={size},uid=10001,gid=10001,mode=0777"]
        docker("run", "-d", "--name", name, "--network", self.network, "--network-alias", name, *self.label(),
               "--label", f"wb-saas.slug={name}",  # the label the admission code counts running tenants by
               "-u", "10001:10001", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
               "--memory", self.args.limit_mem, "--memory-reservation", self.args.reservation, "--cpus", "1.0",
               "--oom-score-adj", "1000", "-v", f"{volume}:/app/data", *tmpfs,
               "-e", "WB_SAAS_IS_DEMO=1", "-e", "WB_SAAS_TENANT_SLUG=demo", "-e", "WB_SAAS_TELEGRAM_ENABLED=0",
               "-e", f"MH_ALLOWED_HOSTS=localhost,127.0.0.1,{name}", "-e", "WB_SAAS_INTERNAL_API_URL=http://127.0.0.1:9",
               "-e", "WB_SAAS_INTERNAL_API_SECRET=bench-only-secret", "-e", "MPLCONFIGDIR=/tmp/matplotlib", "-e", "HOME=/app",
               self.args.image)
        return name

    def wait_healthy(self, names: list[str], timeout: int = 300) -> list[str]:
        deadline, pending = time.time() + timeout, set(names)
        while pending and time.time() < deadline:
            for name in list(pending):
                state = docker("inspect", "--format", "{{.State.Health.Status}} {{.State.Running}}", name, check=False)
                if state.startswith("healthy"):
                    pending.discard(name)
            time.sleep(3)
        return sorted(pending)

    def grow_to(self, target: int) -> list[str]:
        indices = range(len(self.names) + 1, target + 1)
        with ThreadPoolExecutor(max_workers=4) as pool:
            created = list(pool.map(self.create_tenant, indices))
        self.names += created
        return created

    # ---- admission (the production code, real engine state) -------------------------------------------------------
    def admission(self, next_slug: str) -> dict:
        import docker as sdk
        from tenant_budget import CapacityExhausted, TenantBudget, parse_size
        if _LOCAL_ENDPOINT is None:
            raise RuntimeError("Local engine guard must pass before admission measurement")
        # Docker SDK does not interpret DOCKER_CONTEXT; use the same pinned engine as CLI.
        client = sdk.DockerClient(base_url=_LOCAL_ENDPOINT)
        budget = TenantBudget()
        read_available = lambda: self.available_now() * MIB  # the VM's own MemAvailable, also before the first tenant exists
        need = parse_size(self.args.reservation)
        keys = ("budget_mib", "reserved_mib", "free_mib", "running_tenants", "host_available_mib")
        try:
            budget.acquire(client, next_slug, need=need, configured="", fraction=0.7, min_available=parse_size("1g"),
                           wait_seconds=0, host_available=read_available)
            budget.release(next_slug)
            snapshot = budget.snapshot(client, next_slug, need=need, configured="", fraction=0.7, min_available=parse_size("1g"),
                                       host_available=read_available)
            return {"decision": "admitted", **{k: snapshot.get(k) for k in keys}}
        except CapacityExhausted as exc:
            return {"decision": "refused", "reason": exc.info.get("reason"), **{k: exc.info.get(k) for k in keys}}

    # ---- measurement ----------------------------------------------------------------------------------------------
    def probe_all(self) -> dict[str, dict]:
        with ThreadPoolExecutor(max_workers=8) as pool:
            return dict(zip(self.names, pool.map(probe, self.names)))

    def flags(self) -> tuple[int, int]:
        oom = sum(docker("inspect", "--format", "{{.State.OOMKilled}}", n, check=False) == "true" for n in self.names)
        restarts = sum(int(docker("inspect", "--format", "{{.RestartCount}}", n, check=False) or 0) for n in self.names)
        return oom, restarts

    def memory_summary(self, after: dict, before: dict | None) -> dict:
        cur = [mib(v.get("cur", 0)) for v in after.values()]
        peak = [mib(v.get("peak", 0)) for v in after.values()]
        anon = [mib(v["anon"]) for v in after.values() if "anon" in v]
        out = {"tenants": len(after), "cur_mib_avg": round(statistics.mean(cur), 1), "cur_mib_max": max(cur), "cur_mib_total": round(sum(cur), 1),
               "peak_mib_avg": round(statistics.mean(peak), 1), "peak_mib_max": max(peak), "peak_mib_sum": round(sum(peak), 1),
               "anon_mib_avg": round(statistics.mean(anon), 1) if anon else None,
               "pids_max": max(v.get("pids", 0) for v in after.values())}

        def delta(key: str) -> int:
            return sum(v.get(key, 0) for v in after.values()) - (sum(v.get(key, 0) for v in before.values()) if before else 0)
        for key in ("high", "max", "oom", "oom_kill"):
            out[f"memory_events_{key}"] = delta(f"ev_{key}")
        periods = delta("cpu_nr_periods")
        out.update(cpu_seconds=round(delta("cpu_usage_usec") / 1e6, 1), cpu_throttled_periods=delta("cpu_nr_throttled"), cpu_periods=periods,
                   cpu_throttled_pct=round(100 * delta("cpu_nr_throttled") / periods, 1) if periods else 0.0,
                   cpu_throttled_seconds=round(delta("cpu_throttled_usec") / 1e6, 1))
        return out

    def drive(self, group: list[str], paths: list[str], parallel: int, k: int) -> dict:
        name = f"{self.prefix}-driver{k}-{self.run_id}"
        out = docker("run", "--rm", "--name", name, "--network", self.network, *self.label(), "-u", "10001:10001", "--cap-drop", "ALL",
                     "--read-only", "--entrypoint", "python3", self.args.image, "-c", DRIVER, json.dumps(group), json.dumps(paths),
                     str(self.args.rounds), str(parallel), timeout=3600)
        return json.loads(out.splitlines()[-1])

    def run_phase(self, label: str, calc: bool, background: bool, parallel: int, paths: list[str], before: dict,
                  bg_names: list[str] | None = None) -> dict:
        bg_names = bg_names if bg_names is not None else self.names
        floor, via = self.args.min_vm_available_mib, self.names[0]
        box: dict = {"min": vm_available_mib(via), "load_max": 0.0}
        stop = threading.Event()

        def watch():
            while not stop.is_set():
                try:
                    box["min"] = min(box["min"], vm_available_mib(via))
                    box["load_max"] = max(box["load_max"], vm_loadavg(via))
                except Exception:
                    pass
                stop.wait(1.5)
        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        started = time.time()
        results: dict = {}
        bg: dict = {}

        def drivers():
            groups = [self.names[i::self.args.drivers] for i in range(self.args.drivers)]
            with ThreadPoolExecutor(max_workers=len(groups)) as pool:
                futures = [pool.submit(self.drive, g, paths, parallel, k) for k, g in enumerate(groups) if g]
                for f in futures:
                    results.update(f.result())

        def background_pass():
            with ThreadPoolExecutor(max_workers=max(1, len(bg_names))) as pool:
                outputs = list(pool.map(lambda n: docker("exec", n, "python3", "-c", BACKGROUND, check=False, timeout=900), bg_names))
            for name, text in zip(bg_names, outputs):
                try:
                    bg[name] = json.loads(text.splitlines()[-1])
                except Exception:
                    bg[name] = {"error": "no result"}
        threads = []
        if calc:
            threads.append(threading.Thread(target=drivers))
        if background:
            threads.append(threading.Thread(target=background_pass))
        [t.start() for t in threads]
        [t.join() for t in threads]
        stop.set()
        watcher.join(timeout=5)
        calc_ms, health_ms, ok, refused, fail, health_fail, codes = [], [], 0, 0, 0, 0, {}
        for data in results.values():
            calc_ms += data["calc"]
            health_ms += data["health"]
            ok += data["bucket"]["ok"]
            refused += data["bucket"]["refused"]
            fail += data["bucket"]["fail"]
            health_fail += data["bucket"]["health_fail"]
            for key, count in data["bucket"]["codes"].items():
                codes[key] = codes.get(key, 0) + count
        after = self.probe_all()
        oom, restarts = self.flags()
        phase = {"phase": label, "seconds": round(time.time() - started, 1), "parallel_per_tenant": parallel if calc else 0,
                 "memory": self.memory_summary(after, before), "vm_min_available_mib": box["min"], "vm_load1_max": box["load_max"],
                 "oom_killed_containers": oom, "restarts": restarts, "floor_breached": box["min"] < floor}
        if calc:
            phase["calc"] = {**summarize(calc_ms), "requests": ok + refused + fail, "ok": ok, "refused_4xx": refused,
                             "failures_5xx_or_no_answer": fail, "http_4xx_by_endpoint": codes}
            phase["healthz"] = {**summarize(health_ms), "failures": health_fail}
        if background:
            reports_s = [v["reports"]["s"] for v in bg.values() if isinstance(v.get("reports"), dict)]
            generated = sum(int(v["reports"].get("out") or 0) for v in bg.values() if isinstance(v.get("reports"), dict))
            failed = [v for v in bg.values() if ("error" in v) or not all(j.get("ok") for j in v.values())]
            phase["background"] = {"concurrency": f"{len(bg_names)} of {len(self.names)} tenants at the same time", "tenants": len(bg), "tenants_all_jobs_ok": len(bg) - len(failed), "tenants_with_failed_job": len(failed),
                                   "reports_generated": generated, "report_s_p50": round(statistics.median(reports_s), 2) if reports_s else None,
                                   "report_s_max": max(reports_s) if reports_s else None,
                                   "sync_and_agents": "needs_token (no-op in demo data)",
                                   "error_types": sorted({j.get("err") for v in bg.values() if "error" not in v for j in v.values() if j.get("err")})}
        phase["_after"] = after
        return phase

    def endpoints(self) -> list[str]:
        demo = json.loads(Path(self.args.endpoints_from).read_text(encoding="utf-8"))
        base = sorted({f"/api/v1/{k.split('@')[0]}" for k in demo if k.split("@")[0].startswith(("dashboard/", "control/")) and "__" not in k})
        return base + [p + "?period=90d" for p in base[: self.args.heavy_variants]]

    # ---- the acceptance run ---------------------------------------------------------------------------------------
    def available_now(self) -> int:
        if self.names:
            return vm_available_mib(self.names[0])
        out = docker("run", "--rm", "--name", f"{self.prefix}-memory-{uuid.uuid4().hex[:8]}", *self.label(), "--entrypoint", "cat", self.args.image, "/proc/meminfo")
        return next(int(line.split()[1]) // 1024 for line in out.splitlines() if line.startswith("MemAvailable:"))

    def can_start(self, extra: int) -> tuple[bool, dict]:
        """Gate for growing by ``extra`` tenants from the measured MemAvailable and the measured per-tenant footprint."""
        available = self.available_now()
        per = self.footprint or self.args.first_guess_mib
        gate = {"vm_available_mib": available, "per_tenant_mib": round(per, 1), "per_tenant_source": "measured" if self.footprint else "assumed until the first step is measured",
                "extra_tenants": extra, "needed_mib": round(extra * per), "floor_mib": self.args.min_vm_available_mib}
        return available - extra * per >= self.args.min_vm_available_mib, gate

    def calibrate_background(self) -> None:
        """Measure what ONE background pass adds on top of an idle tenant (peak delta), so concurrency can be gated honestly."""
        first = self.names[0]
        before = probe(first).get("peak", 0)
        docker("exec", first, "python3", "-c", BACKGROUND, check=False, timeout=900)
        self.bg_delta_mib = max(1.0, mib(probe(first).get("peak", 0) - before))

    def one_step(self, target: int, label: str) -> dict:
        step: dict = {"target": target, "label": label}
        step["admission_before_start"] = self.admission(f"{self.prefix}-t{target:02d}-{self.run_id}")
        missing = self.wait_healthy(self.grow_to(target))
        if missing:
            step["not_healthy"] = missing
            self.report["notes"].append(f"{target}: not healthy within the timeout: {missing}")
        time.sleep(self.args.settle)
        idle = self.probe_all()
        step["tenants"] = len(self.names)
        step["idle"] = {"memory": self.memory_summary(idle, None), "vm_available_mib": vm_available_mib(self.names[0]), "vm_load1": vm_loadavg(self.names[0])}
        self.footprint = step["idle"]["memory"]["cur_mib_avg"]
        if self.bg_delta_mib is None:
            self.calibrate_background()
        step["background_pass_peak_delta_mib_measured_on_one_tenant"] = self.bg_delta_mib
        paths = self.endpoints()
        step["endpoints"] = len(paths)
        phases, before = [], idle
        for phase_label, calc, bg, parallel in (("calc_serial", True, False, 1), ("calc_parallel", True, False, self.args.parallel),
                                                 ("background", False, True, 0), ("combined", True, True, self.args.parallel)):
            available = vm_available_mib(self.names[0])
            if available < self.args.min_vm_available_mib:
                phases.append({"phase": phase_label, "skipped": "VM available memory is below the floor before the phase", "vm_available_mib": available})
                continue
            bg_names = None
            if bg:
                # How many background passes the stand can hold at once above the floor, from the measured per-pass peak.
                fits = int((available - self.args.min_vm_available_mib) / (self.bg_delta_mib * 1.15))
                if fits < len(self.names):
                    step.setdefault("refused_full_background_concurrency", []).append(
                        {"phase": phase_label, "requested": len(self.names), "fits_above_floor": max(0, fits), "vm_available_mib": available,
                         "per_pass_peak_delta_mib": self.bg_delta_mib, "floor_mib": self.args.min_vm_available_mib})
                if fits < 1:
                    phases.append({"phase": phase_label, "skipped": "not even one background pass fits above the floor", "vm_available_mib": available})
                    continue
                bg_names = self.names[:max(1, min(len(self.names), fits))]
            phase = self.run_phase(phase_label, calc, bg, parallel, paths, before, bg_names)
            before = phase.pop("_after")
            phases.append(phase)
            print(json.dumps({"n": target, "phase": phase_label, "vm_min": phase["vm_min_available_mib"], "oom": phase["oom_killed_containers"],
                              "restarts": phase["restarts"], "p95": phase.get("calc", {}).get("p95_ms")}), flush=True)
            if phase["floor_breached"] or phase["oom_killed_containers"] or phase["restarts"]:
                step["stopped_after"] = phase_label
                break
        step["phases"] = phases
        return step

    def run(self) -> dict:
        assert_local_engine()
        docker("network", "create", "--internal", *self.label(), self.network)
        try:
            if self.args.admission_only:
                for target in self.args.steps:
                    self.wait_healthy(self.grow_to(target))
                    time.sleep(10)
                    self.report["steps"].append({"target": target, "admission_for_next_tenant": self.admission(f"{self.prefix}-t{target + 1:02d}-{self.run_id}"),
                                                 "vm_available_mib": vm_available_mib(self.names[0])})
                    print(json.dumps(self.report["steps"][-1]), flush=True)
                return self.report
            for target in self.args.steps:
                ok, gate = self.can_start(max(0, target - len(self.names)))
                if not ok:
                    self.report["steps"].append({"target": target, "result": "REFUSED_NOT_RUN", "reason": "starting these tenants would leave the VM below the floor", **gate})
                    self.report["notes"].append(f"{target} tenants not run: {gate}")
                    continue
                self.report["steps"].append(self.one_step(target, str(target)))
                if self.report["steps"][-1].get("stopped_after"):
                    _, gate = self.can_start(0)
                    for later in (t for t in self.args.steps if t > target):
                        self.report["steps"].append({"target": later, "result": "REFUSED_NOT_RUN",
                                                     "reason": f"the {target}-tenant step already breached the VM floor or killed a container: this stand's limit is reached",
                                                     **gate})
                    break
            if self.args.probe_max and self.names:
                self.probe_maximum()
        finally:
            self.report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self.cleanup()
        return self.report

    def probe_maximum(self) -> None:
        """Add tenants one at a time while the measured gate allows it: the largest N this stand holds above the floor."""
        measured = [s["target"] for s in self.report["steps"] if "phases" in s]
        reached = len(self.names)
        while True:
            ok, gate = self.can_start(1)
            if not ok:
                self.report["limit"] = {"max_tenants_started_above_floor": reached, "refused_at": reached + 1, **gate}
                break
            self.names += [self.create_tenant(len(self.names) + 1)]
            self.wait_healthy(self.names[-1:])
            time.sleep(10)
            reached = len(self.names)
            if reached >= max(self.args.steps):
                self.report["limit"] = {"max_tenants_started_above_floor": reached, "refused_at": None}
                break
        if reached > max(measured, default=0):
            time.sleep(self.args.settle)
            self.report["steps"].append(self.one_step(reached, f"{reached} (stand maximum above the floor)"))

    def cleanup(self) -> None:
        ids = docker("ps", "-aq", "--filter", f"label={LABEL}={self.run_id}", check=False).split()
        ids = [cid for cid in ids if self.owns("container", cid)]
        if ids:
            docker("rm", "-f", *ids, check=False, timeout=600)
        for volume in docker("volume", "ls", "-q", "--filter", f"label={LABEL}={self.run_id}", check=False).split():
            if self.owns("volume", volume):
                docker("volume", "rm", "-f", volume, check=False)
        if self.owns("network", self.network):
            docker("network", "rm", self.network, check=False)

    def owns(self, kind: str, reference: str) -> bool:
        labels = ".Config.Labels" if kind == "container" else ".Labels"
        template = '{{.Name}} {{index ' + labels + ' "' + LABEL + '"}}'
        metadata = docker(kind, "inspect", "--format", template, reference, check=False).split()
        return (len(metadata) == 2 and metadata[0].lstrip("/").startswith(self.prefix + "-")
                and metadata[1] == self.run_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", required=True)
    parser.add_argument("--steps", default="20,25,30", type=lambda s: [int(x) for x in s.split(",")])
    parser.add_argument("--probe-max", action="store_true", help="after the steps, add tenants one by one up to the floor and run the phases at that maximum")
    parser.add_argument("--admission-only", action="store_true", help="only grow to each step and record the real admission decision for the next tenant (no load)")
    parser.add_argument("--limit-mem", default="1536m")
    parser.add_argument("--reservation", default="384m")
    parser.add_argument("--min-vm-available-mib", type=int, default=1500)
    parser.add_argument("--first-guess-mib", type=int, default=200, help="per-tenant footprint assumed only until the first step has been measured")
    parser.add_argument("--settle", type=int, default=45)
    parser.add_argument("--parallel", type=int, default=4, help="in-flight requests per tenant in the parallel phases")
    parser.add_argument("--drivers", type=int, default=3, help="driver containers sharing the tenants (keeps the client from being the bottleneck)")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--heavy-variants", type=int, default=6)
    parser.add_argument("--prefix", default="bench")
    parser.add_argument("--endpoints-from", default=str(Path(__file__).resolve().parents[1] / "mobile" / "scripts" / "demo-cabinet.json"))
    parser.add_argument("--out", default="bench-tenant-density.json")
    args = parser.parse_args()
    report = Bench(args).run()
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
