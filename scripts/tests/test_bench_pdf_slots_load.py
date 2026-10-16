"""The PDF-slots load scenario decides PASS only when every declared check holds; no Docker is needed here."""
import copy
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / "provisioner"))
spec = importlib.util.spec_from_file_location("bench_pdf_slots_load_under_test", SCRIPTS / "bench_pdf_slots_load.py")
load = importlib.util.module_from_spec(spec)
spec.loader.exec_module(load)

CRITERIA = {"deadline_seconds": 180.0, "health_p95_ms": 3000.0, "floor_mib": 1500}


def good_cleanup(tenants=4):
    owned = {"container": tenants + 2, "volume": tenants + 1, "network": 1}
    return {"owned": owned, "removed_by_full_id": sum(owned.values()), "removed_ids_all_full": True, "already_absent": [], "not_ours": [], "owned_remaining": [],
            "unknown": [], "observation_errors": [], "unexpected_labelled": [], "foreign_collisions": [], "foreign_missing": [], "foreign_changed": [],
            "foreign_unstable_before_the_run": []}


def good_gate(tenants=4):
    return [{"index": i, "name": f"t{i}", "at": 900.0 + i, "create_started_at": 901.0 + i, "decision": "admitted"} for i in range(tenants)]


def good_result(tenants=4, slots=2):
    return {
        "tenants": tenants, "slots": slots, "observation_failures": [],
        "runs": [{"status": "ok", "attempts": 1, "pdf_header_ok": True, "pdf_bytes": 60_000} for _ in range(tenants)],
        "seconds_to_last_run": 120.0, "peak_concurrent_pdfs_from_intervals": slots, "peak_busy_slots_sampled": slots,
        "healthz": {"n": 300, "failures": 0, "p50_ms": 20, "p95_ms": 400, "max_ms": 900},
        "api": {"dashboard_5xx_or_no_answer": 0, "pdf_in_request_bad": 0},
        "kernel_memory": {"events": {"high": 0, "max": 0, "oom": 0, "oom_kill": 0}, "oom_killed_containers": 0, "restarts": 0},
        "vm_min_available_mib": 3000, "admission": {"decision": "admitted", "running_tenants": tenants, "reserved_mib": tenants * 384, "decided_at": 1000.0},
        "load_started_at": 1010.0, "start_gate": good_gate(tenants), "attestation_failures": [], "cleanup": good_cleanup(tenants), "cleanup_residual_counts": {"container": 0, "volume": 0, "network": 0},
    }


def verdict(mutate=None, **kwargs):
    result = good_result(**kwargs)
    if mutate:
        mutate(result)
    return load.evaluate(result, CRITERIA)


def test_a_clean_run_passes():
    assert verdict() == ("PASS", [])


def test_the_deadline_is_declared_from_the_slots_not_from_the_result():
    assert load.declared_deadline(4, 2, 60, 60) == 180
    assert load.declared_deadline(5, 2, 60, 60) == 240  # a third wave for the fifth tenant
    assert load.declared_deadline(30, 2, 45, 90) == 15 * 45 + 90
    for bad in ((0, 2, 60, 0), (4, 0, 60, 0), (4, 2, 0, 0), (4, 2, 60, -1)):
        with pytest.raises(ValueError):
            load.declared_deadline(*bad)


@pytest.mark.parametrize("name,mutate,fragment", [
    ("a run failed", lambda r: r["runs"][1].update(status="error"), "runs:"),
    ("a run is missing", lambda r: r["runs"].pop(), "runs:"),
    ("too late", lambda r: r.update(seconds_to_last_run=181.0), "deadline:"),
    ("never finished", lambda r: r.update(seconds_to_last_run=None), "deadline:"),
    ("no real pdf", lambda r: r["runs"][0].update(pdf_header_ok=False), "pdf:"),
    ("pdf too small", lambda r: r["runs"][0].update(pdf_bytes=100), "pdf:"),
    ("too many at once (intervals)", lambda r: r.update(peak_concurrent_pdfs_from_intervals=3), "slots: 3 PDFs overlapped"),
    ("too many slot files held", lambda r: r.update(peak_busy_slots_sampled=3), "slot files"),
    ("slots never saturated", lambda r: r.update(peak_concurrent_pdfs_from_intervals=1, peak_busy_slots_sampled=1), "did not use all"),
    ("healthz failed", lambda r: r["healthz"].update(failures=1), "health:"),
    ("healthz never answered", lambda r: r["healthz"].update(n=0), "health:"),
    ("healthz slow", lambda r: r["healthz"].update(p95_ms=3500), "health:"),
    ("dashboard 5xx", lambda r: r["api"].update(dashboard_5xx_or_no_answer=2), "api:"),
    ("in-request pdf neither 200 nor 503+Retry-After", lambda r: r["api"].update(pdf_in_request_bad=1), "in-request PDF"),
    ("memory.events high", lambda r: r["kernel_memory"]["events"].update(high=1), "memory.events high"),
    ("memory.events max", lambda r: r["kernel_memory"]["events"].update(max=1), "memory.events max"),
    ("memory.events oom_kill", lambda r: r["kernel_memory"]["events"].update(oom_kill=1), "memory.events oom_kill"),
    ("container OOM flag", lambda r: r["kernel_memory"].update(oom_killed_containers=1), "OOM-killed"),
    ("restart", lambda r: r["kernel_memory"].update(restarts=1), "restarts"),
    ("stand limit reached", lambda r: r.update(vm_min_available_mib=1499), "stand limit reached"),
    ("no admission decision", lambda r: r.update(admission={}), "admission:"),
    ("admission refused", lambda r: r["admission"].update(decision="refused", reason="memory"), "refused the next tenant"),
    ("admission after the load started", lambda r: r["admission"].update(decided_at=1011.0), "not taken before the load"),
    ("admission without a timestamp", lambda r: r["admission"].pop("decided_at"), "not taken before the load"),
    ("attestation missing", lambda r: r.pop("attestation_failures"), "attestation:"),
    ("attestation failed", lambda r: r.update(attestation_failures=["image: wrong"]), "attestation:"),
    ("cleanup report missing", lambda r: r.pop("cleanup"), "no ownership report"),
    ("cleanup recorded nothing", lambda r: r.update(cleanup=dict(good_cleanup(), owned={"container": 0, "volume": 0, "network": 0}, removed_by_full_id=0)), "recorded no objects"),
    ("cleanup confirmed fewer than recorded", lambda r: r["cleanup"].update(removed_by_full_id=3), "confirmed gone by full ID"),
    ("cleanup removed by short id", lambda r: r["cleanup"].update(removed_ids_all_full=False), "confirmed gone by full ID"),
    ("own object remains", lambda r: r["cleanup"].update(owned_remaining=["volume:x (rm status 1)"]), "own objects remain"),
    ("removal unverifiable (UNKNOWN)", lambda r: r["cleanup"].update(unknown=["container:t1 (removal unverifiable)"]), "state UNKNOWN"),
    ("engine could not be asked", lambda r: r["cleanup"].update(observation_errors=["docker ps failed"]), "could not be asked"),
    ("recorded object vanished", lambda r: r["cleanup"].update(already_absent=["container:t1"]), "vanished before cleanup"),
    ("recorded object changed hands", lambda r: r["cleanup"].update(not_ours=["volume:v"]), "no longer carry"),
    ("unrecorded labelled object", lambda r: r["cleanup"].update(unexpected_labelled=["container:abc"]), "were not recorded"),
    ("name collision with a foreign object", lambda r: r["cleanup"].update(foreign_collisions=["volume:customer"]), "collided"),
    ("foreign object gone", lambda r: r["cleanup"].update(foreign_missing=["deadbeef"]), "existed before the run are gone"),
    ("foreign missing unknown", lambda r: r["cleanup"].update(foreign_missing=None), "UNKNOWN, the engine could not be asked"),
    ("foreign changed unknown", lambda r: r["cleanup"].update(foreign_changed=None), "UNKNOWN, the engine could not be asked"),
    ("foreign container changed state", lambda r: r["cleanup"].update(foreign_changed=["f" * 64]), "changed state"),
    ("report lacks a list", lambda r: r["cleanup"].pop("unknown"), "lacks unknown"),
    ("report lacks the unstable list", lambda r: r["cleanup"].pop("foreign_unstable_before_the_run"), "lacks foreign_unstable_before_the_run"),
    ("start gate missing", lambda r: r.pop("start_gate"), "BEFORE its first object"),
    ("start gate for too few tenants", lambda r: r["start_gate"].pop(), "BEFORE its first object"),
    ("a tenant was refused by the gate", lambda r: r["start_gate"][1].update(decision="refused"), "BEFORE its first object"),
    ("gate decided after the tenant was created", lambda r: r["start_gate"][1].update(at=950.0), "BEFORE its first object"),
    ("gate without creation time", lambda r: r["start_gate"][0].pop("create_started_at"), "BEFORE its first object"),
    ("admission saw no running tenants", lambda r: r.update(admission={"decision": "admitted", "running_tenants": 0, "reserved_mib": 0, "decided_at": 1000.0}), "did not measure the real engine state"),
    ("admission saw too few tenants", lambda r: r.update(admission={"decision": "admitted", "running_tenants": 2, "reserved_mib": 768, "decided_at": 1000.0}), "2 running tenants"),
    ("cleanup residue", lambda r: r.update(cleanup_residual_counts={"container": 1, "volume": 0, "network": 0}), "cleanup:"),
    ("cleanup not verified", lambda r: r.update(cleanup_residual_counts=None), "cleanup:"),
])
def test_each_declared_check_alone_fails_the_run(name, mutate, fragment):
    outcome, failed = verdict(mutate)
    assert outcome == "FAIL", name
    assert any(fragment in line for line in failed), (name, failed)


def test_a_stand_limit_is_never_reported_as_success_even_when_everything_else_is_fine():
    outcome, failed = verdict(lambda r: r.update(vm_min_available_mib=900))
    assert outcome == "FAIL" and len(failed) == 1


def test_with_fewer_tenants_than_slots_saturation_is_not_required():
    outcome, _ = verdict(lambda r: r.update(peak_concurrent_pdfs_from_intervals=1, peak_busy_slots_sampled=1), tenants=1)
    assert outcome == "PASS"


def test_several_failures_are_all_listed_not_just_the_first():
    def break_everything(r):
        r["runs"][0]["status"] = "error"
        r["healthz"]["failures"] = 3
        r["vm_min_available_mib"] = 10
    outcome, failed = verdict(break_everything)
    assert outcome == "FAIL" and len(failed) >= 3


def test_peak_concurrency_counts_ends_before_starts_on_ties():
    assert load.pdfb.peak_concurrency([(0, 10), (10, 20), (20, 30)]) == 1  # back to back is not an overlap
    assert load.pdfb.peak_concurrency([(0, 10), (5, 15), (8, 12)]) == 3
    assert load.pdfb.peak_concurrency([]) == 0


def bucket(calc=(10, 20), codes=None, health=(5, 6), health_fail=0, pdf=()):
    return {"calc_ms": list(calc), "calc_codes": codes or {"200 /api/v1/dashboard/today": 2}, "health_ms": list(health),
            "health_fail": health_fail, "api_pdf": list(pdf)}


def test_api_summary_separates_business_4xx_from_failures_and_validates_the_in_request_pdf():
    buckets = {
        "t1": bucket(codes={"200 /api/v1/dashboard/today": 5, "409 /api/v1/dashboard/customer-quality": 2, "500 /api/v1/control/data-quality": 1},
                     pdf=[{"status": 200, "retry_after": None, "ms": 5000}, {"status": 503, "retry_after": "30", "ms": 15100}]),
        "t2": bucket(codes={"0 /api/v1/dashboard/finance": 1}, pdf=[{"status": 503, "retry_after": None, "ms": 100}, {"status": 500, "retry_after": None, "ms": 90}], health_fail=2),
    }
    api, health = load.summarize_api(buckets)
    assert api["dashboard_5xx_or_no_answer"] == 2  # the 500 and the no-answer; the 409 is a business answer
    assert api["pdf_in_request_200"] == 1 and api["pdf_in_request_503_retry_after"] == 1 and api["pdf_in_request_bad"] == 2
    assert health["failures"] == 2 and health["n"] == 4


def test_percentile_handles_small_and_empty_samples():
    assert load.percentile([], .95) == 0.0 and load.percentile([7], .95) == 7
    assert load.percentile(list(range(1, 101)), .95) == 95


def test_a_dirty_tree_is_refused_so_an_act_always_names_its_commit():
    args = load.argparse.Namespace(allow_dirty=False, tenants=2, slots=2, pdf_seconds=60, margin_seconds=60)
    with patch.object(load, "git_state", return_value={"head": "abc", "branch": "x", "dirty": True}):
        with pytest.raises(SystemExit) as refused:
            load.scenario(args)
    assert "dirty" in str(refused.value)


def test_the_remote_engine_guard_runs_before_anything_is_created():
    args = load.argparse.Namespace(allow_dirty=False, tenants=2, slots=2, pdf_seconds=60, margin_seconds=60, health_p95_ms=1, min_vm_available_mib=1,
                                   image="x", prefix="bench", settle=0, api_parallel=3, api_pdf_every=20.0, drivers=2, grace_seconds=1, heavy_variants=1,
                                   pdf_peak_delta_mib=250, api_delta_mib=40, endpoints_from="unused")
    with patch.object(load, "git_state", return_value={"head": "abc", "branch": "x", "dirty": False}), \
         patch.object(load.base, "assert_local_engine", side_effect=SystemExit("Refusing to run")), \
         patch.object(load.pdfb, "Arm") as arm:
        with pytest.raises(SystemExit):
            load.scenario(args)
    arm.assert_not_called()


def test_the_embedded_scripts_are_valid_python_and_the_driver_stops_on_sigterm():
    import ast
    for script in (load.DRIVER, load.STATE):
        ast.parse(script)
    assert "signal.SIGTERM" in load.DRIVER and "pdf_header_ok" in load.STATE


@pytest.mark.parametrize("mutate", [
    lambda r: r.pop("observation_failures"),
    lambda r: r.update(observation_failures=["missing kernel peak"]),
    lambda r: r.update(observation_failures=["missing per-tenant driver"]),
    lambda r: r.update(observation_failures=["sampler failed"]),
])
def test_missing_or_failed_observations_never_pass(mutate):
    result = good_result()
    mutate(result)
    assert load.evaluate(result, CRITERIA)[0] == "FAIL"


def observations():
    names = ["t1", "t2"]
    paths = ["/api/v1/dashboard/today"]
    buckets = {name: bucket(pdf=[{"status":200,"retry_after":None,"ms":1}]) for name in names}
    row = {"cur":100,"peak":200,"ev_high":0,"ev_max":0,"ev_oom":0,"ev_oom_kill":0,"cpu_nr_throttled":0,"cpu_nr_periods":10}
    return [names, paths, buckets, {n:dict(row) for n in names}, {n:dict(row) for n in names}, [(3000,2)], [], []]


def test_complete_raw_observations_pass():
    assert load.observation_failures(*observations()) == []


@pytest.mark.parametrize("index,mutate", [
    (2, lambda value:value.pop("t2")),
    (2, lambda value:value["t2"].update(calc_ms=[])),
    (2, lambda value:value["t2"].update(health_ms=[])),
    (2, lambda value:value["t2"].update(api_pdf=[])),
    (2, lambda value:value["t2"].update(calc_codes={"403 /api/v1/dashboard/today":2})),
    (3, lambda value:value["t2"].pop("peak")),
    (4, lambda value:value["t2"].pop("ev_oom")),
    (4, lambda value:value["t2"].pop("ev_oom_kill")),
    (4, lambda value:value["t2"].update(peak=0)),
    (5, lambda value:value.clear()),
    (5, lambda value:value.append((3000,None))),
    (6, lambda value:value.append("SamplerError")),
    (7, lambda value:value.append("DriverThreadError")),
])
def test_each_raw_observation_gap_fails(index, mutate):
    values = observations()
    mutate(values[index])
    assert load.observation_failures(*values)


def test_driver_exit_and_missing_result_are_not_silently_merged():
    payload={"buckets":{"t1":bucket()},"worker_errors":["RuntimeError"]}
    def docker(*args, **kwargs):
        if args[0] == "wait":return "2"
        if args[0] == "logs":return load.json.dumps(payload)
        return ""
    with patch.object(load.base,"docker",side_effect=docker):
        buckets,errors=load.collect_drivers(["own-driver"])
    assert set(buckets)=={"t1"} and errors==["DriverExit","RuntimeError"]


def test_driver_thread_exceptions_are_recorded_and_exit_nonzero():
    assert "threading.excepthook = thread_failure" in load.DRIVER
    assert "sys.exit(2 if worker_errors else 0)" in load.DRIVER
