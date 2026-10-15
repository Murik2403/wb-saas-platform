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
            "HostConfig": {"ReadonlyRootfs": True, "Privileged": False, "CapDrop": ["ALL"], "CapAdd": None, **load.TENANT_LIMITS},
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
    ("raised memory limit before", lambda b, a: b[NAMES[0]]["inspect"]["HostConfig"].update(Memory=2 * load.TENANT_MEMORY_BYTES), "limit Memory"),
    ("memory limit raised only after the load", lambda b, a: a[NAMES[0]]["inspect"]["HostConfig"].update(Memory=2 * load.TENANT_MEMORY_BYTES), "limit Memory is"),
    ("memory reservation changed", lambda b, a: a[NAMES[2]]["inspect"]["HostConfig"].update(MemoryReservation=1), "limit MemoryReservation"),
    ("cpu limit removed", lambda b, a: b[NAMES[2]]["inspect"]["HostConfig"].update(NanoCpus=0), "limit NanoCpus"),
    ("pids limit raised after", lambda b, a: a[NAMES[2]]["inspect"]["HostConfig"].update(PidsLimit=-1), "limit PidsLimit"),
    ("oom score adjusted after", lambda b, a: a[NAMES[2]]["inspect"]["HostConfig"].update(OomScoreAdj=0), "limit OomScoreAdj"),
    ("after-inspection uses another image", lambda b, a: a[NAMES[1]]["inspect"].update(Image="sha256:" + "c" * 64), "(after)"),
    ("slot volume swapped only after", lambda b, a: a[NAMES[1]]["inspect"]["Mounts"][0].update(Name="other"), "(after)"),
    ("lock dev missing", lambda b, a: a[NAMES[1]]["locks"]["dir"].pop("dev"), "lacks a typed device"),
    ("lock inode missing in every tenant", lambda b, a: [a[n]["locks"]["files"]["pdf-slot-00.lock"].pop("ino") for n in NAMES], "lacks a typed device"),
    ("lock inode None everywhere (None == None)", lambda b, a: [a[n]["locks"]["dir"].update(ino=None) for n in NAMES], "lacks a typed device"),
    ("lock inode zero", lambda b, a: a[NAMES[0]]["locks"]["dir"].update(ino=0), "lacks a typed device"),
    ("lock mode missing (a default of 0 is not evidence)", lambda b, a: a[NAMES[1]]["locks"]["files"]["pdf-slot-01.lock"].pop("mode"), "lacks a typed device"),
    ("lock mode a string", lambda b, a: a[NAMES[1]]["locks"]["dir"].update(mode="0o770"), "lacks a typed device"),
    ("lock owner missing", lambda b, a: a[NAMES[1]]["locks"]["dir"].pop("uid"), "lacks a typed device"),
    ("locks missing for every tenant", lambda b, a: [a[n].pop("locks") for n in NAMES], "no lock identity"),
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
    ("lock files with other names", lambda b, a: a[NAMES[1]]["locks"]["files"].update({"x.lock": a[NAMES[1]]["locks"]["files"].pop("pdf-slot-01.lock")}), "expected exactly"),
    ("lock owned by root", lambda b, a: a[NAMES[1]]["locks"]["files"]["pdf-slot-00.lock"].update(uid=0), "not owned by 10001"),
    ("directory world accessible", lambda b, a: a[NAMES[1]]["locks"]["dir"].update(mode=0o777), "accessible to others"),
    ("lock writable by others", lambda b, a: a[NAMES[1]]["locks"]["files"]["pdf-slot-00.lock"].update(mode=0o666), "accessible to others"),
    ("image volume replaced by a bind", lambda b, a: b[NAMES[1]]["inspect"]["Mounts"].append({"Destination": "/app/tenant-app/data", "Type": "bind", "Name": "", "RW": True}), "anonymous volume"),
    ("different directory inode", lambda b, a: a[NAMES[2]]["locks"]["dir"].update(ino=101), "not the same directory"),
    ("different device", lambda b, a: a[NAMES[2]]["locks"]["dir"].update(dev=67), "not the same directory"),
    ("different lock file inode", lambda b, a: a[NAMES[2]]["locks"]["files"]["pdf-slot-01.lock"].update(ino=999), "not the same files"),
    ("no inspect data", lambda b, a: b.pop(NAMES[1]), "no inspect data"),
])
def test_each_topology_property_alone_fails(name, mutate, fragment):
    failed = topo_failures(mutate)
    assert failed, name
    assert any(fragment in line for line in failed), (name, failed)


def test_the_attested_files_include_the_d014_memory_fix():
    paths = {path for path, _ in load.SOURCE_FILES}
    assert {"/app/tenant-app/reports/metrics.py", "/app/tenant-app/db/financial_fifo_reconciliation.py"} <= paths


def test_the_images_own_anonymous_volume_is_expected():
    def add(before, after):
        for table in (before, after):
            table[NAMES[0]]["inspect"]["Mounts"].append({"Destination": "/app/tenant-app/data", "Type": "volume", "Name": "d" * 64, "RW": True})
    assert topo_failures(add) == []


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
    return load.image_failures({"id": IMAGE_ID}, trees or TREES, hashes if hashes is not None else {n: dict(BLOBS) for n in NAMES}, BLOBS, NAMES)


def test_an_image_built_from_the_same_sources_passes():
    assert image_check() == []


@pytest.mark.parametrize("name,trees,hashes", [
    ("image without a revision", dict(TREES, built_from=None), None),
    ("tenant-app tree differs", dict(TREES, built_from_tenant_app="other"), None),
    ("tenant-api tree differs", dict(TREES, built_from_tenant_api="other"), None),
    ("tree of the revision unknown", dict(TREES, built_from_tenant_app=None, head_tenant_app=None), None),
    ("no hashes read", None, {}),
    ("one file differs in one tenant", None, {NAMES[0]: dict(BLOBS), NAMES[1]: dict(BLOBS, **{load.SOURCE_FILES[0][0]: "f" * 64}), NAMES[2]: dict(BLOBS)}),
    ("hashes for one tenant only", None, {NAMES[0]: dict(BLOBS)}),
    ("hashes for an extra, unknown tenant", None, {**{n: dict(BLOBS) for n in NAMES}, "stranger": dict(BLOBS)}),
    ("hashes for an unknown tenant instead", None, {NAMES[0]: dict(BLOBS), NAMES[1]: dict(BLOBS), "stranger": dict(BLOBS)}),
    ("an extra unattested file hashed", None, {n: dict(BLOBS, **{"/app/other.py": "1" * 64}) for n in NAMES}),
    ("a file is missing in a tenant", None, {NAMES[0]: {k: v for k, v in BLOBS.items() if k != load.SOURCE_FILES[0][0]}, NAMES[1]: dict(BLOBS), NAMES[2]: dict(BLOBS)}),
])
def test_each_image_gap_fails(name, trees, hashes):
    assert image_check(trees, hashes), name


def test_a_missing_repository_blob_fails():
    broken = dict(BLOBS, **{load.SOURCE_FILES[0][0]: None})
    assert load.image_failures({"id": IMAGE_ID}, TREES, {n: dict(broken) for n in NAMES}, broken, NAMES)


def test_every_attested_source_file_exists_in_this_repository():
    root = Path(__file__).resolve().parents[2]
    assert all((root / repo_path).is_file() for _, repo_path in load.SOURCE_FILES)


def test_an_image_without_an_id_fails():
    assert load.image_failures({"id": ""}, TREES, {n: dict(BLOBS) for n in NAMES}, BLOBS, NAMES)


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

    refusal = None
    create_started = {}
    owned = {"container": [], "volume": [], "network": []}

    def start(self, gate=None):
        if self.refusal is not None:
            raise self.refusal
    def wait_healthy(self): pass
    def stop(self): self.stopped = True
    def cleanup_report(self):
        return {"owned": {"container": 1}, "removed_by_full_id": 1, "removed_ids_all_full": True, "already_absent": [], "not_ours": [], "owned_remaining": [], "unknown": [],
                "observation_errors": [], "unexpected_labelled": [], "foreign_collisions": [], "foreign_missing": [], "foreign_changed": [], "foreign_unstable_before_the_run": []}


def run_scenario(admission, refusal=None, **patches):
    stub = StubArm()
    stub.refusal = refusal
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


def test_a_refusal_by_the_per_tenant_gate_stops_creation_and_is_refused_not_run():
    refusal = pdfb.StartRefused(1, {"code": "capacity_exhausted", "reason": "host_memory"})
    result, stub = run_scenario({"decision": "admitted"}, refusal=refusal)
    assert result["verdict"] == "REFUSED_NOT_RUN" and "refused tenant 2 of 2 before it was created" in result["failed_checks"][0] and stub.stopped


def test_a_refusal_whose_cleanup_could_not_be_verified_is_a_failed_act_not_a_clean_refusal():
    refusal = pdfb.StartRefused(0, {"code": "capacity_exhausted", "reason": "budget"})
    with patch.object(StubArm, "cleanup_report", return_value=dict(StubArm().cleanup_report(), unknown=["container:t1 (removal unverifiable)"])):
        result, _ = run_scenario({"decision": "admitted"}, refusal=refusal)
    assert result["verdict"] == "FAIL" and any("UNKNOWN" in line for line in result["failed_checks"])


def test_a_harness_fault_is_a_failed_act_with_its_reason_never_a_pass():
    result, stub = run_scenario({"decision": "admitted", "running_tenants": 2, "reserved_mib": 768})
    assert result["verdict"] == "FAIL" and "must not start the load" in result["failed_checks"][0] and stub.stopped
