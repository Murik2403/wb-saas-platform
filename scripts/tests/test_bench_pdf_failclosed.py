"""Fail-closed ownership, cleanup and per-tenant admission of the benchmark Arm, against a fake Docker engine with fault injection.

The engine below behaves like the real one where it matters: ``volume create`` is idempotent (it returns an existing volume and keeps ITS
labels), ``inspect`` of a missing object is Docker's own "No such ..." answer, and every call can be made to fail or time out.
"""
import importlib.util
import itertools
import json
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / "provisioner"))
spec = importlib.util.spec_from_file_location("bench_pdf_slots_load_failclosed", SCRIPTS / "bench_pdf_slots_load.py")
load = importlib.util.module_from_spec(spec)
spec.loader.exec_module(load)
pdfb = load.pdfb
LABEL = pdfb.LABEL


class FakeEngine:
    def __init__(self):
        self.objects: dict[tuple[str, str], dict] = {}
        self.calls: list[tuple] = []
        self.counter = itertools.count(1)
        self.unavailable: set[str] = set()      # verbs whose every call fails like an unreachable daemon, e.g. {"inspect", "ps"}
        self.fail_rm = False                     # rm/rm -f answers an error and removes nothing
        self.lose_create_ack: set[str] = set()   # names whose create happens but whose answer is lost
        self.fail_create: set[str] = set()       # names whose create fails without creating anything

    # -- helpers -------------------------------------------------------------------------------------------------------
    def put(self, kind, name, label=None, ident=None, state="true 2026-01-01T00:00:00Z"):
        ident = ident or (name if kind == "volume" else f"{next(self.counter):064x}")
        self.objects[(kind, ident)] = {"name": name, "label": label, "state": state}
        return ident

    def find(self, kind, ref):
        for (k, ident), obj in self.objects.items():
            if k == kind and ref in (ident, obj["name"]):
                return ident, obj
        return None, None

    @staticmethod
    def label_of(args):
        for i, token in enumerate(args):
            if token == "--label" and args[i + 1].startswith(LABEL + "="):
                return args[i + 1].split("=", 1)[1]
        return None

    def ids(self, kind, args):
        wanted = next((a.split("=", 2)[2] for a in args if a.startswith("label=")), None)
        return [i for (k, i), o in self.objects.items() if k == kind and (wanted is None or o["label"] == wanted)]

    # -- the two entry points the harness uses -----------------------------------------------------------------------------
    def status(self, *args, timeout=120):
        self.calls.append(args)
        verb = args[0] if args[0] not in ("container", "volume", "network") else (args[0] + " " + args[1])
        head = args[0]
        if verb.split()[-1] in self.unavailable or head in self.unavailable:
            return 1, "", "Cannot connect to the Docker daemon at npipe:////./pipe/docker_engine. Is the docker daemon running?"
        if head == "ps":
            return 0, "\n".join(self.ids("container", args)), ""
        if head == "volume" and args[1] == "ls":
            return 0, "\n".join(self.ids("volume", args)), ""
        if head == "network" and args[1] == "ls":
            return 0, "\n".join(self.ids("network", args)), ""
        if head == "inspect":  # container states, several ids
            refs = [a for a in args[3:]]
            rows = []
            for ref in refs:
                ident, obj = self.find("container", ref)
                if ident is None:
                    return 1, "", f"Error response from daemon: No such container: {ref}"
                rows.append(f"{ident} {obj['state']}")
            return 0, "\n".join(rows), ""
        if head in ("container", "volume", "network") and args[1] == "inspect":
            ident, obj = self.find(head, args[2])
            if ident is None:
                return 1, "[]", {"container": f"Error response from daemon: No such container: {args[2]}", "volume": f"Error response from daemon: get {args[2]}: no such volume",
                                 "network": f"Error response from daemon: network {args[2]} not found"}[head]
            data = {"Id": ident, "Name": ("/" if head == "container" else "") + obj["name"]}
            labels = {LABEL: obj["label"]} if obj["label"] else {}
            data["Config" if head == "container" else "Labels"] = {"Labels": labels} if head == "container" else labels
            return 0, json.dumps([data]), ""
        if head == "rm":
            if self.fail_rm:
                return 1, "", "Error response from daemon: cannot remove container: device busy"
            for ref in [a for a in args[1:] if not a.startswith("-")]:
                ident, _ = self.find("container", ref)
                self.objects.pop(("container", ident), None)
            return 0, "", ""
        if head in ("network", "volume") and args[1] == "rm":
            if self.fail_rm:
                return 1, "", "Error response from daemon: remove failed: in use"
            ident, _ = self.find(head, args[-1])
            self.objects.pop((head, ident), None)
            return 0, "", ""
        return 0, "", ""

    def docker(self, *args, check=True, timeout=300):
        code, out, err = self.status(*args, timeout=timeout)
        head = args[0]
        name = args[args.index("--name") + 1] if "--name" in args else args[-1]
        if head in ("network", "volume") and args[1] == "create":
            if name in self.fail_create:
                raise RuntimeError("create failed")
            label = self.label_of(args)
            if head == "volume":
                if self.find("volume", name)[0] is None:  # idempotent: an existing volume is returned untouched, with ITS labels
                    self.put("volume", name, label)
                if name in self.lose_create_ack:
                    raise RuntimeError("timeout waiting for the daemon (create ACK lost)")
                return name
            ident = self.put("network", name, label)
            if name in self.lose_create_ack:
                raise RuntimeError("timeout waiting for the daemon (create ACK lost)")
            return ident
        if head == "run" and "-d" in args:
            if name in self.fail_create:
                raise RuntimeError("create failed")
            ident = self.put("container", name, self.label_of(args))
            if name in self.lose_create_ack:
                raise RuntimeError("timeout waiting for the daemon (create ACK lost)")
            return ident
        if check and code != 0:
            raise RuntimeError(f"docker failed: {err}")
        return out


def arm_args(tenants=3, **extra):
    return type("A", (), {"prefix": "bench", "tenants": tenants, "image": "img", "stability_seconds": 0, **extra})()


@pytest.fixture
def engine():
    fake = FakeEngine()
    with patch.object(pdfb.base, "docker", fake.docker), patch.object(pdfb.base, "docker_status", fake.status), patch.object(pdfb.time, "sleep"):
        yield fake


def new_arm(tenants=3, slots=2):
    return pdfb.Arm(arm_args(tenants), "load", slots)


def started(engine, tenants=3):
    arm = new_arm(tenants)
    arm.start()
    return arm


# ---- 1. a pre-existing volume is never adopted, chowned or seeded ----------------------------------------------------------

def test_a_preexisting_data_volume_with_the_name_of_a_tenant_is_refused_before_any_mutation(engine):
    arm = new_arm()
    engine.put("volume", f"{arm.names[1]}-data", label="customer")  # somebody else's volume under a name this run is about to use
    with pytest.raises(RuntimeError, match="refusing to start"):
        arm.start()
    assert not [c for c in engine.calls if c[0] == "run"], "no helper (chown/seed) and no container may run"
    assert ("volume", f"{arm.names[1]}-data") in engine.objects and engine.objects[("volume", f"{arm.names[1]}-data")]["label"] == "customer"
    assert not any(c[:2] == ("volume", "create") for c in engine.calls), "not even the other volumes were created before the refusal"


def test_a_volume_that_appears_between_the_name_check_and_the_create_is_not_used(engine):
    """The idempotent `volume create` returns the foreign volume; its label is not ours, so nothing may mount or chown it."""
    arm = new_arm(tenants=1)
    name = f"{arm.names[0]}-data"
    original = engine.docker

    def racing(*args, **kwargs):
        if args[:2] == ("volume", "create") and args[-1] == name:
            engine.put("volume", name, label="customer")  # created by someone else just before our create
        return original(*args, **kwargs)
    with patch.object(pdfb.base, "docker", racing):
        with pytest.raises(RuntimeError, match="not created by this run"):
            arm.start()
    chown = [c for c in engine.calls if c[0] == "run" and "chown" in c and f"{name}:/data" in c]
    assert chown == [], "the privileged chown helper must never run against a volume this run did not create"
    assert f"volume:{name}" in arm.collisions and ("volume", name) in engine.objects


def test_a_lost_create_answer_is_reconciled_by_the_reserved_name_and_the_volume_is_cleaned_up(engine):
    arm = new_arm(tenants=1)
    name = f"{arm.names[0]}-data"
    engine.lose_create_ack.add(name)
    with pytest.raises(RuntimeError, match="ACK lost"):
        arm.start()
    assert any(e["name"] == name for e in arm.owned["volume"]), "the volume that was created but not acknowledged is adopted so it is removed"
    arm.stop()
    report = arm.cleanup_report()
    assert ("volume", name) not in engine.objects and report["unknown"] == []


def test_a_lost_create_answer_for_a_foreign_object_is_reported_and_never_removed(engine):
    arm = new_arm(tenants=1)
    name = f"{arm.names[0]}-data"
    engine.lose_create_ack.add(name)
    original = engine.docker

    def foreign(*args, **kwargs):
        if args[:2] == ("volume", "create") and args[-1] == name:
            engine.put("volume", name, label="customer")  # not created by us at all
            raise RuntimeError("timeout")
        return original(*args, **kwargs)
    with patch.object(pdfb.base, "docker", foreign):
        with pytest.raises(RuntimeError):
            arm.start()
    arm.stop()
    assert ("volume", name) in engine.objects and f"volume:{name}" in arm.collisions
    assert load.cleanup_failures(arm.cleanup_report())


def test_a_lost_create_answer_with_an_unreachable_engine_leaves_the_state_unknown(engine):
    arm = new_arm(tenants=1)
    name = f"{arm.names[0]}-data"
    engine.lose_create_ack.add(name)
    original = engine.docker

    def then_unreachable(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        finally:
            if args[:2] == ("volume", "create") and args[-1] == name:
                engine.unavailable.add("volume")
    with patch.object(pdfb.base, "docker", then_unreachable):
        with pytest.raises(RuntimeError):
            arm.start()
    assert any(name in item for item in arm.unknown)


# ---- 2. cleanup is never a success on an error ---------------------------------------------------------------------------------

def test_a_normal_cleanup_is_confirmed_by_the_engine(engine):
    arm = started(engine)
    arm.stop()
    report = arm.cleanup_report()
    assert load.cleanup_failures(report) == []
    assert report["removed_by_full_id"] == sum(report["owned"].values()) and report["unknown"] == [] and report["owned_remaining"] == []
    assert not [k for k in engine.objects if engine.objects[k]["label"] == arm.run_id]


def test_a_failed_rm_with_unavailable_probes_is_not_a_clean_pass(engine):
    """The review's counterexample: `rm` fails and the daemon cannot be asked; the container is still there."""
    arm = started(engine, tenants=1)
    engine.fail_rm = True
    engine.unavailable.update({"container", "inspect", "ps"})
    arm.stop()
    report = arm.cleanup_report()
    assert any(("container", e["id"]) in engine.objects for e in arm.owned["container"]), "the fixture container is really still there"
    problems = load.cleanup_failures(report)
    assert problems and report["removed_by_full_id"] == 0
    assert report["unknown"] and report["observation_errors"], "the unanswerable engine is UNKNOWN, never 'absent'"


def test_a_failed_rm_with_a_working_engine_leaves_the_object_remaining(engine):
    arm = started(engine, tenants=1)
    engine.fail_rm = True
    arm.stop()
    report = arm.cleanup_report()
    assert report["owned_remaining"] and "rm status 1" in report["owned_remaining"][0] and report["removed_by_full_id"] == 0 and load.cleanup_failures(report)


def test_a_removal_counts_only_after_a_successful_authoritative_check(engine):
    """`rm` answers OK, but the follow-up inspection cannot be answered: not counted as removed, state UNKNOWN."""
    arm = started(engine, tenants=1)
    original = engine.status
    state = {"rm_done": False}

    def flaky(*args, **kwargs):
        if args[0] == "rm":
            state["rm_done"] = True
        if state["rm_done"] and args[0] == "container" and args[1] == "inspect":
            return 1, "", "error during connect: the pipe is gone"
        return original(*args, **kwargs)
    with patch.object(pdfb.base, "docker_status", flaky):
        arm.stop()
    assert arm.removed != [e for e in arm.removed if e["kind"] == "container"] or all(r["kind"] != "container" for r in arm.removed)
    assert any(item.startswith("container:") and "unverifiable" in item for item in arm.unknown)


def test_inspect_timeouts_are_unknown_not_absent(engine):
    with patch.object(pdfb.base, "docker_status", return_value=(-1, "", "TimeoutExpired: docker inspect")):
        assert pdfb.inspect_object("container", "x")[0] == "unknown"
    with patch.object(pdfb.base, "docker_status", return_value=(1, "[]", "Error response from daemon: No such container: x")):
        assert pdfb.inspect_object("container", "x")[0] == "absent"
    with patch.object(pdfb.base, "docker_status", return_value=(1, "", "permission denied while trying to connect to the Docker daemon socket")):
        assert pdfb.inspect_object("volume", "x")[0] == "unknown"


def test_an_inventory_that_cannot_be_read_raises_instead_of_looking_empty(engine):
    engine.unavailable.add("ps")
    with pytest.raises(pdfb.ObservationError):
        pdfb.inventory()


def test_a_cleanup_report_without_the_post_run_inventory_is_unknown(engine):
    arm = started(engine, tenants=1)
    arm.stop()
    engine.unavailable.add("ps")
    report = arm.cleanup_report()
    assert report["observation_errors"] and report["foreign_missing"] is None and load.cleanup_failures(report)


def test_an_object_that_vanished_before_cleanup_is_not_counted_as_removed_by_us(engine):
    arm = started(engine, tenants=1)
    victim = arm.owned["container"][0]
    engine.objects.pop(("container", victim["id"]))
    arm.stop()
    report = arm.cleanup_report()
    assert f"container:{victim['name']}" in report["already_absent"] and load.cleanup_failures(report)


# ---- ownership of what stays untouched -----------------------------------------------------------------------------------------

def test_cleanup_removes_only_recorded_objects_by_full_id_and_never_touches_foreign_ones(engine):
    engine.put("container", "someone-elses", label=None, ident="f" * 64)
    engine.put("volume", "customer-data")
    engine.put("network", "customer-net", ident="e" * 64)
    arm = started(engine)
    stray = engine.put("container", "stray", label=arm.run_id, ident="c" * 64)  # carries our label, we never recorded it
    arm.stop()
    report = arm.cleanup_report()
    assert ("container", "f" * 64) in engine.objects and ("volume", "customer-data") in engine.objects and ("network", "e" * 64) in engine.objects
    assert ("container", stray) in engine.objects and report["unexpected_labelled"] == ["container:" + stray] and load.cleanup_failures(report)
    removed = [c for c in engine.calls if c[0] == "rm"]
    assert removed and all(re.fullmatch(r"[0-9a-f]{64}", c[-1]) for c in removed), removed


def test_an_object_whose_label_changed_hands_is_not_removed(engine):
    arm = started(engine, tenants=1)
    entry = arm.owned["container"][0]
    engine.objects[("container", entry["id"])]["label"] = "somebody-else"
    arm.stop()
    report = arm.cleanup_report()
    assert ("container", entry["id"]) in engine.objects and report["not_ours"] and load.cleanup_failures(report)


def test_a_vanished_pre_existing_object_fails_the_cleanup(engine):
    engine.put("container", "customer", ident="f" * 64)
    arm = started(engine, tenants=1)
    arm.stop()
    engine.objects.pop(("container", "f" * 64))
    report = arm.cleanup_report()
    assert report["foreign_missing"] == ["f" * 64] and load.cleanup_failures(report)


def test_a_pre_existing_container_that_changed_state_fails_unless_it_was_already_unstable(engine):
    steady = engine.put("container", "steady", ident="a" * 64)
    flapping = engine.put("container", "restart-loop", ident="b" * 64)
    seen = itertools.count()
    original = engine.status

    def churn(*args, **kwargs):
        if args[0] == "inspect" and args[1] == "--format":
            engine.objects[("container", flapping)]["state"] = f"true t{next(seen)}"  # a restart loop: a new StartedAt every time it is read
        return original(*args, **kwargs)
    with patch.object(pdfb.base, "docker_status", churn):
        arm = started(engine, tenants=1)
        arm.stop()
        engine.objects[("container", steady)]["state"] = "false 2026-01-01T00:00:00Z"  # somebody stopped a stable container during the run
        report = arm.cleanup_report()
    assert report["foreign_unstable_before_the_run"] == [flapping]
    assert report["foreign_changed"] == [steady] and load.cleanup_failures(report), "the stable container's change is caught; the restart loop is only listed"


def test_an_unstable_container_is_listed_not_silently_whitelisted(engine):
    flapping = engine.put("container", "restart-loop", ident="b" * 64)
    seen = itertools.count()
    original = engine.status

    def churn(*args, **kwargs):
        if args[0] == "inspect" and args[1] == "--format":
            engine.objects[("container", flapping)]["state"] = f"true t{next(seen)}"
        return original(*args, **kwargs)
    with patch.object(pdfb.base, "docker_status", churn):
        arm = started(engine, tenants=1)
        arm.stop()
        report = arm.cleanup_report()
    assert report["foreign_unstable_before_the_run"] == [flapping] and report["foreign_changed"] == [] and load.cleanup_failures(report) == []


# ---- 3. admission and the memory floor before every create ---------------------------------------------------------------------

def test_the_gate_runs_before_each_tenants_first_object_and_a_refusal_stops_creation(engine):
    arm = new_arm(tenants=4)
    order = []

    def gate(index, name):
        order.append(("gate", index, len([c for c in engine.calls if c[:2] == ("volume", "create") and c[-1].endswith("-data")])))
        if index == 2:
            raise pdfb.StartRefused(index, {"code": "capacity_exhausted", "reason": "host_memory"})
        return lambda: order.append(("release", index))
    with pytest.raises(pdfb.StartRefused) as refusal:
        arm.start(gate=gate)
    assert refusal.value.index == 2
    created_data = [c[-1] for c in engine.calls if c[:2] == ("volume", "create") and c[-1].endswith("-data")]
    assert len(created_data) == 2 and set(arm.create_started) == set(arm.names[:2]), "tenants 3 and 4 were never allocated"
    assert [o for o in order if o[0] == "release"] and sorted(o[1] for o in order if o[0] == "release") == [0, 1]
    mutating = [" ".join(c) for c in engine.calls if c[0] == "run" or c[:2] in (("volume", "create"), ("network", "create"))]
    assert all(arm.names[i] not in "".join(mutating) for i in (2, 3)), "no create/run call ever named tenants 3 or 4"
    arm.stop()
    assert load.cleanup_failures(arm.cleanup_report()) == []


def test_the_gate_decision_precedes_the_creation_of_its_tenant(engine):
    arm = new_arm(tenants=2)
    stamps = {}

    def gate(index, name):
        stamps[name] = pdfb.time.time()
        return None
    arm.start(gate=gate)
    assert all(stamps[name] <= arm.create_started[name] for name in arm.names)


def test_the_real_budget_counts_tenants_that_are_still_being_created():
    """make_gate uses the production tenant_budget: a claim made for a tenant in flight is counted by the next decision."""
    from tenant_budget import TenantBudget

    class Client:
        containers = type("C", (), {"list": staticmethod(lambda **k: [])})()

        @staticmethod
        def info():
            return {"MemTotal": int((5 * 384 + 1) * 1048576 / 0.7)}  # a budget of 5 tenants and a bit

    args = type("A", (), {"min_vm_available_mib": 1500})()
    record: list = []
    with patch.object(load, "vm_available_helper", return_value=6000):
        gate = load.make_gate(args, TenantBudget(), Client, record)
        releases = [gate(i, f"t{i}") for i in range(5)]  # five claims, none released: all in flight
        with pytest.raises(pdfb.StartRefused) as refusal:
            gate(5, "t5")
    assert refusal.value.info["reason"] == "budget" and [r["decision"] for r in record] == ["admitted"] * 5 + ["refused"]
    assert all(callable(r) for r in releases)


def test_the_memory_floor_refuses_before_the_budget_does():
    from tenant_budget import TenantBudget

    class Client:
        containers = type("C", (), {"list": staticmethod(lambda **k: [])})()

        @staticmethod
        def info():
            return {"MemTotal": 64 * 1024 ** 3}

    args = type("A", (), {"min_vm_available_mib": 1500})()
    record: list = []
    with patch.object(load, "vm_available_helper", return_value=1800):  # 1800 - 384 = 1416 < 1500
        with pytest.raises(pdfb.StartRefused) as refusal:
            load.make_gate(args, TenantBudget(), Client, record)(0, "t0")
    assert refusal.value.info["reason"] == "host_memory" and record[0]["decision"] == "refused" and record[0]["vm_available_mib"] == 1800


def test_an_unreadable_vm_memory_refuses_instead_of_admitting():
    from tenant_budget import TenantBudget

    class Client:
        containers = type("C", (), {"list": staticmethod(lambda **k: [])})()

        @staticmethod
        def info():
            return {"MemTotal": 64 * 1024 ** 3}

    args = type("A", (), {"min_vm_available_mib": 1500})()
    with patch.object(load, "vm_available_helper", side_effect=pdfb.ObservationError("VM memory unreadable")):
        with pytest.raises(pdfb.StartRefused) as refusal:
            load.make_gate(args, TenantBudget(), Client, [])(0, "t0")
    assert refusal.value.info["reason"] == "measurement_unavailable"


def test_an_unanswerable_engine_at_cleanup_is_unknown_not_a_foreign_object(engine):
    arm = started(engine, tenants=1)
    engine.unavailable.update({"container", "volume", "network"})
    arm.stop()
    assert arm.unknown and all(item.startswith(("container:", "volume:", "network:", "label listing")) for item in arm.unknown)
    assert arm.not_ours == [] and arm.removed == []


def test_a_name_reserved_when_the_engine_was_down_is_reconciled_at_cleanup_and_removed(engine):
    arm = new_arm(tenants=1)
    name = f"{arm.names[0]}-data"
    engine.lose_create_ack.add(name)
    original = engine.docker

    def then_down(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        finally:
            if args[:2] == ("volume", "create") and args[-1] == name:
                engine.unavailable.add("volume")  # the reconcile right after the lost answer cannot be answered
    with patch.object(pdfb.base, "docker", then_down):
        with pytest.raises(RuntimeError):
            arm.start()
    assert not any(e["name"] == name for e in arm.owned["volume"]), "not adopted yet: the engine did not answer"
    engine.unavailable.clear()
    arm.stop()
    assert ("volume", name) not in engine.objects, "the leaked volume is found by its reserved name at cleanup and removed (it carries our label)"
