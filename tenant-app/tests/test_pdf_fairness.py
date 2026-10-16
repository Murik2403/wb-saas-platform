"""Queued background work has priority over requests for the shared PDF slots, and a request answers within its deadline.

Tenants are separate processes (own data dir, one shared slot dir), like containers mounting the same host directory. Builds are
simulated by holding the slot for a fraction of a second; no real report is rendered.
"""
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from reports import pdf_budget
from reports.pdf_budget import PdfBusy, announce_background_waiting, background_waiting, interactive_pdf, pdf_slot, withdraw_background_waiting

TENANT_APP = Path(__file__).resolve().parents[1]


@pytest.fixture
def host(tmp_path, monkeypatch):
    slots = tmp_path / "slots"
    slots.mkdir()
    monkeypatch.setenv("WB_SAAS_PDF_SLOT_DIR", str(slots))
    monkeypatch.setenv("WB_SAAS_PDF_SLOTS", "2")
    monkeypatch.setattr(pdf_budget, "DATA_DIR", tmp_path / "tenant-a")
    monkeypatch.setattr(pdf_budget, "POLL_SECONDS", 0.02)
    yield slots
    withdraw_background_waiting()


def tenant_process(tmp_path, name, body):
    code = "import sys; sys.path.insert(0, %r)\nfrom pathlib import Path\nfrom reports import pdf_budget\npdf_budget.DATA_DIR = Path(%r)\npdf_budget.POLL_SECONDS = 0.02\n" % (
        str(TENANT_APP), str(tmp_path / name)) + textwrap.dedent(body)
    return subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env={**os.environ, "WB_SAAS_DATA_DIR": str(tmp_path / name)})


def stop(*processes):
    for process in processes:
        process.kill()
        process.wait(timeout=15)


# ---- the beacon -------------------------------------------------------------------------------------------------------------------

def test_no_beacon_means_requests_are_not_held_back(host):
    assert background_waiting() is False
    assert interactive_pdf(lambda: "pdf", wait=1) == "pdf"


def test_a_beacon_is_visible_while_held_and_gone_after_withdrawal(host):
    assert announce_background_waiting() is True and announce_background_waiting() is True  # idempotent
    assert len(list(host.glob("pdf-bg-wait-*.lock"))) == 1 and background_waiting() is True
    withdraw_background_waiting()
    assert background_waiting() is False and not list(host.glob("pdf-bg-wait-*.lock"))


def test_a_beacon_of_a_dead_process_disappears_with_it_without_any_cleanup(tmp_path, host):
    process = tenant_process(tmp_path, "t1", """
        from reports.pdf_budget import announce_background_waiting
        assert announce_background_waiting()
        print("announced", flush=True)
        import time; time.sleep(60)
    """)
    try:
        assert process.stdout.readline().strip() == "announced", process.stderr.read()
        assert background_waiting() is True
    finally:
        stop(process)
    assert background_waiting() is False  # the OS released the lock with the process


def test_an_old_unlocked_beacon_file_is_removed_as_a_leftover(host, monkeypatch):
    leftover = host / "pdf-bg-wait-1-dead.lock"
    leftover.write_bytes(b"0")
    old = time.time() - pdf_budget.STALE_BEACON_SECONDS - 5
    os.utime(leftover, (old, old))
    fresh = host / "pdf-bg-wait-2-new.lock"
    fresh.write_bytes(b"0")  # just created and not yet locked: must survive, its creator is about to lock it
    assert background_waiting() is False
    assert not leftover.exists() and fresh.exists()


def test_without_a_slot_directory_there_is_no_beacon_and_no_priority(tmp_path, monkeypatch):
    monkeypatch.delenv("WB_SAAS_PDF_SLOT_DIR", raising=False)
    monkeypatch.setattr(pdf_budget, "DATA_DIR", tmp_path / "t")
    assert announce_background_waiting() is False and background_waiting() is False


# ---- requests yield to queued background work ----------------------------------------------------------------------------------

def test_a_request_is_refused_at_once_while_background_work_waits_and_never_builds(tmp_path, host):
    waiter = tenant_process(tmp_path, "t1", """
        from reports.pdf_budget import announce_background_waiting
        announce_background_waiting(); print("announced", flush=True)
        import time; time.sleep(60)
    """)
    try:
        assert waiter.stdout.readline().strip() == "announced", waiter.stderr.read()
        started = time.monotonic()
        with pytest.raises(PdfBusy, match="очереди"):
            interactive_pdf(lambda: pytest.fail("a request must not build while queued reports wait"), wait=15)
        assert time.monotonic() - started < 1.0, "the refusal is immediate, it does not poll for 15 s"
    finally:
        stop(waiter)


def test_a_request_that_is_already_polling_gives_up_when_background_work_appears(tmp_path, host):
    holders = [tenant_process(tmp_path, f"h{i}", """
        import time
        from reports.pdf_budget import pdf_slot
        with pdf_slot(wait=5):
            print("held", flush=True)
            time.sleep(60)
    """) for i in range(2)]
    try:
        for holder in holders:
            assert holder.stdout.readline().strip() == "held", holder.stderr.read()
        threading.Timer(0.3, announce_background_waiting).start()
        started = time.monotonic()
        with pytest.raises(PdfBusy, match="очереди"):
            interactive_pdf(lambda: pytest.fail("must not build"), wait=15)
        assert time.monotonic() - started < 3.0
    finally:
        stop(*holders)


def test_a_waiting_tenants_own_requests_are_held_back_too(tmp_path, host):
    assert announce_background_waiting()
    with pytest.raises(PdfBusy):
        interactive_pdf(lambda: "pdf", wait=1)


STARVATION = """
import json, sys, threading, time
from reports import pdf_budget
from reports.pdf_budget import PdfBusy, interactive_pdf, try_pdf_slot, announce_background_waiting, withdraw_background_waiting
role, count, hold, seconds = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
t0 = time.monotonic()
if role == "background":  # one tenant's worker: one queued run, one try per tick; a beacon while it waits
    done = None
    while time.monotonic() - t0 < seconds and done is None:
        slot = try_pdf_slot()
        if slot is None:
            announce_background_waiting()
            time.sleep(0.25)  # the tick
            continue
        with slot:
            time.sleep(hold)
        done = round(time.monotonic() - t0, 2)
    withdraw_background_waiting()
    print(json.dumps({"role": role, "done": done}), flush=True)
else:  # a tenant's requests, one after another
    ok = refused = 0
    while time.monotonic() - t0 < seconds:
        try:
            interactive_pdf(lambda: time.sleep(hold), wait=1.0)
            ok += 1
        except PdfBusy:
            refused += 1
            time.sleep(0.05)
    print(json.dumps({"role": role, "ok": ok, "refused": refused}), flush=True)
"""


def test_background_queue_is_not_starved_by_a_constant_stream_of_requests(tmp_path, host):
    """The real-run failure at small scale: 6 background workers with one queued run each, 6 tenants whose requests never stop, 2 slots.

    Every background run must finish well inside the window (a build takes 0.3 s, so 6 runs on 2 slots need about 1 s of slot time);
    requests are refused while the queue is waiting and are served again once it has drained."""
    window = 12.0
    background = [tenant_process(tmp_path, f"bg{i}", f"import sys; sys.argv = ['x', 'background', '1', '0.3', '{window}']\n" + STARVATION) for i in range(6)]
    requests = [tenant_process(tmp_path, f"rq{i}", f"import sys; sys.argv = ['x', 'requests', '1', '0.3', '{window}']\n" + STARVATION) for i in range(6)]
    try:
        import json
        results = []
        for process in background + requests:
            out, err = process.communicate(timeout=window + 30)
            assert process.returncode == 0, err
            results.append(json.loads(out.strip().splitlines()[-1]))
    finally:
        stop(*[p for p in background + requests if p.poll() is None])
    done = [r["done"] for r in results if r["role"] == "background"]
    served = [r for r in results if r["role"] == "requests"]
    assert all(d is not None for d in done), f"a background run was starved: {done}"
    assert max(done) < 6.0, f"the queue took {max(done)} s to drain 6 builds on 2 slots: {done}"
    assert sum(r["refused"] for r in served) > 0, "requests must have been refused while the queue waited"
    assert sum(r["ok"] for r in served) > 0, "requests are served again once the queue has drained"


# ---- the request deadline ----------------------------------------------------------------------------------------------------------

def test_a_build_that_outlives_the_deadline_is_answered_busy_in_time_and_keeps_its_slot_until_it_ends(tmp_path, host):
    release = threading.Event()
    finished = threading.Event()

    def slow():
        release.wait(30)
        finished.set()
        return "late"
    started = time.monotonic()
    with pytest.raises(PdfBusy, match="дольше допустимого"):
        interactive_pdf(slow, wait=1, deadline=0.4)
    assert time.monotonic() - started < 2.0
    with pytest.raises(PdfBusy):  # the abandoned build still holds this tenant's PDF slot: no second PDF in this tenant meanwhile
        with pdf_slot(wait=0):
            pass
    release.set()
    assert finished.wait(5)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:  # the thread releases the slot right after the build returns
        try:
            with pdf_slot(wait=0):
                break
        except PdfBusy:
            time.sleep(0.05)
    else:
        pytest.fail("the slot was never released after the abandoned build ended")


def test_a_request_abandoned_while_waiting_for_a_slot_never_builds(tmp_path, host):
    holders = [tenant_process(tmp_path, f"h{i}", """
        import time
        from reports.pdf_budget import pdf_slot
        with pdf_slot(wait=5):
            print("held", flush=True)
            time.sleep(2.0)
    """) for i in range(2)]
    built = []
    try:
        for holder in holders:
            assert holder.stdout.readline().strip() == "held", holder.stderr.read()
        with pytest.raises(PdfBusy):
            interactive_pdf(lambda: built.append("built"), wait=30, deadline=0.3)
        for holder in holders:
            holder.wait(timeout=15)  # the slots are free now: an abandoned request must not take them and build
        time.sleep(1.0)
        assert built == []
    finally:
        stop(*[h for h in holders if h.poll() is None])


def test_an_error_inside_the_build_reaches_the_caller_unchanged(host):
    with pytest.raises(ValueError, match="bad period"):
        interactive_pdf(lambda: (_ for _ in ()).throw(ValueError("bad period")), wait=1)


def test_the_deadline_is_below_the_proxy_timeout_with_room_for_the_response():
    assert pdf_budget.INTERACTIVE_DEADLINE_SECONDS <= 55
    assert pdf_budget.INTERACTIVE_DEADLINE_SECONDS > 15


def test_a_request_abandoned_at_the_instant_its_slot_is_granted_does_not_build(host, monkeypatch):
    """The race the polling check cannot see: the slot is granted after the caller already gave up."""
    from contextlib import contextmanager
    built = []
    slot_granted = threading.Event()
    proceed = threading.Event()

    @contextmanager
    def late_slot(wait=0.0):
        proceed.wait(5)  # the grant arrives only after the request's deadline
        slot_granted.set()
        yield
    monkeypatch.setattr(pdf_budget, "pdf_slot", late_slot)
    threading.Timer(0.5, proceed.set).start()
    with pytest.raises(PdfBusy):
        interactive_pdf(lambda: built.append("built"), wait=30, deadline=0.2)
    assert slot_granted.wait(5)
    time.sleep(0.3)
    assert built == []
