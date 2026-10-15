"""Verdict logic and refusal paths of the PDF fault scenarios; the faults themselves need real containers and are run by the script."""
import ast
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / "provisioner"))
spec = importlib.util.spec_from_file_location("bench_pdf_faults_under_test", SCRIPTS / "bench_pdf_faults.py")
faults = importlib.util.module_from_spec(spec)
spec.loader.exec_module(faults)
load = faults.load
pdfb = faults.pdfb

SCENARIOS = ("S1_sigkill_slot_holder", "S2_lease_expiry_and_fencing", "S3_poison_job", "S4_cancel_by_deleting_the_definition")


def good_cleanup():
    owned = {"container": 5, "volume": 4, "network": 1}
    return {"owned": owned, "removed_by_full_id": 10, "removed_ids_all_full": True, "already_absent": [], "not_ours": [], "owned_remaining": [], "unknown": [], "observation_errors": [],
            "unexpected_labelled": [], "foreign_collisions": [], "foreign_missing": [], "foreign_changed": [], "foreign_unstable_before_the_run": []}


def good_result(tenants=3):
    return {"tenants": tenants, "floor_mib": 1500, "vm_min_available_mib": 4000, "oom_killed_containers": 0, "cleanup": good_cleanup(),
            "start_gate": [{"index": i, "name": f"t{i}", "at": 100.0 + i, "create_started_at": 101.0 + i, "decision": "admitted"} for i in range(tenants)],
            "scenarios": {name: {"checks": [faults.check("something holds", True), faults.check("something else holds", True)]} for name in SCENARIOS}}


def verdict(mutate=None):
    result = good_result()
    if mutate:
        mutate(result)
    return faults.failed_checks(result)


def test_a_complete_clean_result_has_no_failures():
    assert verdict() == []


@pytest.mark.parametrize("name,mutate,fragment", [
    ("one check of one scenario fails", lambda r: r["scenarios"][SCENARIOS[2]]["checks"].append(faults.check("poison closed", False, "still running")), "poison closed"),
    ("a scenario recorded no checks", lambda r: r["scenarios"][SCENARIOS[1]].update(checks=[]), "no checks recorded"),
    ("a scenario is missing", lambda r: r["scenarios"].pop(SCENARIOS[3]), "expected exactly"),
    ("an unknown scenario is present", lambda r: r["scenarios"].update(extra={"checks": [faults.check("x", True)]}), "expected exactly"),
    ("scenarios absent", lambda r: r.pop("scenarios"), "expected exactly"),
    ("a scenario raised", lambda r: r["scenarios"][SCENARIOS[0]].update(error="TimeoutError: x"), "scenario itself failed"),
    ("gate missing", lambda r: r.pop("start_gate"), "BEFORE its first object"),
    ("gate for too few tenants", lambda r: r["start_gate"].pop(), "BEFORE its first object"),
    ("a tenant was refused", lambda r: r["start_gate"][0].update(decision="refused"), "BEFORE its first object"),
    ("gate after creation", lambda r: r["start_gate"][0].update(at=500.0), "BEFORE its first object"),
    ("vm memory not measured", lambda r: r.update(vm_min_available_mib=None), "not measured"),
    ("vm below the floor", lambda r: r.update(vm_min_available_mib=1499), "stand limit"),
    ("an oom kill", lambda r: r.update(oom_killed_containers=1), "OOM-killed"),
    ("cleanup unverifiable", lambda r: r["cleanup"].update(unknown=["container:x"]), "UNKNOWN"),
    ("cleanup missing", lambda r: r.pop("cleanup"), "no ownership report"),
])
def test_each_gap_alone_fails_the_act(name, mutate, fragment):
    failed = verdict(mutate)
    assert failed and any(fragment in line for line in failed), (name, failed)


def test_peak_overlap_counts_builds_that_ran_at_once():
    rows = [{"status": "ok", "started_at": "2026-10-16T10:00:00", "finished_at": "2026-10-16T10:00:30"},
            {"status": "ok", "started_at": "2026-10-16T10:00:10", "finished_at": "2026-10-16T10:00:40"},
            {"status": "ok", "started_at": "2026-10-16T10:00:30", "finished_at": "2026-10-16T10:01:00"},
            {"status": "error", "started_at": "2026-10-16T10:00:00", "finished_at": "2026-10-16T10:01:00"}]
    assert faults.peak_overlap(rows) == 2


def test_the_container_side_operations_are_valid_python_and_cover_every_fault():
    ast.parse(faults.OPS)
    for operation in ("define", "enqueue", "rows", "dead_run", "expire_leases", "stale_owner", "delete_definition", "slots"):
        assert f'op == "{operation}"' in faults.OPS
    assert "finish_run(arg[\"run_id\"], status=\"ok\"" in faults.OPS and "heartbeat(arg[\"run_id\"]" in faults.OPS


def test_wait_until_returns_the_value_or_none_on_timeout():
    assert faults.wait_until(lambda: "x", 1, 0.01) == "x"
    assert faults.wait_until(lambda: None, 0.05, 0.01) is None


# ---- refusal and fault paths of the harness ----------------------------------------------------------------------------------

def scenario_args(**overrides):
    values = dict(allow_dirty=False, tenants=3, slots=2, image="x", prefix="bench", settle=0, min_vm_available_mib=1500, pdf_seconds=1, fault_timeout=1)
    values.update(overrides)
    return load.argparse.Namespace(**values)


class StubArm:
    refusal = None
    create_started = {}
    owned = {"container": [], "volume": [], "network": []}
    names = ["a", "b", "c"]

    def __init__(self, *a, **k):
        self.stopped = False

    def start(self, gate=None):
        if self.refusal is not None:
            raise self.refusal

    def wait_healthy(self):
        pass

    def stop(self):
        self.stopped = True

    def cleanup_report(self):
        return good_cleanup()


def run_scenario(refusal=None):
    stub = StubArm()
    stub.refusal = refusal
    with patch.object(load, "git_state", return_value={"head": "abc", "branch": "x", "dirty": False}), patch.object(faults.base, "assert_local_engine"), \
         patch.object(faults.pdfb, "Arm", return_value=stub), patch.object(faults.time, "sleep"), \
         patch.object(faults, "run_scenarios", side_effect=RuntimeError("scenarios must not run")), patch.object(load, "collect_image", side_effect=RuntimeError("no engine")):
        return faults.scenario(scenario_args()), stub


def test_a_refusal_before_creation_is_refused_not_run_and_cleans_up():
    result, stub = run_scenario(pdfb.StartRefused(1, {"code": "capacity_exhausted", "reason": "host_memory"}))
    assert result["verdict"] == "REFUSED_NOT_RUN" and "refused tenant 2 of 3" in result["failed_checks"][0] and stub.stopped and "scenarios" not in result


def test_a_harness_fault_is_a_failed_act_with_its_reason_and_the_cleanup_still_runs():
    result, stub = run_scenario()
    assert result["verdict"] == "FAIL" and "no engine" in result["failed_checks"][0] and stub.stopped


def test_a_dirty_tree_is_refused():
    with patch.object(load, "git_state", return_value={"head": "abc", "branch": "x", "dirty": True}):
        with pytest.raises(SystemExit):
            faults.scenario(scenario_args())


def test_container_operations_accept_a_name_argument_and_ship_the_whole_argument_object():
    """Regression: `define` takes a `name` argument, which once collided with the tenant parameter of op()."""
    calls = []

    def fake_docker(*args, **kwargs):
        calls.append(args)
        return '{"definition_id": 7, "run_id": 9, "rows": [], "expired": 1}'
    with patch.object(faults.base, "docker", fake_docker):
        job = faults.new_job("tenant-1", "faults-s1")
        assert job == {"definition_id": 7, "run_id": 9, "second_enqueue_run_id": 9}
        assert faults.op("tenant-1", "define", name="x")["definition_id"] == 7
    shipped = [c for c in calls if c[0] == "exec"]
    assert shipped[0][2:4] == ("python3", "-c") and shipped[0][5] == '{"name": "faults-s1"}' and shipped[1][5] == '{"definition_id": 7}'
