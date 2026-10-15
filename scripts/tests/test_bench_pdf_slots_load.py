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


def good_result(tenants=4, slots=2):
    return {
        "tenants": tenants, "slots": slots,
        "runs": [{"status": "ok", "attempts": 1, "pdf_header_ok": True, "pdf_bytes": 60_000} for _ in range(tenants)],
        "seconds_to_last_run": 120.0, "peak_concurrent_pdfs_from_intervals": slots, "peak_busy_slots_sampled": slots,
        "healthz": {"n": 300, "failures": 0, "p50_ms": 20, "p95_ms": 400, "max_ms": 900},
        "api": {"dashboard_5xx_or_no_answer": 0, "pdf_in_request_bad": 0},
        "kernel_memory": {"events": {"high": 0, "max": 0, "oom": 0, "oom_kill": 0}, "oom_killed_containers": 0, "restarts": 0},
        "vm_min_available_mib": 3000, "admission": {"decision": "admitted", "running_tenants": tenants, "reserved_mib": tenants * 384}, "cleanup_residual_counts": {"container": 0, "volume": 0, "network": 0},
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
    ("admission saw no running tenants", lambda r: r.update(admission={"decision": "admitted", "running_tenants": 0, "reserved_mib": 0}), "did not measure the real engine state"),
    ("admission saw too few tenants", lambda r: r.update(admission={"decision": "refused", "running_tenants": 2, "reserved_mib": 768}), "2 running tenants"),
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
