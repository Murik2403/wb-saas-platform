"""Isolated tenant-density benchmark: N real tenant containers on a LOCAL Docker engine, synthetic data, no production.

What it does
  * starts N tenant containers from one image with the provisioner's hardening (read-only root, cap_drop ALL, uid 10001,
    hard mem limit, soft reservation) on a private internal network, each with its own volume seeded by the project's
    fictional demo seeder (demo mode: read-only, no marketplace calls, no credentials);
  * measures memory per container (cgroup memory.current / peak / anon) and the VM's MemAvailable at each step;
  * drives synthetic read traffic (the dashboard GET endpoints captured in mobile/scripts/demo-cabinet.json) in waves
    and records latency, errors, peak memory and OOM kills;
  * stops growing the moment the VM's MemAvailable would fall below --min-vm-available-mib, and says so.

Safety: it refuses a non-local engine (DOCKER_HOST set, or a context that is not a local desktop engine), only ever
touches objects named ``<prefix>-*`` that carry the label ``wb-saas.benchmark=<run id>``, makes its containers the
first choice of the kernel OOM killer (oom_score_adj 1000) and removes everything it created when it ends.

    python scripts/bench_tenant_density.py --image bench-tenant:overlay --steps 1,5,10,15,20 --out bench.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

LABEL = "wb-saas.benchmark"
HELPER_IMAGE = "alpine:3.20"  # already present on a machine that runs the provisioner; used only to chown a new volume


def docker(*args: str, check: bool = True, timeout: int = 300) -> str:
    done = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if check and done.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args[:3])} failed: {done.stderr.strip()[:300]}")
    return done.stdout.strip()


def assert_local_engine() -> None:
    if os.environ.get("DOCKER_HOST"):
        raise SystemExit("Refusing to run: DOCKER_HOST is set, the engine may be remote.")
    context = docker("context", "show")
    endpoint = docker("context", "inspect", "--format", "{{.Endpoints.docker.Host}}")
    if any(word in (context + endpoint).lower() for word in ("ssh://", "tcp://", "staging", "prod", "marketshelper")):
        raise SystemExit(f"Refusing to run: context {context!r} / {endpoint!r} does not look like a local engine.")


def vm_available_mib(probe: str) -> int:
    meminfo = docker("exec", probe, "cat", "/proc/meminfo")
    for line in meminfo.splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    raise RuntimeError("MemAvailable not found")


def cgroup_numbers(name: str) -> dict[str, int]:
    script = ("f=/sys/fs/cgroup; if [ -f $f/memory.current ]; then "
              "echo cur $(cat $f/memory.current); echo peak $(cat $f/memory.peak 2>/dev/null || echo 0); "
              "awk '$1==\"anon\"{print \"anon\",$2} $1==\"file\"{print \"file\",$2}' $f/memory.stat; "
              "else echo cur $(cat $f/memory/memory.usage_in_bytes); echo peak $(cat $f/memory/memory.max_usage_in_bytes); fi")
    out = docker("exec", name, "sh", "-c", script, check=False)
    values = {}
    for line in out.splitlines():
        key, _, number = line.partition(" ")
        if number.strip().isdigit():
            values[key] = int(number)
    return values


def mib(value: int) -> float:
    return round(value / 1048576, 1)


DRIVER = r'''
import json, sys, time, urllib.request, urllib.error, threading
targets, paths, rounds = json.loads(sys.argv[1]), json.loads(sys.argv[2]), int(sys.argv[3])
results = {}
def hit(host):
    stats = {"ok": 0, "err": 0, "absent": 0, "ms": []}
    for _ in range(rounds):
        for path in paths:
            started = time.perf_counter()
            try:
                req = urllib.request.Request(f"http://{host}:8501{path}", headers={"Host": host})
                with urllib.request.urlopen(req, timeout=120) as response:
                    response.read()
                stats["ok"] += 1
            except urllib.error.HTTPError as exc:
                stats["absent" if exc.code == 404 else "err"] += 1  # 404 = optional source not in the demo data, not a failure
            except Exception:
                stats["err"] += 1
            stats["ms"].append((time.perf_counter() - started) * 1000)
    results[host] = stats
threads = [threading.Thread(target=hit, args=(h,)) for h in targets]
[t.start() for t in threads]; [t.join() for t in threads]
print(json.dumps(results))
'''


class Bench:
    def __init__(self, args):
        self.args, self.run_id = args, uuid.uuid4().hex[:8]
        self.prefix = args.prefix
        self.network = f"{self.prefix}-net-{self.run_id}"
        self.names: list[str] = []
        self.volumes: list[str] = []
        self.report: dict = {"image": args.image, "run": self.run_id, "limits": {"mem": args.limit_mem, "reservation": args.reservation},
                             "steps": [], "notes": []}
        self.lock = threading.Lock()

    def label(self) -> list[str]:
        return ["--label", f"{LABEL}={self.run_id}"]

    def tenant(self, index: int) -> tuple[str, str]:
        return f"{self.prefix}-t{index:02d}-{self.run_id}", f"{self.prefix}-data-{index:02d}-{self.run_id}"

    def create_tenant(self, index: int) -> str:
        name, volume = self.tenant(index)
        docker("volume", "create", *self.label(), volume)
        self.volumes.append(volume)
        docker("run", "--rm", "-u", "0", "--network", "none", "--cap-drop", "ALL", "--cap-add", "CHOWN", "-v", f"{volume}:/data",
               HELPER_IMAGE, "chown", "10001:10001", "/data")
        seed_env = ["-e", "WB_SAAS_IS_DEMO=1", "-e", "WB_SAAS_TENANT_SLUG=demo", "-e", "WB_SAAS_DATA_DIR=/app/data"]  # the seeder only runs for slug demo
        docker("run", "--rm", "--network", "none", "-u", "10001:10001", "--read-only", "--cap-drop", "ALL",
               "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "-v", f"{volume}:/app/data", *seed_env,
               "--entrypoint", "python3", "-w", "/app/tenant-app", self.args.image, "seed_demo_data.py", timeout=300)
        tmpfs = []
        for path, size in (("/tmp", "64m"), ("/var/lib/nginx", "64m"), ("/var/cache/nginx", "16m"), ("/var/log/nginx", "16m"), ("/run", "16m")):
            tmpfs += ["--tmpfs", f"{path}:rw,noexec,nosuid,size={size},uid=10001,gid=10001,mode=0777"]
        docker("run", "-d", "--name", name, "--network", self.network, "--network-alias", name, *self.label(),
               "-u", "10001:10001", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
               "--memory", self.args.limit_mem, "--memory-reservation", self.args.reservation, "--cpus", "1.0",
               "--oom-score-adj", "1000", "-v", f"{volume}:/app/data", *tmpfs,
               "-e", "WB_SAAS_IS_DEMO=1", "-e", "WB_SAAS_TENANT_SLUG=demo", "-e", "WB_SAAS_TELEGRAM_ENABLED=0",
               "-e", f"MH_ALLOWED_HOSTS=localhost,127.0.0.1,{name}", "-e", "WB_SAAS_INTERNAL_API_URL=http://127.0.0.1:9",
               "-e", "WB_SAAS_INTERNAL_API_SECRET=bench-only-secret", "-e", "MPLCONFIGDIR=/tmp/matplotlib", "-e", "HOME=/app",
               self.args.image)
        self.names.append(name)
        return name

    def wait_healthy(self, names: list[str], timeout: int = 240) -> list[str]:
        deadline, pending = time.time() + timeout, set(names)
        while pending and time.time() < deadline:
            for name in list(pending):
                state = docker("inspect", "--format", "{{.State.Health.Status}} {{.State.Running}}", name, check=False)
                if state.startswith("healthy"):
                    pending.discard(name)
            time.sleep(3)
        return sorted(pending)

    def snapshot(self, label: str) -> dict:
        probe = self.names[0]
        with_stats = docker("stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}", *self.names)
        cg = {name: cgroup_numbers(name) for name in self.names}
        usage = []
        for line in with_stats.splitlines():
            name, _, rest = line.partition("\t")
            number = rest.split("/")[0].strip()
            unit = {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024}
            for suffix, factor in unit.items():
                if number.endswith(suffix):
                    usage.append(float(number[: -len(suffix)]) * factor)
        anon = [mib(v.get("anon", 0)) for v in cg.values() if v.get("anon")]
        peak = [mib(v.get("peak", 0)) for v in cg.values()]
        oom = sum(docker("inspect", "--format", "{{.State.OOMKilled}}", n, check=False) == "true" for n in self.names)
        restarts = sum(int(docker("inspect", "--format", "{{.RestartCount}}", n, check=False) or 0) for n in self.names)
        return {"label": label, "tenants": len(self.names), "docker_stats_mib_avg": round(statistics.mean(usage), 1) if usage else None,
                "docker_stats_mib_max": round(max(usage), 1) if usage else None, "docker_stats_mib_total": round(sum(usage), 1),
                "anon_mib_avg": round(statistics.mean(anon), 1) if anon else None, "peak_mib_avg": round(statistics.mean(peak), 1),
                "peak_mib_max": max(peak), "vm_available_mib": vm_available_mib(probe), "oom_killed": oom, "restarts": restarts}

    def load(self, names: list[str], paths: list[str], rounds: int) -> dict:
        driver = f"{self.prefix}-driver-{self.run_id}"
        output = docker("run", "--rm", "--name", driver, "--network", self.network, *self.label(), "-u", "10001:10001",
                        "--cap-drop", "ALL", "--read-only", "--entrypoint", "python3", self.args.image,
                        "-c", DRIVER, json.dumps(names), json.dumps(paths), str(rounds), timeout=1800)
        return json.loads(output.splitlines()[-1])

    def watch_vm(self, stop: threading.Event, floor: int, box: dict) -> None:
        while not stop.is_set():
            try:
                available = vm_available_mib(self.names[0])
                box["min"] = min(box.get("min", available), available)
                if available < floor:
                    box["breached"] = True
            except Exception:
                pass
            stop.wait(2)

    def endpoints(self) -> list[str]:
        demo = json.loads(Path(self.args.endpoints_from).read_text(encoding="utf-8"))
        paths = []
        for key in demo:
            base = key.split("@")[0]
            if base.startswith(("dashboard/", "control/")) and "__" not in base:
                paths.append(f"/api/v1/{base}")
        unique = sorted(set(paths))
        return unique + [p + "?period=90d" for p in unique[: self.args.heavy_variants]]

    def run(self) -> dict:
        assert_local_engine()
        floor, paths = self.args.min_vm_available_mib, self.endpoints()
        self.report["endpoints"] = len(paths)
        docker("network", "create", "--internal", *self.label(), self.network)
        started = 0
        try:
            for target in self.args.steps:
                if target <= started:
                    continue
                per = (self.report["steps"][-1]["anon_mib_avg"] if self.report["steps"] else None) or 250
                if self.report["steps"] and vm_available_mib(self.names[0]) - (target - started) * per * 1.3 < floor:
                    self.report["notes"].append(f"stopped before {target} tenants: projected VM available below {floor} MiB")
                    break
                new = [self.create_tenant(i) for i in range(started + 1, target + 1)]
                started = target
                missing = self.wait_healthy(new)
                if missing:
                    self.report["notes"].append(f"not healthy within the timeout: {missing}")
                time.sleep(self.args.settle)
                step = self.snapshot(f"{target} idle")
                # synthetic read load in waves, so that the VM is never asked to hold every tenant's peak at once
                box: dict = {}
                stop = threading.Event()
                watcher = threading.Thread(target=self.watch_vm, args=(stop, floor, box), daemon=True)
                watcher.start()
                latencies, errors, total, absent = [], 0, 0, 0
                for offset in range(0, len(self.names), self.args.wave):
                    wave = self.names[offset: offset + self.args.wave]
                    if box.get("breached"):
                        self.report["notes"].append("load aborted: VM available memory fell below the floor")
                        break
                    result = self.load(wave, paths, self.args.rounds)
                    for stats in result.values():
                        latencies += stats["ms"]
                        errors += stats["err"]
                        total += stats["ok"] + stats["err"]
                        absent += stats["absent"]
                stop.set()
                watcher.join(timeout=5)
                loaded = self.snapshot(f"{target} after load")
                if latencies:
                    latencies.sort()
                    loaded.update(requests=total, errors=errors, absent_optional=absent, p50_ms=round(latencies[len(latencies) // 2]),
                                  p95_ms=round(latencies[int(len(latencies) * 0.95) - 1]), max_ms=round(latencies[-1]))
                loaded["vm_min_available_during_load_mib"] = box.get("min")
                step["after_load"] = loaded
                step["anon_mib_avg"] = step["anon_mib_avg"] or step["docker_stats_mib_avg"]
                self.report["steps"].append(step)
                print(json.dumps({k: step[k] for k in ("label", "docker_stats_mib_avg", "anon_mib_avg", "vm_available_mib")}), flush=True)
        finally:
            self.cleanup()
        return self.report

    def cleanup(self) -> None:
        ids = docker("ps", "-aq", "--filter", f"label={LABEL}={self.run_id}", check=False).split()
        if ids:
            docker("rm", "-f", *ids, check=False, timeout=600)
        for volume in docker("volume", "ls", "-q", "--filter", f"label={LABEL}={self.run_id}", check=False).split():
            docker("volume", "rm", "-f", volume, check=False)
        docker("network", "rm", self.network, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", required=True)
    parser.add_argument("--steps", default="1,5,10,15,20", type=lambda s: [int(x) for x in s.split(",")])
    parser.add_argument("--limit-mem", default="1536m")
    parser.add_argument("--reservation", default="384m")
    parser.add_argument("--min-vm-available-mib", type=int, default=1500)
    parser.add_argument("--settle", type=int, default=45, help="seconds of idle before measuring")
    parser.add_argument("--wave", type=int, default=5, help="tenants driven at the same time")
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
