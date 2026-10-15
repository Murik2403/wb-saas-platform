"""Image, topology, job identity and cleanup ownership of the PDF slots load scenario. No Docker is needed: the engine is a fake."""
import copy
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / "provisioner"))
spec = importlib.util.spec_from_file_location("bench_pdf_slots_load_attest", SCRIPTS / "bench_pdf_slots_load.py")
load = importlib.util.module_from_spec(spec)
spec.loader.exec_module(load)
pdfb = load.pdfb

NAMES = ["bench-load-t01-ab12cd34", "bench-load-t02-ab12cd34", "bench-load-t03-ab12cd34"]
SLOT_VOLUME = "bench-load-slots-ab12cd34"
IMAGE_ID = "sha256:" + "a" * 64
METRICS = [f"metric_{i:02d}" for i in range(21)]


def inspect_row(name, *, image=IMAGE_ID, started="2026-10-15T10:00:00Z"):
    return {"Image": image, "Config": {"User": "10001:10001", "Env": ["WB_SAAS_PDF_SLOTS=2", "WB_SAAS_PDF_SLOT_DIR=/pdf-slots", "HOME=/app"]},
            "HostConfig": {"ReadonlyRootfs": True, "Privileged": False, "CapDrop": ["ALL"], "CapAdd": None, "Memory": load.TENANT_MEMORY_BYTES},
            "State": {"Running": True, "OOMKilled": False, "RestartCount": 0, "StartedAt": started},
            "Mounts": [{"Destination": "/pdf-slots", "Type": "volume", "Name": SLOT_VOLUME, "RW": True},
                       {"Destination": "/app/data", "Type": "volume", "Name": f"{name}-data", "RW": True},
                       {"Destination": "/tmp", "Type": "tmpfs", "RW": True}]}


def processes():
    return [{"pid": "7", "user": "10001", "args": "python3 -m uvicorn api_server:app"}, {"pid": "9", "user": "10001", "args": "python3 background_worker.py"},
            {"pid": "1", "user": "10001", "args": "nginx: master process nginx"}, {"pid": "22", "user": "10001", "args": "nginx: worker process"}]


def locks(slots=2):
    return {"dir": {"dev": 66, "ino": 100, "uid": 10001, "mode": 0o770},
            "files": {f"pdf-slot-{i:02d}.lock": {"dev": 66, "ino": 200 + i, "uid": 10001, "mode": 0o660} for i in range(slots)}}


def topology():
    before = {n: {"inspect": inspect_row(n), "processes": processes()} for n in NAMES}
    after = {n: {"inspect": inspect_row(n), "processes": processes(), "locks": locks()} for n in NAMES}
    return before, after


EXPECTED = {"image_id": IMAGE_ID, "slot_volume": SLOT_VOLUME, "slots": 2, "memory_bytes": load.TENANT_MEMORY_BYTES}


def topo_failures(mutate=None):
    before, after = topology()
    if mutate:
        mutate(before, after)
    return load.topology_failures(NAMES, EXPECTED, before, after)


def test_a_correct_topology_has_no_failures():
    assert topo_failures() == []


@pytest.mark.parametrize("name,mutate,fragment", [
    ("another image", lambda b, a: b[NAMES[1]]["inspect"].update(Image="sha256:" + "b" * 64), "runs image"),
    ("root user", lambda b, a: b[NAMES[0]]["inspect"]["Config"].update(User="0:0"), "runs as"),
    ("writable root", lambda b, a: b[NAMES[0]]["inspect"]["HostConfig"].update(ReadonlyRootfs=False), "read-only"),
    ("privileged", lambda b, a: b[NAMES[0]]["inspect"]["HostConfig"].update(Privileged=True), "unprivileged"),
    ("capability added", lambda b, a: b[NAMES[0]]["inspect"]["HostConfig"].update(CapAdd=["SYS_ADMIN"]), "unprivileged"),
    ("raised memory limit", lambda b, a: b[NAMES[0]]["inspect"]["HostConfig"].update(Memory=2 * load.TENANT_MEMORY_BYTES), "memory limit"),
    ("wrong slot count env", lambda b, a: b[NAMES[2]]["inspect"]["Config"].update(Env=["WB_SAAS_PDF_SLOTS=8", "WB_SAAS_PDF_SLOT_DIR=/pdf-slots"]), "slot environment"),
    ("no slot dir env", lambda b, a: b[NAMES[2]]["inspect"]["Config"].update(Env=["WB_SAAS_PDF_SLOTS=2"]), "slot environment"),
    ("slot volume missing", lambda b, a: b[NAMES[1]]["inspect"]["Mounts"].pop(0), "shared slot volume"),
    ("slot volume is another volume", lambda b, a: b[NAMES[1]]["inspect"]["Mounts"][0].update(Name="other"), "shared slot volume"),
    ("slot volume read-only", lambda b, a: b[NAMES[1]]["inspect"]["Mounts"][0].update(RW=False), "shared slot volume"),
    ("slot mount is a bind", lambda b, a: b[NAMES[1]]["inspect"]["Mounts"][0].update(Type="bind"), "shared slot volume"),
    ("data volume of another tenant", lambda b, a: b[NAMES[1]]["inspect"]["Mounts"][1].update(Name=f"{NAMES[0]}-data"), "own its data volume"),
    ("unexpected mount", lambda b, a: b[NAMES[1]]["inspect"]["Mounts"].append({"Destination": "/host", "Type": "bind", "RW": True}), "unexpected mounts"),
    ("no worker process", lambda b, a: b[NAMES[0]].update(processes=[p for p in processes() if "worker" not in p["args"] or "nginx" in p["args"]]), "worker process missing"),
    ("api restarted", lambda b, a: a[NAMES[0]]["processes"][0].update(pid="99"), "api process missing or restarted"),
    ("worker restarted", lambda b, a: a[NAMES[0]]["processes"][1].update(pid="99"), "worker process missing or restarted"),
    ("container restarted", lambda b, a: a[NAMES[0]]["inspect"]["State"].update(StartedAt="2026-10-15T10:05:00Z"), "restarted, stopped or OOM"),
    ("container restart count grew", lambda b, a: a[NAMES[0]]["inspect"]["State"].update(RestartCount=1), "restarted, stopped or OOM"),
    ("container oom killed", lambda b, a: a[NAMES[0]]["inspect"]["State"].update(OOMKilled=True), "restarted, stopped or OOM"),
    ("container stopped", lambda b, a: a[NAMES[0]]["inspect"]["State"].update(Running=False), "restarted, stopped or OOM"),
    ("no lock identity", lambda b, a: a[NAMES[1]].pop("locks"), "no lock identity"),
    ("too few lock files", lambda b, a: a[NAMES[1]]["locks"]["files"].pop("pdf-slot-01.lock"), "lock files"),
    ("too many lock files", lambda b, a: a[NAMES[1]]["locks"]["files"].update({"pdf-slot-02.lock": {"dev": 66, "ino": 5, "uid": 10001, "mode": 0o660}}), "lock files"),
    ("lock owned by root", lambda b, a: a[NAMES[1]]["locks"]["files"]["pdf-slot-00.lock"].update(uid=0), "not owned by 10001"),
    ("directory world accessible", lambda b, a: a[NAMES[1]]["locks"]["dir"].update(mode=0o777), "accessible to others"),
    ("different directory inode", lambda b, a: a[NAMES[2]]["locks"]["dir"].update(ino=101), "not the same directory"),
    ("different device", lambda b, a: a[NAMES[2]]["locks"]["dir"].update(dev=67), "not the same directory"),
    ("different lock file inode", lambda b, a: a[NAMES[2]]["locks"]["files"]["pdf-slot-01.lock"].update(ino=999), "not the same files"),
    ("no inspect data", lambda b, a: b.pop(NAMES[1]), "no inspect data"),
])
def test_each_topology_property_alone_fails(name, mutate, fragment):
    failed = topo_failures(mutate)
    assert failed, name
    assert any(fragment in line for line in failed), (name, failed)


def test_two_tenants_sharing_a_data_volume_is_a_failure():
    def share(before, after):
        before[NAMES[1]]["inspect"]["Mounts"][1]["Name"] = f"{NAMES[1]}-data"
        after[NAMES[1]]["inspect"]["Mounts"][1]["Name"] = f"{NAMES[1]}-data"
        before[NAMES[2]]["inspect"]["Mounts"][1]["Name"] = f"{NAMES[1]}-data"
    assert topo_failures(share)


def test_process_roles_read_the_real_command_lines():
    roles = load.process_roles(processes())
    assert roles == {"api": ["7"], "worker": ["9"], "nginx": ["1"]}


# ---- image --------------------------------------------------------------------------------------------------------------

BLOBS = {path: f"{i:064x}" for i, (path, _) in enumerate(load.SOURCE_FILES)}
TREES = {"head_tenant_app": "t1", "head_tenant_api": "t2", "built_from": "c" * 40, "built_from_tenant_app": "t1", "built_from_tenant_api": "t2"}


def image_check(trees=None, hashes=None):
    return load.image_failures({"id": IMAGE_ID}, trees or TREES, hashes if hashes is not None else {n: dict(BLOBS) for n in NAMES}, BLOBS)


def test_an_image_built_from_the_same_sources_passes():
    assert image_check() == []


@pytest.mark.parametrize("name,trees,hashes", [
    ("image without a revision", dict(TREES, built_from=None), None),
    ("tenant-app tree differs", dict(TREES, built_from_tenant_app="other"), None),
    ("tenant-api tree differs", dict(TREES, built_from_tenant_api="other"), None),
    ("tree of the revision unknown", dict(TREES, built_from_tenant_app=None, head_tenant_app=None), None),
    ("no hashes read", None, {}),
    ("one file differs in one tenant", None, {NAMES[0]: dict(BLOBS), NAMES[1]: dict(BLOBS, **{load.SOURCE_FILES[0][0]: "f" * 64}), NAMES[2]: dict(BLOBS)}),
    ("a file is missing in a tenant", None, {NAMES[0]: {k: v for k, v in BLOBS.items() if k != load.SOURCE_FILES[0][0]}, NAMES[1]: dict(BLOBS), NAMES[2]: dict(BLOBS)}),
])
def test_each_image_gap_fails(name, trees, hashes):
    assert image_check(trees, hashes), name


def test_an_image_without_an_id_fails():
    assert load.image_failures({"id": ""}, TREES, {n: dict(BLOBS) for n in NAMES}, BLOBS)


# ---- jobs ---------------------------------------------------------------------------------------------------------------

def jobs_and_runs():
    jobs = {n: {"run_id": 10 + i, "definition_id": 20 + i, "metrics": sorted(METRICS), "before": {"runs": 0, "definitions": 0}} for i, n in enumerate(NAMES)}
    runs = {n: [{"id": 10 + i, "definition_id": 20 + i, "status": "ok", "delivery_requested": 0}] for i, n in enumerate(NAMES)}
    return jobs, runs


def job_check(mutate=None):
    jobs, runs = jobs_and_runs()
    if mutate:
        mutate(jobs, runs)
    return load.job_failures(NAMES, jobs, runs, METRICS)


def test_jobs_matching_their_enqueue_ids_pass():
    assert job_check() == []


@pytest.mark.parametrize("name,mutate,fragment", [
    ("no enqueue ids", lambda j, r: j.pop(NAMES[0]), "no enqueue ids"),
    ("run id not an int", lambda j, r: j[NAMES[0]].update(run_id="10"), "no enqueue ids"),
    ("only 20 of 21 metrics", lambda j, r: j[NAMES[0]].update(metrics=sorted(METRICS)[:20]), "report metric codes"),
    ("another metric set", lambda j, r: j[NAMES[0]].update(metrics=sorted(METRICS[:20] + ["other"])), "report metric codes"),
    ("no final state", lambda j, r: r.pop(NAMES[0]), "no final state"),
    ("the enqueued run is missing", lambda j, r: r[NAMES[0]][0].update(id=999), "not found exactly once"),
    ("a different run finished instead", lambda j, r: r[NAMES[0]].append(dict(r[NAMES[0]][0], id=500)) or r[NAMES[0]][0].update(status="error"), "ended error"),
    ("run belongs to another definition", lambda j, r: r[NAMES[0]][0].update(definition_id=77), "ended ok (definition 77"),
    ("a delivery was requested", lambda j, r: r[NAMES[0]][0].update(delivery_requested=1), "delivery"),
    ("a duplicate run of the same definition", lambda j, r: r[NAMES[0]].append({"id": 11 + 100, "definition_id": 20, "status": "ok", "delivery_requested": 0}), "duplicate or foreign"),
    ("a foreign extra run", lambda j, r: r[NAMES[0]].append({"id": 300, "definition_id": 55, "status": "ok", "delivery_requested": 0}), "duplicate or foreign"),
])
def test_each_job_identity_gap_fails(name, mutate, fragment):
    failed = job_check(mutate)
    assert failed and any(fragment in line for line in failed), (name, failed)


def test_a_run_that_was_not_the_last_one_is_still_matched_by_id():
    def reorder(jobs, runs):
        runs[NAMES[0]].insert(0, {"id": 1, "definition_id": 1, "status": "ok", "delivery_requested": 0})
        jobs[NAMES[0]]["before"]["runs"] = 1
    assert job_check(reorder) == []


# ---- cleanup ownership with a fake engine -------------------------------------------------------------------------------------

class FakeEngine:
    """Containers/volumes/networks with full IDs, names and labels; `docker` calls are answered from it."""

    def __init__(self, preexisting=()):
        self.objects = {}
        self.calls = []
        for kind, ident, name, label in preexisting:
            self.objects[(kind, ident)] = {"name": name, "label": label, "state": "true 2026-01-01T00:00:00Z"}

    def add(self, kind, ident, name, label):
        self.objects[(kind, ident)] = {"name": name, "label": label, "state": "true 2026-10-15T00:00:00Z"}
        return ident

    def docker(self, *args, check=True, timeout=300):
        self.calls.append(args)
        head = args[0]
        if head == "ps":
            wanted = next((a.split("=", 1)[1] for a in args if a.startswith("label=")), None) if "--filter" in args else None
            return "\n".join(i for (k, i), o in self.objects.items() if k == "container" and (wanted is None or o["label"] == wanted.split("=", 1)[1]))
        if head == "volume" and args[1] == "ls":
            wanted = next((a.split("=", 2)[2] for a in args if a.startswith("label=")), None)
            return "\n".join(i for (k, i), o in self.objects.items() if k == "volume" and (wanted is None or o["label"] == wanted))
        if head == "network" and args[1] == "ls":
            wanted = next((a.split("=", 2)[2] for a in args if a.startswith("label=")), None)
            return "\n".join(i for (k, i), o in self.objects.items() if k == "network" and (wanted is None or o["label"] == wanted))
        if head == "inspect" and "{{.Id}} {{.State.Running}}" in " ".join(args):
            ids = [a for a in args if (("container", a) in self.objects)]
            return "\n".join(f"{i} {self.objects[('container', i)]['state']}" for i in ids)
        if head in ("container", "volume", "network") and args[1] == "inspect":
            target = args[-1]
            for (kind, ident), obj in self.objects.items():
                if kind == head and target in (ident, obj["name"]):
                    return f"{ident}|{obj['name']}|{obj['label']}" if "Labels" in " ".join(args) or "Config.Labels" in " ".join(args) else obj["name"]
            return ""
        if head == "rm":
            for ident in args[1:]:
                self.objects.pop(("container", ident), None)
            return ""
        if head in ("volume", "network") and args[1] == "rm":
            target = args[-1]
            for key in [k for k, o in self.objects.items() if k[0] == head and target in (k[1], o["name"])]:
                self.objects.pop(key)
            return ""
        return ""


def make_arm(engine, prefix="bench"):
    args = type("A", (), {"prefix": prefix, "tenants": 1, "image": "img"})()
    arm = pdfb.Arm(args, "load", 2)
    return arm


def test_cleanup_removes_only_recorded_objects_by_full_id_and_reports_foreign_state():
    foreign = ("container", "f" * 64, "someone-elses", "")
    engine = FakeEngine([foreign, ("volume", "customer-data", "customer-data", ""), ("network", "n" * 64, "customer-net", "")])
    with patch.object(pdfb.base, "docker", engine.docker):
        arm = make_arm(engine)
        arm.foreign_before = pdfb.inventory()
        cid = engine.add("container", "a" * 64, arm.names[0], arm.run_id)
        arm.adopt("container", cid, arm.names[0])
        arm.adopt("volume", engine.add("volume", arm.slot_volume, arm.slot_volume, arm.run_id), arm.slot_volume)
        arm.adopt("network", engine.add("network", "b" * 64, arm.network, arm.run_id), arm.network)
        arm.stop()
        report = arm.cleanup_report()
    assert report["owned"] == {"container": 1, "volume": 1, "network": 1} and report["removed_by_full_id"] == 3 and report["removed_ids_all_full"]
    assert report["owned_remaining"] == {} and report["unexpected_labelled"] == [] and report["foreign_missing"] == []
    assert ("container", "f" * 64) in engine.objects and ("volume", "customer-data") in engine.objects  # the foreign ones are untouched
    assert load.cleanup_failures(report) == []
    removed = [c for c in engine.calls if c[0] == "rm"]
    assert removed == [("rm", "-f", "-v", "a" * 64)], removed  # by full ID, never by label filter or name


def test_an_object_carrying_the_run_label_that_was_not_recorded_is_reported_and_not_removed():
    engine = FakeEngine()
    with patch.object(pdfb.base, "docker", engine.docker):
        arm = make_arm(engine)
        arm.foreign_before = pdfb.inventory()
        arm.adopt("container", engine.add("container", "a" * 64, arm.names[0], arm.run_id), arm.names[0])
        engine.add("container", "c" * 64, "stray", arm.run_id)
        arm.stop()
        report = arm.cleanup_report()
    assert ("container", "c" * 64) in engine.objects
    assert report["unexpected_labelled"] == ["container:" + "c" * 64] and load.cleanup_failures(report)


def test_an_object_whose_label_no_longer_matches_is_not_removed():
    engine = FakeEngine()
    with patch.object(pdfb.base, "docker", engine.docker):
        arm = make_arm(engine)
        arm.foreign_before = pdfb.inventory()
        arm.adopt("container", engine.add("container", "a" * 64, arm.names[0], "somebody-else"), arm.names[0])
        arm.stop()
        report = arm.cleanup_report()
    assert ("container", "a" * 64) in engine.objects and report["owned_remaining"] == {"container": ["a" * 64]} and load.cleanup_failures(report)


def test_a_vanished_foreign_object_is_a_cleanup_failure():
    engine = FakeEngine([("container", "f" * 64, "customer", "")])
    with patch.object(pdfb.base, "docker", engine.docker):
        arm = make_arm(engine)
        arm.foreign_before = pdfb.inventory()
        arm.adopt("container", engine.add("container", "a" * 64, arm.names[0], arm.run_id), arm.names[0])
        arm.stop()
        engine.objects.pop(("container", "f" * 64))
        report = arm.cleanup_report()
    assert report["foreign_missing"] == ["f" * 64] and load.cleanup_failures(report)


def test_a_short_id_is_never_used_to_remove_anything():
    engine = FakeEngine()
    with patch.object(pdfb.base, "docker", engine.docker):
        arm = make_arm(engine)
        arm.foreign_before = pdfb.inventory()
        arm.adopt("container", engine.add("container", "abc123", arm.names[0], arm.run_id), arm.names[0])
        arm.stop()
    assert ("container", "abc123") in engine.objects  # a recorded ID that is not a full 64-hex ID is refused


# ---- the scenario's refusal and failure paths -----------------------------------------------------------------------------------

def scenario_args(**overrides):
    values = dict(allow_dirty=False, tenants=2, slots=2, pdf_seconds=60, margin_seconds=60, health_p95_ms=3000, image="x", prefix="bench", settle=0, api_parallel=3,
                  api_pdf_every=20.0, drivers=2, grace_seconds=1, heavy_variants=1, pdf_peak_delta_mib=250, api_delta_mib=40, endpoints_from="unused",
                  min_vm_available_mib=1500, expect_metrics=21)
    values.update(overrides)
    return load.argparse.Namespace(**values)


class StubArm:
    def __init__(self, *a, **k):
        self.names, self.run_id, self.slot_volume, self.network = NAMES[:2], "ab12cd34", SLOT_VOLUME, "net"
        self.stopped = False

    def start(self): pass
    def wait_healthy(self): pass
    def stop(self): self.stopped = True
    def cleanup_report(self): return {"owned": {"container": 1}, "removed_by_full_id": 1, "removed_ids_all_full": True, "owned_remaining": {}, "unexpected_labelled": [], "foreign_missing": []}


def run_scenario(admission, **patches):
    stub = StubArm()
    image = ({"id": IMAGE_ID, "labels": {}}, TREES, {}, BLOBS, METRICS)
    with patch.object(load, "git_state", return_value={"head": "abc", "branch": "x", "dirty": False}), patch.object(load.base, "assert_local_engine"), \
         patch.object(load.pdfb, "Arm", return_value=stub), patch.object(load.time, "sleep"), patch.object(load.base, "probe", return_value={"cur": 100}), \
         patch.object(load, "vm_available", return_value=5000), patch.object(load, "collect_image", return_value=image), patch.object(load, "collect_topology", return_value={}), \
         patch.object(load, "admission_decision", return_value=admission), patch.object(load, "pdfb_paths", return_value=["/api/v1/dashboard/today"]), \
         patch.object(load, "run_drivers", side_effect=patches.get("run_drivers", RuntimeError("must not start the load"))):
        return load.scenario(scenario_args()), stub


def test_a_refused_admission_is_refused_not_run_before_any_load_starts():
    result, stub = run_scenario({"decision": "refused", "reason": "memory", "running_tenants": 2, "reserved_mib": 768})
    assert result["verdict"] == "REFUSED_NOT_RUN" and "refused" in result["failed_checks"][0] and "load_started_at" not in result
    assert stub.stopped and result["cleanup"]["removed_by_full_id"] == 1


def test_a_harness_fault_is_a_failed_act_with_its_reason_never_a_pass():
    result, stub = run_scenario({"decision": "admitted", "running_tenants": 2, "reserved_mib": 768})
    assert result["verdict"] == "FAIL" and "must not start the load" in result["failed_checks"][0] and stub.stopped
