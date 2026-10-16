"""The fault scenarios' own logic (S1 precondition, S3 neighbour isolation, memory/OOM observation) against a small simulated fleet.

The simulator is a clock, a few tenants and a slot rule: enough to prove WHAT the scenarios require and WHEN they kill, not how Docker
behaves (the real runs do that).
"""
import importlib.util
import itertools
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / "provisioner"))
spec = importlib.util.spec_from_file_location("bench_pdf_faults_scenarios_under_test", SCRIPTS / "bench_pdf_faults.py")
faults = importlib.util.module_from_spec(spec)
spec.loader.exec_module(faults)
load = faults.load
pdfb = faults.pdfb


def stamp(t):
    return f"2026-10-16T10:{t // 60:02d}:{t % 60:02d}"


class World:
    """Tenants with one worker each; a worker builds one run at a time; at most `slots` builds run at once across the fleet."""

    def __init__(self, names, slots=2, duration=None):
        self.t = 0
        self.slots = slots
        self.duration = duration or {}
        self.events = []
        self.tenants = {n: {"runs": {}, "defs": {}, "alive": True, "seq": itertools.count(1), "started": 0} for n in names}

    # -- time
    def running_total(self):
        return sum(1 for tn in self.tenants.values() if tn["alive"] for r in tn["runs"].values() if r["status"] == "running" and not r["dead"])

    def tick(self):
        self.t += 1
        for name, tn in self.tenants.items():
            if not tn["alive"]:
                continue
            mine = [r for r in tn["runs"].values() if r["status"] == "running" and not r["dead"]]
            if mine:
                run = mine[0]
                run["left"] -= 1
                if run["left"] <= 0:
                    run.update(status="ok", finished=self.t, pdf=True)
                continue
            queued = sorted((r for r in tn["runs"].values() if r["status"] == "queued"), key=lambda r: r["id"])
            if queued and self.running_total() < self.slots:
                queued[0].update(status="running", attempts=queued[0]["attempts"] + 1, left=self.duration.get(name, 4), started=self.t, dead=False)

    # -- the container-side operations
    def op(self, tenant, operation, **arg):
        tn = self.tenants[tenant]
        self.events.append((self.t, operation, tenant))
        if operation == "define":
            did = next(tn["seq"])
            tn["defs"][did] = True
            return {"definition_id": did}
        if operation == "enqueue":
            existing = [r for r in tn["runs"].values() if r["definition_id"] == arg["definition_id"] and r["status"] in ("queued", "running")]
            if existing:
                return {"run_id": existing[0]["id"]}
            rid = next(tn["seq"]) + 100
            tn["runs"][rid] = {"id": rid, "definition_id": arg["definition_id"], "status": "queued", "attempts": 0, "left": 0, "dead": False, "pdf": False, "started": 0, "finished": 0}
            return {"run_id": rid}
        if operation == "expire_leases":
            count = 0
            for run in tn["runs"].values():
                if run["status"] == "running" and run["dead"]:
                    count += 1
                    if run["attempts"] >= 3:
                        run.update(status="error", error="Сборка прервана 3 раза подряд (возможно, нехватка памяти).")
                    else:
                        run.update(status="queued", dead=False)
            return {"expired": count}
        raise AssertionError(f"operation {operation} is not simulated")

    def row(self, tenant, run_id):
        run = self.tenants[tenant]["runs"].get(run_id)
        if run is None:
            return None
        return {"id": run["id"], "definition_id": run["definition_id"], "status": run["status"], "attempts": run["attempts"], "error": run.get("error"),
                "started_at": stamp(run["started"]), "finished_at": stamp(run["finished"]) if run["status"] == "ok" else None, "pdf_header_ok": run["pdf"],
                "pdf_bytes": 30000 if run["pdf"] else 0, "file_recorded": run["pdf"], "file_present": run["pdf"]}

    def busy(self):
        return self.running_total()

    def docker(self, *args, check=True, timeout=300):
        self.events.append((self.t, args[0], args[1] if len(args) > 1 else None))
        if args[0] == "kill":
            tn = self.tenants[args[1]]
            tn["alive"] = False
            for run in tn["runs"].values():
                if run["status"] == "running":
                    run["dead"] = True
            return args[1]
        if args[0] == "start":
            self.tenants[args[1]]["alive"] = True
            return args[1]
        if args[0] == "inspect" and "{{.State.ExitCode}}" in args:
            return "137"
        raise AssertionError(f"docker {args} is not simulated")


def run_with(world, scenario, slots=2, busy=None, after_wait=None, **extra):
    names = list(world.tenants)

    def wait_until(predicate, timeout, interval=1.0):
        for _ in range(int(max(timeout, 1) * 4)):
            value = predicate()
            if value:
                if after_wait:
                    after_wait(value)
                return value
            world.tick()
        return None
    args = type("A", (), {"slots": slots, "fault_timeout": 120, "pdf_seconds": 5, **extra})()
    arm = type("Arm", (), {"names": names})()
    with patch.object(faults, "op", world.op), patch.object(faults, "run_by_id", world.row), patch.object(faults, "host_busy", busy or (lambda name: world.busy())), \
         patch.object(faults, "wait_until", wait_until), patch.object(faults, "wait_healthy", lambda *a, **k: True), patch.object(faults.base, "docker", world.docker), \
         patch.object(faults.time, "sleep", lambda s: world.tick()), patch.object(faults.time, "time", lambda: float(world.t)):
        return scenario(arm, args)


def results(entry):
    return {c["check"]: c["ok"] for c in entry["checks"]}


# ---- S1: the kill needs a positively observed waiting contender -------------------------------------------------------------------

def test_s1_kills_only_after_seeing_every_slot_busy_and_a_tenant_waiting_then_sees_it_take_over():
    world = World(["t-a", "t-b", "t-c"])
    outcome = run_with(world, faults.s1_sigkill)
    assert all(c["ok"] for c in outcome["checks"]), [c for c in outcome["checks"] if not c["ok"]]
    kill_time = next(t for t, verb, _ in world.events if verb == "kill")
    enqueues = [t for t, verb, _ in world.events if verb == "enqueue"]
    assert min(enqueues) <= kill_time and outcome["notes"]["waiting_before_kill"] and outcome["notes"]["waiting_tenant_seen_running_after_kill"]
    assert outcome["notes"]["slots_busy_before_kill"] == 2


def test_s1_without_a_waiting_contender_nothing_is_killed_and_the_scenario_fails():
    """The review's counterexample: both other jobs already finished; a busy count falling to zero must not count as a hand-over."""
    world = World(["t-a", "t-b"], slots=2)  # two tenants, two slots: nobody ever waits
    outcome = run_with(world, faults.s1_sigkill)
    verdicts = results(outcome)
    assert verdicts["every slot busy and a tenant observed waiting for one before the kill"] is False
    assert not [e for e in world.events if e[1] == "kill"], "no SIGKILL may be sent when the precondition never held"


def test_s1_with_a_waiting_tenant_but_free_slots_does_not_kill_either():
    world = World(["t-a", "t-b", "t-c"], slots=2)
    outcome = run_with(world, faults.s1_sigkill, slots=3)  # the declared slot count says 3 are busy, the fleet only ever runs 2
    assert results(outcome)["every slot busy and a tenant observed waiting for one before the kill"] is False
    assert not [e for e in world.events if e[1] == "kill"]


def test_s1_a_handover_that_never_happens_fails_the_scenario():
    world = World(["t-a", "t-b", "t-c"])
    original = world.tick

    def frozen_after_kill():
        if any(e[1] == "kill" for e in world.events):
            world.t += 1  # time passes but the waiting tenant is never scheduled
            return
        original()
    world.tick = frozen_after_kill
    outcome = run_with(world, faults.s1_sigkill)
    assert results(outcome)["the tenant that was waiting took the freed slot within 15 s (observed queued before, running after)"] is False


def test_s1_waits_for_the_slot_files_not_only_for_run_statuses():
    """Statuses say two builds run and one waits, but the lock files show fewer held slots: no kill."""
    world = World(["t-a", "t-b", "t-c"])
    outcome = run_with(world, faults.s1_sigkill, busy=lambda name: 1)
    assert results(outcome)["every slot busy and a tenant observed waiting for one before the kill"] is False
    assert not [e for e in world.events if e[1] == "kill"]


def test_s1_waits_for_enough_running_builds_not_only_for_held_slot_files():
    """The slot files show every slot held, but only one tenant's run is running (the other holder is not a tenant of this test): no kill."""
    world = World(["t-a", "t-b", "t-c"], slots=1)  # the fleet only ever runs one build while the harness declares two slots
    outcome = run_with(world, faults.s1_sigkill, busy=lambda name: 2)
    assert results(outcome)["every slot busy and a tenant observed waiting for one before the kill"] is False
    assert not [e for e in world.events if e[1] == "kill"]


def test_s1_rechecks_the_waiting_tenant_at_the_kill_instant():
    """The contender started between the precondition and the kill: nothing waits any more, so nothing is killed."""
    world = World(["t-a", "t-b", "t-c"])

    def contender_starts(found):
        for name in found["contenders"]:
            for run in world.tenants[name]["runs"].values():
                run.update(status="running", attempts=1, left=10, started=world.t)
    outcome = run_with(world, faults.s1_sigkill, after_wait=contender_starts)
    assert results(outcome)["every slot busy and a tenant observed waiting for one at the moment of the kill"] is False
    assert not [e for e in world.events if e[1] == "kill"]


# ---- S3: the neighbour must be building through every kill ----------------------------------------------------------------------

def test_s3_a_neighbour_build_is_running_at_each_of_the_three_kills_and_all_complete():
    world = World(["t-a", "t-b", "t-c"], duration={"t-a": 12, "t-c": 60})
    outcome = run_with(world, faults.s3_poison)
    verdicts = results(outcome)
    assert all(verdicts.values()), [k for k, v in verdicts.items() if not v]
    kills = [i for i, e in enumerate(world.events) if e[1] == "kill"]
    neighbour_enqueues = [i for i, e in enumerate(world.events) if e[1] == "enqueue" and e[2] == "t-a"]
    assert len(kills) == 3 and len(neighbour_enqueues) >= 3
    assert all(any(n < k for n in neighbour_enqueues) for k in kills), "a neighbour job was enqueued before every kill"
    assert [c["neighbour_status_at_kill"] for c in outcome["notes"]["cycles"]] == ["running"] * 3


def test_s3_a_neighbour_that_never_builds_fails_the_scenario():
    """Work after the poison episode would not be isolation: if the other tenant has nothing running at the kill, the check fails."""
    world = World(["t-a", "t-b", "t-c"], duration={"t-c": 60})
    world.tenants["t-a"]["alive"] = False  # the other tenant cannot build at all while the poison tenant is being killed
    outcome = run_with(world, faults.s3_poison)
    verdicts = results(outcome)
    assert any(not ok and "the other tenant had a build RUNNING at the moment of the kill" in name for name, ok in verdicts.items())
    assert verdicts["every build of the other tenant, running through the kill cycles, completed ok on attempt 1 with a real PDF"] is False


def test_s3_the_poison_run_is_closed_after_exactly_three_interruptions():
    world = World(["t-a", "t-b", "t-c"], duration={"t-a": 12, "t-c": 60})
    outcome = run_with(world, faults.s3_poison)
    verdicts = results(outcome)
    assert verdicts["after the third interruption the run is closed with an error"] and verdicts["the error says it was interrupted 3 times"]
    assert verdicts["the poisoned run stays closed (not requeued) while the worker keeps ticking"]


# ---- resource observation ----------------------------------------------------------------------------------------------------------

def test_sample_coverage_needs_samples_at_both_ends_without_a_long_gap():
    full = [(float(t), 4000) for t in range(0, 41, 2)]
    assert faults.sample_coverage(full, (0.0, 40.0)) == {"count": 21, "max_gap_seconds": 2.0}
    assert faults.sample_coverage([(5.0, 1), (6.0, 1)], (0.0, 40.0))["max_gap_seconds"] == 34.0  # nothing for most of the window
    assert faults.sample_coverage([], (0.0, 40.0)) == {"count": 0, "max_gap_seconds": 40.0}
    assert faults.sample_coverage([(0.0, 1), (40.0, 1)], (0.0, 40.0))["max_gap_seconds"] == 40.0
    gappy = [(float(t), 4000) for t in list(range(0, 11, 2)) + list(range(30, 41, 2))]
    assert faults.sample_coverage(gappy, (0.0, 40.0))["max_gap_seconds"] == 20.0


def test_observe_oom_reads_typed_flags_and_reports_every_unreadable_answer():
    answers = {"a": (0, "false", ""), "b": (0, "true", ""), "c": (1, "", "daemon down"), "d": (0, "", ""), "e": (0, "maybe", ""), "f": (-1, "", "TimeoutExpired")}
    with patch.object(faults.base, "docker_status", lambda *args, **kw: answers[args[-1]]):
        flags, errors = faults.observe_oom(list(answers))
    assert flags == {"a": False, "b": True} and len(errors) == 4


def scenario_args(**overrides):
    values = dict(allow_dirty=False, tenants=3, slots=2, image="x", prefix="bench", settle=0, min_vm_available_mib=1500, pdf_seconds=1, fault_timeout=1, sample_interval=2.0)
    values.update(overrides)
    return load.argparse.Namespace(**values)


class ImmediateThread:
    def __init__(self, target, **kw):
        self.target = target

    def start(self):
        self.target()

    def join(self, **kw):
        pass

    def is_alive(self):
        return False


class ThreeSamples:
    def __init__(self):
        self.n = 0
        self.stopped = False

    def is_set(self):
        return self.stopped or self.n >= 3

    def wait(self, _seconds):
        self.n += 1

    def set(self):
        self.stopped = True


class StubArm:
    names = ["t-a", "t-b", "t-c"]
    create_started = {}
    owned = {"container": [], "volume": [], "network": []}

    def start(self, gate=None):
        pass

    def wait_healthy(self):
        pass

    def stop(self):
        pass

    def cleanup_report(self):
        owned = {"container": 5, "volume": 4, "network": 1}
        return {"owned": owned, "removed_by_full_id": 10, "removed_ids_all_full": True, "already_absent": [], "not_ours": [], "owned_remaining": [], "unknown": [], "observation_errors": [],
                "unexpected_labelled": [], "foreign_collisions": [], "foreign_missing": [], "foreign_changed": [], "foreign_unstable_before_the_run": []}


def drive_scenario(memory_reads, oom_answer, scenarios_ok=True):
    """The review's fixture, against the real scenario(): the engine and the scenarios are replaced, the observation code is real."""
    keys = faults.SCENARIO_KEYS
    scenarios = {key: {"checks": [faults.check("holds", True)], "window": [0.0, 1.0]} for key in keys}
    scenarios["_oom_after_each_scenario"] = {"per_scenario": {key: {n: False for n in StubArm.names} for key in keys}, "errors": []}

    def gate(_args, _budget, _client, gates):
        gates.extend({"index": i, "name": n, "at": 100.0 + i, "decision": "admitted"} for i, n in enumerate(StubArm.names))
    arm = StubArm()
    arm.create_started = {n: 101.0 + i for i, n in enumerate(StubArm.names)}
    with patch.object(load, "git_state", return_value={"head": "x", "dirty": False}), patch.object(faults.base, "assert_local_engine"), patch.object(faults.pdfb, "Arm", return_value=arm), \
         patch("docker.DockerClient"), patch("tenant_budget.TenantBudget"), patch.object(faults.load, "make_gate", side_effect=gate), patch.object(faults.time, "sleep"), \
         patch.object(faults.load, "collect_image", return_value=({"id": "x"}, {}, {}, {}, [])), patch.object(faults.load, "image_failures", return_value=[]), \
         patch.object(faults.load, "vm_available", side_effect=memory_reads), patch.object(faults.threading, "Event", ThreeSamples), patch.object(faults.threading, "Thread", ImmediateThread), \
         patch.object(faults, "run_scenarios", return_value=scenarios), patch.object(faults.base, "docker_status", return_value=oom_answer):
        return faults.scenario(scenario_args())


def test_the_reviews_unreadable_memory_and_oom_counterexamples_are_no_longer_a_pass():
    for reads, answer, fragment in (([4000, RuntimeError("memory read unavailable"), RuntimeError("memory read unavailable")], (0, "false", ""), "memory reads failed"),
                                    ([4000, 4000, 4000], (0, "", ""), "UNKNOWN"), ([4000, 4000, 4000], (1, "", "daemon down"), "UNKNOWN"),
                                    ([4000, RuntimeError("x"), RuntimeError("x")], (0, "", ""), "memory reads failed")):
        result = drive_scenario(reads, answer)
        assert result["verdict"] == "FAIL" and any(fragment in line for line in result["failed_checks"]), (reads, answer, result["failed_checks"])


def test_complete_observations_with_a_single_short_window_are_still_a_failure_when_coverage_is_too_thin():
    result = drive_scenario([4000, 4000, 4000], (0, "false", ""))
    assert result["verdict"] == "FAIL" and any("not covered by memory samples" in line for line in result["failed_checks"])
