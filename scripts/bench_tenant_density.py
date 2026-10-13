"""Isolated tenant workload on LOCAL Docker, synthetic or frozen SQL-copy data.

Default deployed mode uses the image entrypoint: API + resident worker + nginx.
An explicit api-only comparison excludes that worker and generates PDFs separately.
Provider integrations are deliberately not exercised; neither profile proves
production capacity without representative fixture, hardware and a real measured run.

What it does
  * starts N tenant containers from one image with the provisioner's hardening (read-only root, cap_drop ALL, uid 10001,
    hard mem limit, soft reservation, 1 CPU) on a private internal network, each with its own synthetic or frozen-copy
    volume (demo mode: read-only API, no marketplace calls, no credentials);
  * at each step asks the REAL admission code (provisioner/tenant_budget.py) whether one more tenant would be admitted,
    against the real engine state, and records the decision (admitted / refused + reason + numbers);
  * runs four measured phases with ALL tenants at the same time (no waves):
      calc_serial    one in-flight dashboard request per tenant (cross-tenant contention only)
      calc_parallel  PARALLEL in-flight requests per tenant (the per-tenant work guard queues them)
      background     one explicit local PDF report in every tenant (no provider jobs)
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
import hashlib
import inspect
import math
import os
import re
import statistics
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "provisioner"))

LABEL = "wb-saas.benchmark"
HELPER_IMAGE = "alpine:3.20"  # present wherever the provisioner runs; used only to chown a fresh volume
MIB = 1048576
_LOCAL_ENDPOINT: str | None = None


def prepare_benchmark_db(database: Path) -> dict:
    """Operational sanitization applies only to a newly owned benchmark copy."""
    with closing(sqlite3.connect(database)) as connection, connection:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        def columns(table):
            return {r[1] for r in connection.execute('PRAGMA table_info("' + table.replace('"', '""') + '")')}
        if "shop_workspaces" in tables:
            fields = columns("shop_workspaces")
            updates = [name + "=" + ("0" if name == "active" else "''") for name in ("credential_cipher", "active") if name in fields]
            if updates:
                connection.execute("UPDATE shop_workspaces SET " + ",".join(updates))
        if "report_definitions" in tables:
            fields = columns("report_definitions")
            updates = [name + "=0" for name in ("email_enabled", "telegram_enabled") if name in fields]
            if "schedule_type" in fields:
                updates.append("schedule_type='manual'")
            if updates:
                connection.execute("UPDATE report_definitions SET " + ",".join(updates))
        if "shop_schedules" in tables and "enabled" in columns("shop_schedules"):
            connection.execute("UPDATE shop_schedules SET enabled=0")
        for table in ("report_runs", "growth_outbox", "settings_change_history"):
            if table in tables:
                connection.execute('DELETE FROM "' + table + '"')
        ids = []
        for table in ("sales", "orders", "products_catalog"):
            if table in tables and "nm_id" in columns(table):
                ids = [int(r[0]) for r in connection.execute(f"SELECT DISTINCT nm_id FROM {table} WHERE nm_id > 0 ORDER BY nm_id LIMIT 10")]
                if ids:
                    break
        connection.execute("CREATE TABLE IF NOT EXISTS rnp_favorite(nm_id INTEGER PRIMARY KEY,position INTEGER NOT NULL)")
        connection.execute("DELETE FROM rnp_favorite")
        connection.executemany("INSERT INTO rnp_favorite VALUES(?,?)", [(nm_id, index) for index, nm_id in enumerate(ids)])
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("Benchmark copy integrity failed")
    return {"favorites": len(ids), "sample_nm_id": ids[0] if ids else None}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reject_fixture_links(path: Path) -> None:
    for component in (path, *path.parents):
        if component.is_symlink() or getattr(component, "is_junction", lambda: False)():
            raise ValueError("Fixture symlinks and junctions are not allowed")


def copy_frozen_fixture(source: Path, destination: Path) -> dict:
    """Copy only tenant SQLite; token/config files never enter the new volume."""
    source, destination = Path(source), Path(destination)
    reject_fixture_links(source)
    reject_fixture_links(destination)
    if not source.is_dir() or any(p.is_symlink() or getattr(p, "is_junction", lambda: False)() for p in source.rglob("*")):
        raise ValueError("Fixture links are not allowed")
    database = source / "wb_dashboard.sqlite3"
    if not database.is_file() or any((source / (database.name + suffix)).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("Fixture must be a frozen SQLite snapshot without sidecars")
    resolved_source, resolved_destination = source.resolve(), destination.resolve()
    if (resolved_source == resolved_destination or resolved_source in resolved_destination.parents
            or resolved_destination in resolved_source.parents or (destination.exists() and any(destination.iterdir()))):
        raise ValueError("Destination must be a new empty benchmark volume")
    digest = file_sha256(database)
    destination.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as incoming:
        with closing(sqlite3.connect(destination / database.name)) as copied:
            incoming.backup(copied)
    metadata = prepare_benchmark_db(destination / database.name)
    (destination / "settings.json").write_text(json.dumps({"demo_mode": True, "sync_interval_minutes": 1440, "agent_interval_minutes": 1440}), encoding="utf-8")
    if file_sha256(database) != digest:
        raise ValueError("Frozen fixture changed during copy")
    return {"databases": 1, "source_sha256": digest, **metadata}


API_ONLY_ENTRYPOINT = r'''set -euo pipefail
cd /app/tenant-app
uvicorn api_server:app --host 127.0.0.1 --port 8001 &
api=$!
nginx -g 'daemon off;' &
web=$!
trap 'kill "$api" "$web" 2>/dev/null || true' TERM INT EXIT
wait -n "$api" "$web"
'''

ISOLATED_ENV = {
    "WB_SAAS_IS_DEMO": "1", "WB_SAAS_TENANT_SLUG": "demo", "WB_SAAS_TELEGRAM_ENABLED": "0",
    "WB_SAAS_INTERNAL_API_URL": "", "WB_SAAS_INTERNAL_API_SECRET": "bench-only-secret", "MH_LOCAL_JOBS": "0",
    "WB_SAAS_SMTP_HOST": "", "WB_SAAS_SMTP_USER": "", "WB_SAAS_SMTP_PASSWORD": "",
    "WB_SAAS_TELEGRAM_BOT_TOKEN": "", "WB_SAAS_SENTRY_DSN": "", "WB_SAAS_DATABASE_URL": "",
    "WB_SAAS_YOOKASSA_SHOP_ID": "", "WB_SAAS_YOOKASSA_SECRET_KEY": "",
    "WB_SAAS_DATA_ENCRYPTION_KEY": "", "WB_SAAS_DATA_ENCRYPTION_KEYRING": "",
}


def isolated_env_args():
    return [item for key, value in ISOLATED_ENV.items() for item in ("-e", key + "=" + value)]


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
    "echo cur $(cat $f/memory.current); if [ -f $f/memory.peak ]; then echo peak $(cat $f/memory.peak); fi; "
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
def validate_api_body(body: bytes, status: int) -> None:
    if status != 200 or not body or len(body) > 16 * 1024 * 1024:
        raise ValueError("Invalid or oversized API response")
    value = json.loads(body)
    if not isinstance(value, (dict, list)) or not value:
        raise ValueError("API response must contain a nonempty JSON object or array")


DRIVER = r'''
import json, sys, time, threading, urllib.request, urllib.error
''' + inspect.getsource(validate_api_body) + r'''
targets, paths, rounds, parallel = json.loads(sys.argv[1]), json.loads(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
out, lock = {}, threading.Lock()
opener = urllib.request.build_opener(type('NoRedirect', (urllib.request.HTTPRedirectHandler,), {'redirect_request': lambda self,*args: None})())
def one(host, path, bucket):
    t0 = time.perf_counter()
    kind = "ok"
    try:
        req = urllib.request.Request(f"http://{host}:8501{path}", headers={"Host": host})
        with opener.open(req, timeout=20) as r:
            validate_api_body(r.read(16*1024*1024+1), r.status)
    except urllib.error.HTTPError as e:
        kind = "refused" if e.code < 500 else "fail"
        key = f"{e.code} {path.split('?')[0]}"
        with lock:
            bucket["codes"][key] = bucket["codes"].get(key, 0) + 1
    except Exception:
        kind = "fail"
    with lock:
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
            with opener.open(urllib.request.Request(f"http://{host}:8501/healthz", headers={"Host": host}), timeout=5) as response:
                body=response.read(64*1024+1)
                if response.status != 200 or not body or len(body)>64*1024:
                    raise ValueError('Invalid health response')
            hs.append((time.perf_counter() - t0) * 1000)
        except Exception:
            bucket["health_fail"] += 1
        stop.wait(0.5)
def tenant(host):
    bucket = {"ok": 0, "refused": 0, "fail": 0, "health_fail": 0, "codes": {}}
    calc, hs, stop = [], [], threading.Event()
    h = threading.Thread(target=health, args=(host, stop, hs, bucket)); h.start()
    calc_started=time.time()
    ws = [threading.Thread(target=worker, args=(host, bucket, calc)) for _ in range(parallel)]
    [w.start() for w in ws]; [w.join() for w in ws]
    calc_finished=time.time()
    stop.set(); h.join()
    out[host] = {"bucket": bucket, "calc": calc, "health": hs, "calc_window": {"start":calc_started,"end":calc_finished}}
ts = [threading.Thread(target=tenant, args=(t,)) for t in targets]
[t.start() for t in ts]; [t.join() for t in ts]
print(json.dumps(out))
'''

# One explicit report pass. Provider sync/agents are not exercised or labelled successful.
BACKGROUND = r'''
import json, sys, time, importlib
sys.path.insert(0, "/app/tenant-app")
res = {}
def timed(name, fn):
    t0 = time.perf_counter()
    wall_started=time.time()
    try:
        out = fn(); res[name] = {"ok": True, "s": round(time.perf_counter() - t0, 2), "out": out if isinstance(out, (int, dict, str)) else None, "work_window":{"start":wall_started,"end":time.time(),"resolution_s":0}}
    except RuntimeError as e:
        res[name] = {"ok": False, "s": round(time.perf_counter() - t0, 2), "err": type(e).__name__}
    except BaseException as e:
        res[name] = {"ok": False, "s": round(time.perf_counter() - t0, 2), "err": type(e).__name__}
def reports():
    from config import DB_PATH
    from reports.store import ReportStore
    from reports.catalog import METRIC_LABELS
    store = ReportStore(DB_PATH)
    did = store.add_definition(name="density-benchmark", metrics=list(METRIC_LABELS), schedule_type="manual", schedule_time="23:59")
    store.enqueue_run(did)
    return importlib.import_module("report_scheduler").check_once(deliver=False)
timed("reports", reports)
print(json.dumps(res))
'''


def resident_worker_alive(proc_dir: Path = Path('/proc')) -> bool:
    for candidate in proc_dir.glob('[0-9]*/cmdline'):
        try:
            args = candidate.read_bytes().split(b'\0')
            if any(Path(arg.decode(errors='replace')).name == 'background_worker.py' for arg in args):
                return True
        except OSError:
            continue
    return False


def resident_report_state(database: Path, run_id: int, definition_id: int, data_dir: Path, *, worker_alive: bool) -> dict:
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        row = connection.execute('SELECT id,definition_id,status,file_path,delivery_requested,delivery_status,started_at,finished_at FROM report_runs WHERE id=? AND definition_id=?', (run_id, definition_id)).fetchone()
    result = {'run_id': run_id, 'definition_id': definition_id, 'status': row[2] if row else 'missing', 'worker_alive': worker_alive, 'pdf_complete': False}
    if row and row[2] == 'ok' and not row[4] and row[5] == 'not_requested' and row[3]:
        if row[6] and row[7]:
            def epoch(raw):
                parsed=datetime.fromisoformat(raw)
                return (parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo('Europe/Moscow'))).timestamp()
            result['work_window']={'start':epoch(row[6]),'end':epoch(row[7]),'resolution_s':1}
        path = Path(row[3]).resolve()
        if path.is_relative_to(data_dir.resolve()) and path.is_file():
            with path.open('rb') as document:
                header = document.read(5)
                document.seek(max(0, path.stat().st_size - 1024))
                result['pdf_complete'] = header == b'%PDF-' and b'%%EOF' in document.read()
    return result


RESIDENT_QUEUE = r'''
import json,sys
from pathlib import Path
sys.path.insert(0,'/app/tenant-app')
from config import DB_PATH
from reports.store import ReportStore
from reports.catalog import METRIC_LABELS
''' + inspect.getsource(resident_worker_alive) + r'''
store=ReportStore(DB_PATH)
definition=store.add_definition(name=sys.argv[1],metrics=list(METRIC_LABELS),schedule_type='manual',schedule_time='23:59',email_enabled=False,telegram_enabled=False)
run=store.enqueue_run(definition,period_days=90,delivery_requested=False)
print(json.dumps({'run_id':run,'definition_id':definition,'worker_alive':resident_worker_alive()}))
'''

RESIDENT_POLL = r'''
import json,sys,sqlite3
from pathlib import Path
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0,'/app/tenant-app')
from config import DB_PATH,DATA_DIR
''' + inspect.getsource(resident_worker_alive) + inspect.getsource(resident_report_state) + r'''
print(json.dumps(resident_report_state(DB_PATH,int(sys.argv[1]),int(sys.argv[2]),DATA_DIR,worker_alive=resident_worker_alive())))
'''


def combined_overlap(calc_window: dict | None, work_window: dict | None) -> dict:
    evidence = {'calc_window': calc_window, 'report_window': work_window, 'proven': False}
    if not isinstance(calc_window, dict) or not isinstance(work_window, dict):
        return evidence
    values = [calc_window.get('start'), calc_window.get('end'), work_window.get('start'), work_window.get('end'), work_window.get('resolution_s')]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        return evidence
    cs, ce, rs, re, precision = values
    # ReportStore persists MSK timestamps truncated to seconds. Use the guaranteed
    # interior interval; rounding a start down must never invent an overlap.
    if cs < ce and rs <= re and 0 <= precision <= 1:
        evidence['guaranteed_overlap_s'] = round(max(0, min(ce, re)-max(cs, rs+precision)), 6)
        evidence['proven'] = evidence['guaranteed_overlap_s'] > 0
    return evidence


class Bench:
    def __init__(self, args):
        self.args, self.run_id = args, uuid.uuid4().hex[:8]
        self.prefix = args.prefix
        self.network = f"{self.prefix}-net-{self.run_id}"
        self.names: list[str] = []
        self.footprint: float | None = None
        self.bg_delta_mib: float | None = None
        self.capacity_client = None
        self.capacity_budget = None
        self.capacity_lock = threading.Lock()
        self.phase_spawn_lock = threading.RLock()
        self.phase_stop = threading.Event()
        self.fixture_metadata: dict = {}
        self.runtime_mode = getattr(args, "runtime_mode", "deployed")
        self.report: dict = {"image": args.image, "run": self.run_id, "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                             "limits": {"mem": args.limit_mem, "reservation": args.reservation, "cpus": 1.0},
                             "floor_mib": args.min_vm_available_mib, "steps": [], "notes": [],
                             "fixture": "restored SQL-only copy" if getattr(args, "fixture_dir", None) else "synthetic",
                             "runtime_mode": self.runtime_mode,
                             "automatic_background": self.runtime_mode == "deployed", "provider_jobs": "not exercised: no credentials or outbound networking",
                             "topology": "API+resident background_worker+nginx; queued PDF generated by resident worker" if self.runtime_mode == "deployed" else "API+nginx only; PDF in an additional process (limited comparison)",
                             "production_capacity_proven": False}

    def label(self) -> list[str]:
        return ["--label", f"{LABEL}={self.run_id}"]

    def tenant(self, index: int) -> tuple[str, str]:
        return f"{self.prefix}-t{index:02d}-{self.run_id}", f"{self.prefix}-data-{index:02d}-{self.run_id}"

    # ---- tenants --------------------------------------------------------------------------------------------------
    def create_tenant(self, index: int) -> str:
        name, volume = self.tenant(index)
        held = self.claim_start(name)
        try:
            return self._create_tenant(index, name, volume)
        finally:
            if held:
                self.capacity_budget.release(name)

    def _create_tenant(self, index: int, name: str, volume: str) -> str:
        docker("volume", "create", *self.label(), volume)
        if not self.owns("volume", volume):
            raise RuntimeError("Refusing to seed a volume without this benchmark's ownership label")
        helper_limits = ["--memory", self.args.limit_mem, "--cpus", "1.0", "--pids-limit", "256", "--oom-score-adj", "1000"]
        fixture = getattr(self.args, "fixture_dir", None)
        if not fixture:
            docker("run", "--rm", "--name", f"{self.prefix}-chown{index}-{self.run_id}", *self.label(), "--memory", "128m", "--cpus", ".25", "--pids-limit", "32", "--oom-score-adj", "1000", "-u", "0", "--network", "none", "--cap-drop", "ALL", "--cap-add", "CHOWN", "-v", f"{volume}:/data",
                   HELPER_IMAGE, "chown", "10001:10001", "/data")
        preamble = "import sys,sqlite3,json,hashlib; from pathlib import Path; from contextlib import closing\n" + inspect.getsource(prepare_benchmark_db)
        if fixture:
            reject_fixture_links(Path(fixture))
            source = Path(fixture).resolve()
            code = preamble + inspect.getsource(file_sha256) + inspect.getsource(reject_fixture_links) + inspect.getsource(copy_frozen_fixture)
            code += "\nresult=copy_frozen_fixture(Path('/fixture'),Path('/app/data')); import os\n"
            # Keep root ownership until all writes finish. With only CAP_CHOWN, chmod
            # must precede chown; process directories last so traversal stays possible.
            code += "for p in sorted(Path('/app/data').rglob('*'),key=lambda p:len(p.parts),reverse=True): os.chmod(p,0o700 if p.is_dir() else 0o600); os.chown(p,10001,10001)\n"
            code += "os.chmod('/app/data',0o700); os.chown('/app/data',10001,10001)\nprint(json.dumps(result))"
            output = docker("run", "--rm", "--name", f"{self.prefix}-copy{index}-{self.run_id}", *self.label(), "--network", "none", "-u", "0",
                            "--read-only", *helper_limits, "--cap-drop", "ALL", "--cap-add", "DAC_READ_SEARCH", "--cap-add", "CHOWN",
                            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "-v", f"{source}:/fixture:ro", "--mount", f"type=volume,source={volume},target=/app/data,volume-nocopy",
                            *isolated_env_args(), "--entrypoint", "python3", self.args.image, "-c", code, timeout=900)
        else:
            code = preamble + "\nimport seed_demo_data,config\nseed_demo_data.DAYS_OF_HISTORY=int(sys.argv[1]);seed_demo_data.main()\nprint(json.dumps(prepare_benchmark_db(config.DB_PATH)))"
            output = docker("run", "--rm", "--name", f"{self.prefix}-seed{index}-{self.run_id}", *self.label(), *helper_limits, "--network", "none", "-u", "10001:10001", "--read-only", "--cap-drop", "ALL",
                            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "-v", f"{volume}:/app/data", *isolated_env_args(), "-e", "WB_SAAS_DATA_DIR=/app/data",
                            "--entrypoint", "python3", "-w", "/app/tenant-app", self.args.image, "-c", code, str(getattr(self.args, "synthetic_days", 365)), timeout=900)
        if output.strip():
            metadata = json.loads(output.splitlines()[-1])
            if not metadata.get("sample_nm_id") or not metadata.get("favorites"):
                raise RuntimeError("Fixture contains no product data")
            self.fixture_metadata = metadata
            self.report["fixture_summary"] = {key: metadata[key] for key in ("databases", "source_sha256", "favorites") if key in metadata}
        elif getattr(self.args, "enforce_admission", False) or fixture:
            raise RuntimeError("Fixture preparation produced no result")
        tmpfs = []
        for path, size in (("/tmp", "64m"), ("/var/lib/nginx", "64m"), ("/var/cache/nginx", "16m"), ("/var/log/nginx", "16m"), ("/run", "16m")):
            tmpfs += ["--tmpfs", f"{path}:rw,noexec,nosuid,size={size},uid=10001,gid=10001,mode=0777"]
        startup = [self.args.image] if self.runtime_mode == "deployed" else ["--entrypoint", "/bin/bash", self.args.image, "-c", API_ONLY_ENTRYPOINT]
        docker("run", "-d", "--name", name, "--network", self.network, "--network-alias", name, *self.label(),
               "--label", f"wb-saas.slug={name}",  # the label the admission code counts running tenants by
               "-u", "10001:10001", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
               "--memory", self.args.limit_mem, "--memory-reservation", self.args.reservation, "--cpus", "1.0",
               "--oom-score-adj", "1000", "--pids-limit", "256", "-v", f"{volume}:/app/data", *tmpfs,
               *isolated_env_args(), "-e", f"MH_ALLOWED_HOSTS=localhost,127.0.0.1,{name}", "-e", "MPLCONFIGDIR=/tmp/matplotlib", "-e", "HOME=/app",
               *startup)
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
        client, budget = self.admission_state()
        from tenant_budget import CapacityExhausted, parse_size
        read_available = lambda: self.available_now() * MIB
        need = parse_size(self.args.reservation)
        keys = ("budget_mib", "reserved_mib", "free_mib", "running_tenants", "host_available_mib")
        try:
            budget.acquire(client, next_slug, need=need, configured="", fraction=0.7, min_available=parse_size("1g"), wait_seconds=0, host_available=read_available)
            budget.release(next_slug)
            snapshot = budget.snapshot(client, next_slug, need=need, configured="", fraction=0.7, min_available=parse_size("1g"), host_available=read_available)
            return {"decision": "admitted", **{k: snapshot.get(k) for k in keys}}
        except CapacityExhausted as exc:
            return {"decision": "refused", "reason": exc.info.get("reason"), **{k: exc.info.get(k) for k in keys}}

    def admission_state(self):
        import docker as sdk
        from tenant_budget import TenantBudget
        if _LOCAL_ENDPOINT is None:
            raise RuntimeError("Local engine guard must pass before admission measurement")
        # Docker SDK does not interpret DOCKER_CONTEXT; use the same pinned engine as CLI.
        with self.capacity_lock:
            if self.capacity_client is None:
                self.capacity_client, self.capacity_budget = sdk.DockerClient(base_url=_LOCAL_ENDPOINT), TenantBudget()
        return self.capacity_client, self.capacity_budget

    def claim_start(self, name: str) -> bool:
        if not getattr(self.args, "enforce_admission", False):
            return False
        from tenant_budget import parse_size
        client, budget = self.admission_state()
        budget.acquire(client, name, need=parse_size(self.args.reservation), configured="", fraction=.7,
                       min_available=max(parse_size("1g"), self.args.min_vm_available_mib * MIB), wait_seconds=0,
                       host_available=lambda: self.available_now() * MIB)
        return True

    # ---- measurement ----------------------------------------------------------------------------------------------
    def probe_all(self) -> dict[str, dict]:
        with ThreadPoolExecutor(max_workers=8) as pool:
            return dict(zip(self.names, pool.map(probe, self.names)))

    def flags(self) -> tuple[int, int]:
        rows = docker("inspect", "--format", "{{.Name}} {{.State.OOMKilled}} {{.RestartCount}}", *self.names).splitlines()
        if len(rows) != len(self.names):
            raise RuntimeError("Container state measurement incomplete")
        parsed = [row.split() for row in rows]
        if any(len(row) != 3 or row[1] not in ("true", "false") or not row[2].isdigit() for row in parsed):
            raise RuntimeError("Container state measurement invalid")
        if {row[0].lstrip('/') for row in parsed} != set(self.names):
            raise RuntimeError("Container state measurement names mismatch")
        return sum(row[1] == "true" for row in parsed), sum(int(row[2]) for row in parsed)

    def memory_summary(self, after: dict, before: dict | None) -> dict:
        required = {"cur", "peak", "ev_high", "ev_max", "ev_oom", "ev_oom_kill", "cpu_usage_usec", "cpu_nr_periods", "cpu_nr_throttled", "cpu_throttled_usec"}
        if (not after or any(not required.issubset(v) for v in after.values())
                or (before is not None and (set(before) != set(after) or any(not required.issubset(v) for v in before.values())))):
            raise RuntimeError("Cgroup telemetry incomplete; missing counters are unknown")
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
        try:
            with self.phase_spawn_lock:
                if self.phase_stop.is_set():
                    raise RuntimeError("Phase aborted before driver creation")
                docker("create", "--name", name, "--network", self.network, *self.label(), "-u", "10001:10001", "--cap-drop", "ALL",
                       "--memory", "512m", "--cpus", "1.0", "--pids-limit", "256", "--oom-score-adj", "1000",
                       "--read-only", "--entrypoint", "python3", self.args.image, "-c", DRIVER, json.dumps(group), json.dumps(paths), str(self.args.rounds), str(parallel))
                if self.phase_stop.is_set():
                    raise RuntimeError("Phase aborted before driver start")
                docker("start", name)
            if docker("wait", name, timeout=3600) != "0":
                raise RuntimeError("Density driver exited unsuccessfully")
            out = docker("logs", name)
            return json.loads(out.splitlines()[-1])
        finally:
            if self.owns("container", name):
                docker("rm", "-f", "-v", name, check=False)

    def run_background(self, name: str) -> dict:
        if self.runtime_mode == "api-only":
            output = docker("exec", name, "python3", "-c", BACKGROUND, timeout=900)
            return json.loads(output.splitlines()[-1])
        started = time.monotonic()
        timeout = getattr(self.args, "report_timeout", 900)
        deadline = started + timeout
        def failed(reason):
            return {"reports": {"ok": False, "out": 0, "s": round(time.monotonic()-started, 2), "err": reason, "executor": "resident_worker"}}
        try:
            if self.phase_stop.is_set():
                return failed('phase_aborted')
            queued = json.loads(docker("exec", name, "python3", "-c", RESIDENT_QUEUE, f"density-{uuid.uuid4().hex}", timeout=min(30, timeout)).splitlines()[-1])
            if not queued.get('worker_alive'):
                return failed('resident_worker_missing')
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return failed('report_deadline')
                if self.phase_stop.is_set():
                    return failed('phase_aborted')
                state = json.loads(docker("exec", name, "python3", "-c", RESIDENT_POLL, str(queued['run_id']), str(queued['definition_id']), timeout=min(30, remaining)).splitlines()[-1])
                if time.monotonic() > deadline:
                    return failed('report_deadline')
                if (state.get('run_id') != queued['run_id'] or state.get('definition_id') != queued['definition_id']):
                    return failed('report_identity_mismatch')
                if not state.get('worker_alive'):
                    return failed('resident_worker_missing')
                if state.get('status') == 'ok':
                    if not state.get('pdf_complete'):
                        return failed('completed_pdf_invalid')
                    return {"reports": {"ok": True, "out": 1, "s": round(time.monotonic()-started, 2), "executor": "resident_worker", "work_window":state.get('work_window')}}
                if state.get('status') not in ('queued', 'running'):
                    return failed('resident_report_failed')
                time.sleep(min(1, remaining))
        except Exception:
            return failed('resident_report_observation_failed')

    def run_phase(self, label: str, calc: bool, background: bool, parallel: int, paths: list[str], before: dict,
                  bg_names: list[str] | None = None, *, calibration: bool = False) -> dict:
        bg_names = bg_names if bg_names is not None else self.names
        floor, via = self.args.min_vm_available_mib, self.names[0]
        box: dict = {"min": vm_available_mib(via), "load_max": 0.0}
        stop = threading.Event()
        self.phase_stop = stop
        failure_reasons = []
        failure_lock = threading.Lock()

        def abort(reason):
            with failure_lock:
                first = not failure_reasons
                if reason not in failure_reasons:
                    failure_reasons.append(reason)
            if first:
                stop.set()
                try:
                    self.stop_owned_phase()
                except Exception:
                    failure_reasons.append("abort_failed")

        def watch():
            while not stop.is_set():
                try:
                    box["min"] = min(box["min"], vm_available_mib(via))
                    box["load_max"] = max(box["load_max"], vm_loadavg(via))
                    oom, restarts = self.flags()
                    if box["min"] < floor:
                        abort("vm_floor")
                    elif oom or restarts:
                        abort("container_oom_or_restart")
                except Exception:
                    abort("measurement_unavailable")
                stop.wait(1)
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
                futures = {pool.submit(self.run_background, name): name for name in bg_names}
                for future in as_completed(futures):
                    name = futures[future]
                    bg[name] = future.result()
                    if not bg[name].get('reports', {}).get('ok'):
                        abort('background_job_failed')
        jobs = []
        if calc:
            jobs.append(drivers)
        if background:
            jobs.append(background_pass)
        def protected(job):
            try:
                job()
            except Exception:
                abort("worker_exception")
        threads = [threading.Thread(target=protected, args=(job,)) for job in jobs]
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
        try:
            after = self.probe_all()
            oom, restarts = self.flags()
            if set(after) != set(self.names) or any("cur" not in v or "peak" not in v for v in after.values()):
                raise RuntimeError("Cgroup measurement incomplete")
            memory = self.memory_summary(after, before)
        except Exception:
            after, memory, oom, restarts = {}, None, None, None
            failure_reasons.append("measurement_unavailable")
        phase = {"phase": label, "acceptance_phase": not calibration, "seconds": round(time.time() - started, 1), "parallel_per_tenant": parallel if calc else 0,
                 "memory": memory, "vm_min_available_mib": box["min"], "vm_load1_max": box["load_max"],
                 "oom_killed_containers": oom, "restarts": restarts, "floor_breached": box["min"] < floor}
        if box["min"] < floor:
            failure_reasons.append("vm_floor")
        if oom or restarts or (memory and any(memory.get("memory_events_" + key, 0) for key in ("high", "max", "oom", "oom_kill"))):
            failure_reasons.append("memory_limit_or_restart")
        if calc:
            phase["calc"] = {**summarize(calc_ms), "requests": ok + refused + fail, "ok": ok, "refused_4xx": refused,
                             "failures_5xx_or_no_answer": fail, "http_4xx_by_endpoint": codes}
            phase["healthz"] = {**summarize(health_ms), "failures": health_fail}
            expected = len(self.names) * parallel * self.args.rounds * len(paths)
            phase["calc"]["expected_requests"] = expected
            if (set(results) != set(self.names) or len(calc_ms) != expected or ok + refused + fail != expected
                    or any(len(v.get("calc", [])) != parallel * self.args.rounds * len(paths) or not v.get("health") for v in results.values())):
                failure_reasons.append("incomplete_requests")
            if refused or fail or health_fail:
                failure_reasons.append("http_errors")
            if calc_ms and max(calc_ms) > getattr(self.args, "max_calc_ms", 20000):
                failure_reasons.append("calculation_deadline")
            if health_ms and (max(health_ms) > getattr(self.args, "max_health_ms", 5000) or summarize(health_ms)["p95_ms"] > getattr(self.args, "health_p95_ms", 1000)):
                failure_reasons.append("health_deadline")
        if background:
            expected_background = set(bg_names) if calibration else set(self.names)
            reports_s = [v["reports"]["s"] for v in bg.values() if isinstance(v.get("reports"), dict)]
            generated = sum(int(v["reports"].get("out") or 0) for v in bg.values() if isinstance(v.get("reports"), dict))
            failed = [v for v in bg.values() if ("error" in v) or not all(j.get("ok") for j in v.values())]
            phase["background"] = {"concurrency": f"{len(bg_names)} of {len(self.names)} tenants at the same time", "tenants": len(bg), "tenants_all_jobs_ok": len(bg) - len(failed), "tenants_with_failed_job": len(failed),
                                   "reports_generated": generated, "report_s_p50": round(statistics.median(reports_s), 2) if reports_s else None,
                                   "report_s_max": max(reports_s) if reports_s else None,
                                   "sync_and_agents": "not run: isolated fixture has no providers",
                                   "error_types": sorted({j.get("err") for v in bg.values() if "error" not in v for j in v.values() if j.get("err")})}
            if ((not calibration and len(bg_names) != len(self.names)) or set(bg) != expected_background or failed
                    or generated < len(expected_background) or any(not isinstance(v.get("reports"), dict) or int(v["reports"].get("out") or 0) < 1 for v in bg.values())):
                failure_reasons.append("incomplete_background")
        if calc and background:
            overlap = {}
            for name in self.names:
                calc_window = results.get(name, {}).get('calc_window')
                work_window = bg.get(name, {}).get('reports', {}).get('work_window')
                overlap[name] = combined_overlap(calc_window, work_window)
            phase['combined_overlap'] = {'per_tenant': overlap, 'proven_tenants': sum(v['proven'] for v in overlap.values()), 'required_tenants': len(self.names)}
            if not all(v['proven'] for v in overlap.values()):
                failure_reasons.append('combined_overlap_unproven')
        phase["failure_reasons"] = sorted(set(failure_reasons))
        phase["failed"] = bool(failure_reasons)
        phase["_after"] = after
        return phase

    def endpoints(self) -> list[str]:
        if getattr(self.args, "endpoints_from", None):
            demo = json.loads(Path(self.args.endpoints_from).read_text(encoding="utf-8"))
            base = sorted({f"/api/v1/{k.split('@')[0]}" for k in demo if k.split("@")[0].startswith(("dashboard/", "control/")) and "__" not in k})
            if not base or any(urlsplit(p).fragment or any(c.isspace() for c in p) for p in base):
                raise ValueError("Invalid benchmark endpoints")
            return base
        today = datetime.now(ZoneInfo("Europe/Moscow")).date()
        nm_id = self.fixture_metadata.get("sample_nm_id", 100001)
        routes = ("today", "ads", "ads/analytics", "assistant", "finance", "management-pnl", "management-pnl/monthly",
                  "production", "reports", "stock", "supply", "products/trend", "products/previous", "products/price-advice", "rnp")
        return (["/api/v1/control/data-quality"] + ["/api/v1/dashboard/" + route + "?period=90d" for route in routes]
                + [f"/api/v1/dashboard/products/heatmap?metric={metric}&period=90d" for metric in ("orders", "revenue", "ad_spend")]
                + [f"/api/v1/dashboard/management-economics?nm_id={nm_id}&start={today-timedelta(days=89)}&end={today}"])

    def stop_owned_phase(self) -> None:
        self.phase_stop.set()
        with self.phase_spawn_lock:
            candidates = docker("ps", "-q", "--filter", f"label={LABEL}={self.run_id}").split()
            owned = [candidate for candidate in candidates if self.owns("container", candidate)]
            if owned:
                docker("stop", "-t", "2", *owned, check=False, timeout=120)

    # ---- the acceptance run ---------------------------------------------------------------------------------------
    def available_now(self) -> int:
        if self.names:
            return vm_available_mib(self.names[0])
        out = docker("run", "--rm", "--name", f"{self.prefix}-memory-{uuid.uuid4().hex[:8]}", *self.label(), "--network", "none", "--memory", "128m", "--cpus", ".25", "--pids-limit", "32", "--oom-score-adj", "1000", "--read-only", "--cap-drop", "ALL", "--entrypoint", "cat", self.args.image, "/proc/meminfo")
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
        before = self.probe_all()
        phase = self.run_phase("background_calibration", False, True, 0, [], before, [first], calibration=True)
        after = phase.pop('_after')
        self.report['background_calibration'] = phase
        if phase['failed']:
            raise RuntimeError("Background report calibration failed its safety gate")
        self.bg_delta_mib = max(1.0, mib(after[first]['peak'] - before[first]['peak']))
        self.footprint = max(self.footprint or 0, phase['memory']['cur_mib_max'])

    def one_step(self, target: int, label: str) -> dict:
        step: dict = {"target": target, "label": label}
        step["admission_before_start"] = self.admission(f"{self.prefix}-t{target:02d}-{self.run_id}")
        missing = self.wait_healthy(self.grow_to(target))
        if missing:
            step["not_healthy"] = missing
            step.update(result="FAILED", failed=True, failure_reasons=["not_healthy"])
            return step
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
                phases.append({"phase": phase_label, "failed": True, "failure_reasons": ["vm_floor"], "skipped": "below floor", "vm_available_mib": available})
                step["stopped_after"] = phase_label
                break
            bg_names = None
            if bg:
                # How many background passes the stand can hold at once above the floor, from the measured per-pass peak.
                fits = int((available - self.args.min_vm_available_mib) / (self.bg_delta_mib * 1.15))
                if fits < len(self.names):
                    step.setdefault("refused_full_background_concurrency", []).append(
                        {"phase": phase_label, "requested": len(self.names), "fits_above_floor": max(0, fits), "vm_available_mib": available,
                         "per_pass_peak_delta_mib": self.bg_delta_mib, "floor_mib": self.args.min_vm_available_mib})
                    phases.append({"phase": phase_label, "failed": True, "failure_reasons": ["full_background_refused"], "skipped": "full concurrency does not fit"})
                    step["stopped_after"] = phase_label
                    break
                bg_names = self.names
            phase = self.run_phase(phase_label, calc, bg, parallel, paths, before, bg_names)
            before = phase.pop("_after")
            phases.append(phase)
            if phase.get('memory'):
                self.footprint = max(self.footprint or 0, phase['memory']['cur_mib_max'])
            print(json.dumps({"n": target, "phase": phase_label, "vm_min": phase["vm_min_available_mib"], "oom": phase["oom_killed_containers"],
                              "restarts": phase["restarts"], "p95": phase.get("calc", {}).get("p95_ms")}), flush=True)
            if phase["failed"]:
                step["stopped_after"] = phase_label
                break
        step["phases"] = phases
        step["failed"] = len(phases) != 4 or any(phase.get("failed") for phase in phases)
        step["result"] = "FAILED" if step["failed"] else "PASSED"
        return step

    def run(self) -> dict:
        assert_local_engine()
        try:
            client, _ = self.admission_state()
            info = client.info()
            self.report["hardware"] = {"cpus": info.get("NCPU"), "total_mib": int(info.get("MemTotal", 0)) // MIB}
            if ((getattr(self.args, "expected_cpus", 0) and info.get("NCPU") != self.args.expected_cpus)
                    or self.report["hardware"]["total_mib"] < getattr(self.args, "min_total_mib", 0)):
                raise RuntimeError("Stand hardware does not match acceptance geometry")
            self.pin_runtime_image(client)
            docker("network", "create", "--internal", *self.label(), self.network)
            if self.args.admission_only:
                for target in self.args.steps:
                    missing = self.wait_healthy(self.grow_to(target))
                    if missing:
                        raise RuntimeError("Admission fixture unhealthy")
                    time.sleep(10)
                    self.report["steps"].append({"target": target, "admission_for_next_tenant": self.admission(f"{self.prefix}-t{target + 1:02d}-{self.run_id}"),
                                                 "vm_available_mib": vm_available_mib(self.names[0])})
                    print(json.dumps(self.report["steps"][-1]), flush=True)
                self.report["passed"] = True
                return self.report
            for target in self.args.steps:
                ok, gate = self.can_start(max(0, target - len(self.names)))
                if not ok:
                    self.report["steps"].append({"target": target, "result": "REFUSED_NOT_RUN", "reason": "starting these tenants would leave the VM below the floor", **gate})
                    self.report["notes"].append(f"{target} tenants not run: {gate}")
                    continue
                self.report["steps"].append(self.one_step(target, str(target)))
                if self.report["steps"][-1].get("failed"):
                    for later in (t for t in self.args.steps if t > target):
                        self.report["steps"].append({"target": later, "result": "REFUSED_NOT_RUN",
                                                     "reason": "an earlier step failed acceptance"})
                    break
            self.report["passed"] = bool(self.report["steps"]) and all(s.get("result") == "PASSED" for s in self.report["steps"])
            if self.args.probe_max and self.names and self.report["passed"]:
                self.probe_maximum()
                self.report["passed"] = all(s.get("result") == "PASSED" for s in self.report["steps"])
        except (Exception, KeyboardInterrupt) as error:
            self.report["passed"] = False
            self.report["failure_type"] = type(error).__name__
            if hasattr(error, "info"):
                self.report["refused"] = {k: error.info.get(k) for k in ("reason", "budget_mib", "reserved_mib", "running_tenants")}
            recorded = {s["target"] for s in self.report["steps"]}
            for target in self.args.steps:
                if target not in recorded:
                    self.report["steps"].append({"target": target, "result": "REFUSED_NOT_RUN", "reason": "run aborted before complete measurement"})
        finally:
            self.report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
            try:
                self.cleanup()
                self.verify_cleanup()
            except Exception:
                self.report.update(passed=False, cleanup_failed=True)
            if self.capacity_client is not None:
                try:
                    self.capacity_client.close()
                except Exception:
                    self.report.update(passed=False, client_close_failed=True)
        return self.report

    def pin_runtime_image(self, client) -> None:
        image = client.images.get(self.args.image)
        attrs = image.attrs
        identifier = attrs.get("Id", "")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", identifier):
            raise RuntimeError("Immutable runtime image identity unavailable")
        self.report["runtime_image"] = {"id": identifier, "repo_digests": attrs.get("RepoDigests", []),
                                        "revision": (attrs.get("Config", {}).get("Labels") or {}).get("org.opencontainers.image.revision")}
        self.args.image = identifier

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
            docker("rm", "-f", "-v", *ids, check=False, timeout=600)
        for volume in docker("volume", "ls", "-q", "--filter", f"label={LABEL}={self.run_id}", check=False).split():
            if self.owns("volume", volume):
                docker("volume", "rm", "-f", volume, check=False)
        if self.owns("network", self.network):
            docker("network", "rm", self.network, check=False)

    def verify_cleanup(self) -> None:
        counts = {}
        for kind, args in (("container", ("ps", "-aq")), ("volume", ("volume", "ls", "-q")), ("network", ("network", "ls", "-q"))):
            candidates = docker(*args, "--filter", f"label={LABEL}={self.run_id}").split()
            owned = 0
            for reference in candidates:
                labels = ".Config.Labels" if kind == "container" else ".Labels"
                template = '{{.Name}} {{index ' + labels + ' "' + LABEL + '"}}'
                fields = docker(kind, "inspect", "--format", template, reference).split()
                if len(fields) != 2:
                    raise RuntimeError("Cleanup ownership inventory unavailable")
                if fields[0].lstrip('/').startswith(self.prefix + '-') and fields[1] == self.run_id:
                    owned += 1
            counts[kind] = owned
        self.report["cleanup_residual_counts"] = counts
        if any(counts.values()):
            raise RuntimeError("Benchmark resources remain after cleanup")

    def owns(self, kind: str, reference: str) -> bool:
        labels = ".Config.Labels" if kind == "container" else ".Labels"
        template = '{{.Name}} {{index ' + labels + ' "' + LABEL + '"}}'
        metadata = docker(kind, "inspect", "--format", template, reference, check=False).split()
        return (len(metadata) == 2 and metadata[0].lstrip("/").startswith(self.prefix + "-")
                and metadata[1] == self.run_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", required=True)
    parser.add_argument("--runtime-mode", choices=("deployed", "api-only"), default="deployed", help="deployed uses the actual image entrypoint and resident report worker; api-only is a limited comparison")
    parser.add_argument("--report-timeout", type=int, default=900)
    parser.add_argument("--steps", default="20,25,30", type=lambda s: [int(x) for x in s.split(",")])
    parser.add_argument("--probe-max", action="store_true", help="after the steps, add tenants one by one up to the floor and run the phases at that maximum")
    parser.add_argument("--admission-only", action="store_true", help="only grow to each step and record the real admission decision for the next tenant (no load)")
    parser.add_argument("--limit-mem", default="1536m")
    parser.add_argument("--reservation", default="384m")
    parser.add_argument("--min-vm-available-mib", type=int, default=2048)
    parser.add_argument("--first-guess-mib", type=int, default=600, help="conservative footprint before the first measured step")
    parser.add_argument("--settle", type=int, default=45)
    parser.add_argument("--parallel", type=int, default=4, help="in-flight requests per tenant in the parallel phases")
    parser.add_argument("--drivers", type=int, default=3, help="driver containers sharing the tenants (keeps the client from being the bottleneck)")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--heavy-variants", type=int, default=6, help="legacy compatibility argument; ignored, curated reads are not duplicated")
    parser.add_argument("--prefix", default="bench")
    parser.add_argument("--endpoints-from", default=None, help="optional JSON route keys; default is curated local reads")
    parser.add_argument("--fixture-dir", type=Path, help="frozen tenant SQLite snapshot, copied from a read-only mount")
    parser.add_argument("--synthetic-days", type=int, default=365)
    parser.add_argument("--expected-cpus", type=int, default=0)
    parser.add_argument("--min-total-mib", type=int, default=0)
    parser.add_argument("--max-calc-ms", type=int, default=20000)
    parser.add_argument("--max-health-ms", type=int, default=5000)
    parser.add_argument("--health-p95-ms", type=int, default=1000)
    parser.set_defaults(enforce_admission=True)
    parser.add_argument("--out", default="bench-tenant-density.json")
    args = parser.parse_args()
    if (not args.steps or any(n <= 0 for n in args.steps) or args.steps != sorted(set(args.steps))
            or min(args.rounds, args.parallel, args.drivers, args.synthetic_days, args.min_vm_available_mib, args.report_timeout) <= 0
            or args.synthetic_days > 1460 or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,40}", args.prefix)):
        parser.error("Invalid benchmark step/resource arguments")
    if args.fixture_dir:
        reject_fixture_links(args.fixture_dir)
        if not args.fixture_dir.is_dir():
            parser.error("Fixture must be an existing frozen directory")
    report = Bench(args).run()
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report written to {args.out}")
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    sys.exit(main())
