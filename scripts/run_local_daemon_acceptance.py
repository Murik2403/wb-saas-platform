"""Run the PDF acceptance benchmarks inside a separate, isolated, throwaway Docker daemon (Docker-in-Docker) on this Windows machine.

Why: the real 20/25/30-tenant runs need ~7-10 GiB and ~10 CPUs for an hour. A privileged container with its OWN dockerd (own
/var/lib/docker volume, network mode none, no host socket, no bind mounts, 20 GiB / 10 CPU / 16000 pids limits, restart no) keeps all
of it away from the host daemon's other containers; nothing production is reachable. The stand is the one the Docker24 audit used
(same support image, same limits); only the sources and the tenant image change.

What it does, per phase (``smoke`` = 2/3/4 tenants, ``load`` = 20/25/30 tenants with two PDF slots, ``faults`` = the four fault
scenarios on 3 and 4 tenants):
  1. preflight (Linux daemon, memory, reserved names authoritatively absent), creates its own labelled volume and daemon container;
  2. loads the tenant image and the helper image from tar files made from the HOST daemon, clones the COMMITTED source from a git
     bundle and checks it out detached at the exact HEAD (the benchmark itself refuses a dirty tree);
  3. runs the benchmarks one after another (never in parallel), each with its own act and raw sidecar, copied out unmodified with sha256;
  4. a watchdog stops exactly this daemon container if Windows free memory falls below 2 GiB or the phase deadline passes;
  5. records the inner daemon's leftover containers/volumes/networks, stops the daemon container, and (unless --keep) removes its own
     container and volume after re-checking name, label and full id, then confirms by inspect that both are gone.
Evidence goes to --evidence (outside the repository); the caller copies the acts into docs/benchmarks and commits them.

    python scripts/run_local_daemon_acceptance.py --phase smoke --head <sha> --image-id sha256:... --image-tar img.tar --helper-tar alpine.tar --bundle src.bundle --evidence C:/.../evidence
"""
from __future__ import annotations

import argparse
import ctypes
import datetime
import hashlib
import json
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_tenant_density as base  # noqa: E402  (its local-engine guard is the reviewed one)

LABEL = "mh.acceptance.local.run"
SUPPORT_IMAGE = "mh-audit-dind:20261016"
MIN_WINDOWS_FREE_BYTES = 2 * 1024 ** 3
PREFLIGHT_WINDOWS_FREE_BYTES = 7 * 1024 ** 3
DAEMON_MEMORY = "20g"
DAEMON_CPUS = "10"
DAEMON_PIDS = "16000"

PHASES = {
    "smoke": [("load", 2), ("load", 3), ("load", 4)],
    "load": [("load", 20), ("load", 25), ("load", 30)],
    "faults": [("faults", 3), ("faults", 4)],
}
GIB = 1024 ** 3


def required_windows_free(tenants: int) -> int:
    """Windows memory that must be free BEFORE a run so that its own growth cannot reach the 2 GiB floor: the Docker24 audit's 20/25/30 runs
    took the Windows free memory from 12.4 to 2.7 GiB (about 0.33 GiB per tenant), so floor + 0.33 GiB per tenant + 1 GiB of margin.
    A run that cannot start under this rule is recorded NOT_RUN instead of being cut short by the watchdog half-way."""
    return int(MIN_WINDOWS_FREE_BYTES + tenants * 0.33 * GIB + 1 * GIB)


DEADLINE_SECONDS = {"smoke": 45 * 60, "load": 150 * 60, "faults": 50 * 60}
EXEC_TIMEOUT_SECONDS = {"load": 3000, "faults": 1500}


def benchmark_command(kind: str, tenants: int, image: str, slots: int = 2) -> tuple[list[str], str]:
    """(argv inside the stand, act path inside the stand). The benchmark scripts own every criterion; nothing is passed to relax them."""
    if kind == "load":
        out = f"/results/pdf-slots-load-{tenants}.json"
        return ["python3", "scripts/bench_pdf_slots_load.py", "--image", image, "--tenants", str(tenants), "--slots", str(slots), "--out", out], out
    if kind == "faults":
        out = f"/results/pdf-faults-{tenants}.json"
        return ["python3", "scripts/bench_pdf_faults.py", "--image", image, "--tenants", str(tenants), "--slots", str(slots), "--out", out], out
    raise ValueError(kind)


class Memory(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in ("total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended")]


def windows_available() -> int:
    value = Memory()
    value.length = ctypes.sizeof(value)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
        raise RuntimeError("Windows memory observation failed")
    return value.available


def engine(*args: str, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess:
    done = subprocess.run(["docker", "--host", base._LOCAL_ENDPOINT, *args], capture_output=True, timeout=timeout)
    if check and done.returncode:
        raise RuntimeError(f"docker {' '.join(args[:3])} failed: {done.stderr.decode(errors='replace')[:250]}")
    return done


def text(done: subprocess.CompletedProcess) -> str:
    return done.stdout.decode(errors="replace").strip()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--phase", required=True, choices=sorted(PHASES))
    parser.add_argument("--head", required=True, help="the exact 40-character commit the benchmarks must run from")
    parser.add_argument("--image-id", required=True, help="the tenant image id (sha256:...) built from that commit")
    parser.add_argument("--image-tar", required=True)
    parser.add_argument("--helper-tar", required=True)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--keep", action="store_true", help="keep the daemon volume for inspection")
    args = parser.parse_args(argv)
    if len(args.head) != 40:
        raise SystemExit("--head must be the full commit id")
    base.assert_local_engine()
    run = uuid.uuid4().hex[:12]
    name, volume = f"mh-accept-{args.phase}-{run}", f"mh-accept-{args.phase}-{run}-docker"
    evidence = Path(args.evidence) / f"{args.phase}-{run}"
    evidence.mkdir(parents=True)
    deadline = time.monotonic() + DEADLINE_SECONDS[args.phase]
    stop = threading.Event()
    cid = None
    result: dict = {"run": run, "phase": args.phase, "productionTouched": False, "source": args.head, "image": args.image_id, "acts": [], "memory": [], "errors": [],
                    "scope": "isolated Linux Docker daemon (DinD); not fresh-OS recovery", "startedAt": datetime.datetime.now(datetime.timezone.utc).isoformat()}

    def save():
        (evidence / "operator.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    def owned():
        data = json.loads(text(engine("inspect", cid)))[0]
        if data["Id"] != cid or data["Config"]["Labels"].get(LABEL) != run or data["Name"] != "/" + name:
            raise RuntimeError("Ownership changed")
        host = data["HostConfig"]
        if host["NetworkMode"] != "none" or host.get("Binds") or host.get("PortBindings"):
            raise RuntimeError("Isolation changed")
        if len(data["Mounts"]) != 1 or data["Mounts"][0].get("Name") != volume:
            raise RuntimeError("Mount topology changed")
        return data

    def watchdog():
        while not stop.wait(2):
            try:
                free = windows_available()
                result["memory"].append({"at": time.time(), "windowsAvailableMiB": free // 1024 ** 2})
                if free < MIN_WINDOWS_FREE_BYTES or time.monotonic() > deadline:
                    raise RuntimeError("Windows 2 GiB memory floor or the phase deadline reached")
            except Exception as error:  # noqa: BLE001 - stop exactly this daemon, record why
                result["errors"].append(str(error))
                try:
                    owned()
                    engine("stop", "-t", "10", cid, timeout=45)
                except Exception as secondary:  # noqa: BLE001
                    result["errors"].append(str(secondary))
                return

    try:
        info = json.loads(text(engine("info", "--format", "{{json .}}")))
        if info["OSType"] != "linux" or info["MemTotal"] < 23 * 1024 ** 3 or windows_available() < PREFLIGHT_WINDOWS_FREE_BYTES:
            raise RuntimeError("Resource preflight refused")
        result["outer"] = {k: info[k] for k in ("ID", "MemTotal", "NCPU", "OSType")}
        result["foreignBefore"] = text(engine("ps", "-aq", "--no-trunc")).split()
        for kind, target in (("container", name), ("volume", volume)):
            probe = engine(kind, "inspect", target, check=False)
            if probe.returncode == 0 or b"no such" not in probe.stderr.lower():
                raise RuntimeError("Reserved name not authoritatively absent")
        engine("volume", "create", "--label", f"{LABEL}={run}", volume)
        if json.loads(text(engine("volume", "inspect", volume)))[0]["Labels"].get(LABEL) != run:
            raise RuntimeError("Created volume does not carry this run's label")
        support = json.loads(text(engine("image", "inspect", SUPPORT_IMAGE)))[0]["Id"]
        cid = text(engine("run", "-d", "--pull", "never", "--name", name, "--label", f"{LABEL}={run}", "--privileged", "--network", "none", "--cgroupns", "private",
                          "--memory", DAEMON_MEMORY, "--memory-swap", DAEMON_MEMORY, "--cpus", DAEMON_CPUS, "--pids-limit", DAEMON_PIDS, "--restart", "no",
                          "--mount", f"type=volume,src={volume},dst=/var/lib/docker", "-e", "DOCKER_TLS_CERTDIR=", "--log-driver", "none", support,
                          "dockerd", "--host=unix:///var/run/docker.sock"))
        result.update(daemonId=cid, daemonName=name, retainedVolume=volume, supportImage=support)
        owned()
        watcher = threading.Thread(target=watchdog, daemon=True)
        watcher.start()
        for _ in range(60):
            if engine("exec", cid, "docker", "info", check=False).returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Inner daemon did not start")
        result["innerEngine"] = text(engine("exec", cid, "docker", "info", "--format", "{{.ID}}"))
        assert result["innerEngine"] != info["ID"], "the inner daemon must be a different engine"
        engine("exec", cid, "mkdir", "-p", "/stand", "/results", "/work")
        engine("cp", args.bundle, cid + ":/stand/source.bundle")
        engine("cp", args.image_tar, cid + ":/stand/tenant-image.tar", timeout=300)
        engine("exec", cid, "docker", "load", "-i", "/stand/tenant-image.tar", timeout=300)
        engine("cp", args.helper_tar, cid + ":/stand/helper-image.tar")
        engine("exec", cid, "docker", "load", "-i", "/stand/helper-image.tar")
        loaded = text(engine("exec", cid, "docker", "image", "inspect", args.image_id, "--format", "{{.Id}}"))
        if loaded != args.image_id:
            raise RuntimeError(f"The loaded tenant image is {loaded}, expected {args.image_id}")
        engine("exec", cid, "git", "clone", "/stand/source.bundle", "/work/repo", timeout=120)
        engine("exec", "-w", "/work/repo", cid, "git", "checkout", "--detach", args.head)
        actual = text(engine("exec", "-w", "/work/repo", cid, "git", "rev-parse", "HEAD"))
        if actual != args.head or text(engine("exec", "-w", "/work/repo", cid, "git", "status", "--porcelain")):
            raise RuntimeError("The stand's checkout is not the clean, exact commit")
        save()
        for kind, tenants in PHASES[args.phase]:
            if result["errors"]:
                break
            owned()
            # Only BETWEEN runs, before any workload exists: release page cache of the image import. Never inside a measured window.
            reclaim = subprocess.run(["wsl", "-d", "docker-desktop", "-u", "root", "--", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"], capture_output=True, timeout=45)
            if reclaim.returncode:
                raise RuntimeError("Pre-run cache reclaim failed")
            need, free = required_windows_free(tenants), windows_available()
            if kind == "load" and tenants >= 10 and free < need:
                summary = {"kind": kind, "tenants": tenants, "verdict": "NOT_RUN", "reason": f"Windows free memory {free // 1024 ** 2} MiB is below the {need // 1024 ** 2} MiB this run needs to stay above the 2 GiB floor"}
                result["acts"].append(summary)
                save()
                print(json.dumps(summary), flush=True)
                break  # a larger run needs even more: stop here and say so
            command, path = benchmark_command(kind, tenants, args.image_id)
            print(f"Starting {kind} {tenants} in the dedicated daemon {run}", flush=True)
            done = engine("exec", "-w", "/work/repo", cid, *command, timeout=min(EXEC_TIMEOUT_SECONDS[kind], max(60, int(deadline - time.monotonic()))), check=False)
            stem = f"{kind}-{tenants}"
            (evidence / f"{stem}.log").write_bytes(done.stdout + done.stderr)
            copied = engine("cp", cid + ":" + path, str(evidence / f"{stem}.json"), check=False)
            summary = {"kind": kind, "tenants": tenants, "exit": done.returncode, "copied": copied.returncode == 0}
            if copied.returncode == 0:
                act = json.loads((evidence / f"{stem}.json").read_text(encoding="utf-8"))
                raw = engine("cp", cid + ":" + path.replace(".json", ".raw.json"), str(evidence / f"{stem}.raw.json"), check=False)
                summary.update(verdict=act.get("verdict"), failed_checks=act.get("failed_checks"), sha256=sha256_file(evidence / f"{stem}.json"), rawCopied=raw.returncode == 0,
                               head=act.get("git", {}).get("head"), dirty=act.get("git", {}).get("dirty"))
                if raw.returncode == 0:
                    summary["rawSha256"] = sha256_file(evidence / f"{stem}.raw.json")
            result["acts"].append(summary)
            save()
            print(json.dumps({k: summary.get(k) for k in ("kind", "tenants", "exit", "verdict", "sha256")}), flush=True)
        result["innerResidual"] = {"containers": text(engine("exec", cid, "docker", "ps", "-aq", "--no-trunc")).split(),
                                   "volumes": text(engine("exec", cid, "docker", "volume", "ls", "-q")).split(),
                                   "networks": [n for n in text(engine("exec", cid, "docker", "network", "ls", "--format", "{{.Name}}")).split() if n not in ("bridge", "host", "none")]}
    except Exception as error:  # noqa: BLE001
        result["errors"].append(type(error).__name__ + ": " + str(error))
    finally:
        stop.set()
        if cid:
            try:
                owned()
                engine("stop", "-t", "20", cid, timeout=60)
                result["daemonStopped"] = json.loads(text(engine("inspect", cid)))[0]["State"]["Running"] is False
            except Exception as error:  # noqa: BLE001
                result["errors"].append("Stop: " + str(error))
            if not args.keep:
                try:
                    owned()  # name, label, id and isolation re-checked immediately before the removal
                    engine("rm", "-f", cid, timeout=60)
                    engine("volume", "rm", volume, timeout=60)
                    gone = {kind: engine(kind, "inspect", target, check=False) for kind, target in (("container", name), ("volume", volume))}
                    result["standRemoved"] = all(p.returncode != 0 and b"no such" in p.stderr.lower() for p in gone.values())
                except Exception as error:  # noqa: BLE001
                    result["errors"].append("Remove: " + str(error))
        result["completedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save()
        print(json.dumps({"evidence": str(evidence), "errors": result["errors"], "acts": [{k: a.get(k) for k in ("kind", "tenants", "verdict")} for a in result["acts"]]}), flush=True)
    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
