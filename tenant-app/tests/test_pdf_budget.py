"""PDF memory budget: OS-lock slots shared by tenants, durable run leases, queue behaviour and crash recovery.

Tenants are simulated by separate processes with their own data dir and one shared slot dir, like separate containers
that mount the same host directory. No real reports, data or network.
"""
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

import report_scheduler as scheduler
from reports import pdf_budget
from reports.pdf_budget import PdfBusy, pdf_slot, try_pdf_slot
from reports.store import MAX_ATTEMPTS, ReportStore

TENANT_APP = Path(__file__).resolve().parents[1]


@pytest.fixture
def host(tmp_path, monkeypatch):
    """This process is tenant A; WB_SAAS_PDF_SLOT_DIR is shared with the helper processes."""
    slots = tmp_path / "slots"
    slots.mkdir()
    monkeypatch.setenv("WB_SAAS_PDF_SLOT_DIR", str(slots))
    monkeypatch.setenv("WB_SAAS_PDF_SLOTS", "2")
    monkeypatch.setattr(pdf_budget, "DATA_DIR", tmp_path / "tenant-a")
    return slots


def other_tenant(tmp_path, name, body, *, env=None):
    """Run ``body`` as another tenant: its own data dir, the same slot dir."""
    code = "import sys; sys.path.insert(0, %r)\nfrom pathlib import Path\nfrom reports import pdf_budget\npdf_budget.DATA_DIR = Path(%r)\n" % (
        str(TENANT_APP), str(tmp_path / name)) + textwrap.dedent(body)
    return subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env={**os.environ, "WB_SAAS_DATA_DIR": str(tmp_path / name), **(env or {})})


HOLD = """
import time
from reports.pdf_budget import pdf_slot
with pdf_slot(wait=5):
    print("held", flush=True)
    time.sleep(60)
"""


def stop(*processes):
    for process in processes:
        process.kill()
        process.wait(timeout=15)  # the OS releases the lock when the process is gone, not at the moment of the kill


def hold(tmp_path, name):
    process = other_tenant(tmp_path, name, HOLD)
    assert process.stdout.readline().strip() == "held", process.stderr.read()
    return process


def test_host_slots_limit_how_many_tenants_render_at_once(tmp_path, host):
    first, second = hold(tmp_path, "t1"), hold(tmp_path, "t2")
    try:
        with pytest.raises(PdfBusy):  # both host slots are taken by other tenants
            with pdf_slot(wait=0):
                pass
        assert try_pdf_slot() is None
        assert pdf_budget.status() == {"host_slots": 2, "host_busy": 2, "configured": True, "healthy": True}
    finally:
        stop(first, second)


def test_a_slot_frees_when_its_holder_dies_without_any_cleanup(tmp_path, host):
    first, second = hold(tmp_path, "t1"), hold(tmp_path, "t2")
    try:
        stop(first)  # crash / OOM kill / container stop: no release code runs
        with pdf_slot(wait=5):
            assert pdf_budget.status()["host_busy"] == 2
    finally:
        stop(second)
    assert pdf_budget.status()["host_busy"] == 0


def test_waiting_gets_the_slot_as_soon_as_one_is_released(tmp_path, host):
    first, second = hold(tmp_path, "t1"), hold(tmp_path, "t2")
    threading.Timer(1.0, lambda: stop(first)).start()
    started = time.monotonic()
    try:
        with pdf_slot(wait=10):
            waited = time.monotonic() - started
        assert 0.8 <= waited < 6
    finally:
        stop(second)


def test_one_pdf_at_a_time_per_tenant_even_with_free_host_slots(tmp_path, host):
    with pdf_slot(wait=0):
        mine = other_tenant(tmp_path, "tenant-a", """
            from reports.pdf_budget import PdfBusy, pdf_slot
            try:
                with pdf_slot(wait=0):
                    print("got")
            except PdfBusy:
                print("busy")
        """)  # the same tenant (same data dir), another process: the API next to the worker
        out, err = mine.communicate(timeout=30)
        assert out.strip() == "busy", err
        assert pdf_budget.status()["host_busy"] == 1  # the other host slot stays free for other tenants


def test_the_slot_is_reentrant_for_the_holder_and_not_configured_means_tenant_only(tmp_path, monkeypatch):
    monkeypatch.delenv("WB_SAAS_PDF_SLOT_DIR", raising=False)
    monkeypatch.setattr(pdf_budget, "DATA_DIR", tmp_path / "solo")
    assert pdf_budget.status() == {"host_slots": 0, "host_busy": None, "configured": False, "healthy": None}
    with pdf_slot(wait=0):
        with pdf_slot(wait=0), try_pdf_slot():  # nested asks do not deadlock on the held slot
            assert pdf_budget.holds_slot()
    assert not pdf_budget.holds_slot()
    with try_pdf_slot():
        pass


def test_concurrent_tenants_never_exceed_the_slot_count(tmp_path, host):
    """Six tenants, five renders each, two slots: the recorded intervals never overlap more than twice."""
    logs = tmp_path / "intervals"
    logs.mkdir()
    body = f"""
        import time, os, json
        from reports.pdf_budget import pdf_slot
        spans = []
        for _ in range(5):
            with pdf_slot(wait=60):
                start = time.time(); time.sleep(0.05); end = time.time()
            spans.append([start, end])
        open({str(logs)!r} + "/" + str(os.getpid()) + ".json", "w").write(json.dumps(spans))
        """
    processes = [other_tenant(tmp_path, f"t{i}", body) for i in range(6)]
    for process in processes:
        assert process.wait(timeout=180) == 0, process.stderr.read()
    spans = [span for path in logs.iterdir() for span in json.loads(path.read_text())]
    assert len(spans) == 30
    events = sorted([(s, 1) for s, _ in spans] + [(e, -1) for _, e in spans], key=lambda x: (x[0], x[1]))
    depth = peak = 0
    for _, step in events:
        depth += step
        peak = max(peak, depth)
    assert peak <= 2, f"{peak} PDFs rendered at once with 2 slots"
    assert peak == 2, "the stress run never used both slots, so it proved nothing"


# ---- durable leases in the queue ------------------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    return ReportStore(tmp_path / "reports.sqlite3")


def definition(store, name="daily"):
    return store.add_definition(name=name, metrics=["stocks"], schedule_type="daily", schedule_time="23:59")


def row(store, run_id):
    return next(r for r in store.list_runs(limit=500) if r["id"] == run_id)


def raw(store, sql, *params):
    import sqlite3
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params)]


def test_claim_leases_the_run_and_finish_releases_it(store):
    run = store.enqueue_run(definition(store))
    claimed = store.claim_next_queued(owner="w1", lease_seconds=120)
    assert claimed["run_id"] == run
    (state,) = raw(store, "SELECT status, attempts, lease_owner, lease_expires_at FROM report_runs WHERE id=?", run)
    assert state["status"] == "running" and state["attempts"] == 1 and state["lease_owner"] == "w1" and state["lease_expires_at"]
    store.finish_run(run, status="ok", file_path="x.pdf")
    (done,) = raw(store, "SELECT lease_owner, lease_expires_at FROM report_runs WHERE id=?", run)
    assert done["lease_owner"] is None and done["lease_expires_at"] is None


def test_a_dead_worker_lease_expires_and_the_run_is_requeued_without_a_restart(store):
    run = store.enqueue_run(definition(store))
    store.claim_next_queued(owner="dead-worker")
    assert store.requeue_expired_leases() == 0  # lease still valid: untouched
    raw_expire(store, run)
    assert store.requeue_expired_leases() == 1
    assert row(store, run)["status"] == "queued" and store.has_queued()
    again = store.claim_next_queued(owner="w2")
    assert again["run_id"] == run
    assert raw(store, "SELECT attempts FROM report_runs WHERE id=?", run)[0]["attempts"] == 2


def raw_expire(store, run):
    import sqlite3
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE report_runs SET lease_expires_at='2000-01-01T00:00:00' WHERE id=?", (run,))


def test_a_run_that_keeps_killing_its_worker_is_closed_instead_of_looping_forever(store):
    run = store.enqueue_run(definition(store))
    for attempt in range(MAX_ATTEMPTS):
        assert store.claim_next_queued(owner=f"w{attempt}")["run_id"] == run
        raw_expire(store, run)
        store.requeue_expired_leases()
    final = row(store, run)
    assert final["status"] == "error" and "прервана" in final["error"] and not store.has_queued()
    assert store.list_definitions()[0]['last_run_at'] is not None


def test_three_crashes_do_not_restart_a_new_automatic_poison_cycle(tmp_path,host,store,monkeypatch):
    definition_id=store.add_definition(name='poison schedule',metrics=['stocks'],schedule_type='daily',schedule_time='00:00')
    run=store.start_run(definition_id,delivery_requested=True)
    for attempt in range(MAX_ATTEMPTS):
        if attempt:store.claim_next_queued(owner='retry-worker')
        raw_expire(store,run)
        store.requeue_expired_leases()
    monkeypatch.setattr(scheduler,'ReportStore',lambda *args:store)
    monkeypatch.setattr(scheduler,'DB_PATH',store.db_path)
    monkeypatch.setattr(scheduler,'build_report_pdf',lambda *args:pytest.fail('A poisoned daily run must not restart automatically'))
    assert scheduler.check_once()==0
    assert [item['id'] for item in store.list_runs()]==[run]
    assert row(store,run)['status']=='error'
    assert store.enqueue_run(definition_id)!=run  # explicit manual retry still works


def test_startup_recovery_requeues_expired_runs_but_respects_the_attempt_cap(store):
    first, second = store.enqueue_run(definition(store, "a")), store.enqueue_run(definition(store, "b"))
    store.claim_next_queued(owner="w")
    store.claim_next_queued(owner="w")
    import sqlite3
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE report_runs SET attempts=? WHERE id=?", (MAX_ATTEMPTS, second))
    raw_expire(store, first)
    raw_expire(store, second)
    assert store.requeue_interrupted_runs() == 1
    assert row(store, first)["status"] == "queued" and row(store, second)["status"] == "error"


def test_worker_restart_preserves_active_direct_api_lease_and_recovers_legacy_rows(store):
    current = store.start_run(definition(store, "active API"))
    legacy = store.start_run(definition(store, "pre-lease"))
    import sqlite3
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE report_runs SET attempts=0,lease_owner=NULL,lease_expires_at=NULL WHERE id=?", (legacy,))
    fresh_worker = ReportStore(store.db_path)
    assert fresh_worker.requeue_interrupted_runs() == 1
    assert row(store, current)["status"] == "running"
    assert row(store, legacy)["status"] == "queued"


def test_fresh_crash_lease_does_not_spawn_duplicate_scheduled_generation(store, monkeypatch):
    definition_id = definition(store, "crashed schedule")
    run = store.start_run(definition_id, delivery_requested=True, owner="dead-worker")
    fresh_worker = ReportStore(store.db_path)
    assert fresh_worker.requeue_interrupted_runs() == 0
    monkeypatch.setattr(scheduler, "try_pdf_slot", lambda: pytest.fail("Pending schedule must not start a second run"))
    assert scheduler.run_definition(fresh_worker, fresh_worker.list_definitions()[0]) is False
    assert [item["id"] for item in fresh_worker.list_runs()] == [run]
    raw_expire(fresh_worker, run)
    assert fresh_worker.requeue_expired_leases() == 1
    assert fresh_worker.has_pending_generation(definition_id)


def test_stale_attempt_cannot_heartbeat_or_finish_a_replacement_lease(store):
    run = store.enqueue_run(definition(store))
    store.claim_next_queued(owner="same-worker")
    old_owner, old_attempt = store.lease_token(run)
    raw_expire(store, run)
    assert store.requeue_expired_leases() == 1
    newer = ReportStore(store.db_path)
    newer.claim_next_queued(owner="same-worker")  # owner name can repeat; attempt is the fencing token
    before = row(newer, run)
    assert not store.heartbeat(run, old_owner, attempt=old_attempt)
    assert store.finish_run(run, status="ok", file_path="stale.pdf", owner=old_owner, attempt=old_attempt) is False
    assert row(newer, run) == before
    assert newer.finish_run(run, status="ok", file_path="new.pdf") is True


def test_heartbeat_extends_only_a_running_lease_and_expired_ones_stay_recoverable(store):
    run = store.enqueue_run(definition(store))
    store.claim_next_queued(owner="w1", lease_seconds=1)
    before = raw(store, "SELECT lease_expires_at FROM report_runs WHERE id=?", run)[0]["lease_expires_at"]
    assert store.heartbeat(run, "w1", lease_seconds=600)
    assert raw(store, "SELECT lease_expires_at FROM report_runs WHERE id=?", run)[0]["lease_expires_at"] > before
    assert not store.heartbeat(run, "someone-else")
    store.finish_run(run, status="ok")
    assert not store.heartbeat(run, "w1")


# ---- the scheduler honours the budget -------------------------------------------------------------------------------

@pytest.fixture
def scheduler_env(tmp_path, monkeypatch, store, host):
    monkeypatch.setattr(scheduler, "REPORTS_DIR", tmp_path / "out")
    calls = []

    def fake_pdf(name, metric_codes, start, end):
        calls.append((name, pdf_budget.holds_slot()))
        return b"%PDF-1.4 fake"
    monkeypatch.setattr(scheduler, "build_report_pdf", fake_pdf)
    monkeypatch.setattr(scheduler, "DB_PATH", store.db_path)  # check_once opens the tenant database itself
    import types
    monkeypatch.setitem(sys.modules, "shop_schedule_runner", types.SimpleNamespace(run_one=lambda: None))
    return calls


def test_queued_runs_wait_untouched_while_every_slot_is_busy_then_run(tmp_path, host, store, scheduler_env):
    run = store.enqueue_run(definition(store))
    first, second = hold(tmp_path, "t1"), hold(tmp_path, "t2")
    try:
        assert scheduler.process_queued(store) == 0
        state = raw(store, "SELECT status, attempts, lease_owner FROM report_runs WHERE id=?", run)[0]
        assert state == {"status": "queued", "attempts": 0, "lease_owner": None}  # never claimed, no attempt burned
        assert scheduler_env == []
    finally:
        stop(first, second)
    time.sleep(0.3)
    assert scheduler.process_queued(store) == 1
    done = row(store, run)
    assert done["status"] == "ok" and Path(done["file_path"]).read_bytes() == b"%PDF-1.4 fake"
    assert scheduler_env == [("daily", True)]  # built inside the held slot


def test_a_due_schedule_stays_due_when_no_slot_is_free(tmp_path, host, store, scheduler_env):
    d = store.add_definition(name="due", metrics=["stocks"], schedule_type="daily", schedule_time="00:00")
    first, second = hold(tmp_path, "t1"), hold(tmp_path, "t2")
    try:
        assert scheduler.check_once(deliver=False) == 0
        assert store.list_runs() == [] and store.list_definitions()[0].get("last_run_at") in (None, "")
    finally:
        stop(first, second)
    time.sleep(0.3)
    assert scheduler.check_once(deliver=False) == 1
    assert [r["status"] for r in store.list_runs()] == ["ok"]


def test_delivery_never_holds_a_pdf_slot(tmp_path, host, store, scheduler_env, monkeypatch):
    seen = []
    monkeypatch.setattr(scheduler, "deliver_report", lambda *a, **k: seen.append(pdf_budget.holds_slot()) or {})
    store.enqueue_run(definition(store), delivery_requested=True)
    assert scheduler.process_queued(store) == 1
    assert seen == [False]


def test_a_worker_that_died_mid_build_is_recovered_by_the_next_tick(tmp_path, host, store, scheduler_env):
    run = store.enqueue_run(definition(store))
    store.claim_next_queued(owner="worker-that-was-oom-killed")  # claimed, then the process vanished
    raw_expire(store, run)
    assert scheduler.process_queued(store) == 1
    assert row(store, run)["status"] == "ok"
    assert raw(store, "SELECT attempts FROM report_runs WHERE id=?", run)[0]["attempts"] == 2


def test_busy_slot_inside_a_request_is_an_error_run_that_does_not_burn_the_schedule(tmp_path, host, store, monkeypatch):
    monkeypatch.setattr(scheduler, "REPORTS_DIR", tmp_path / "out")
    monkeypatch.setattr(scheduler, "build_report_pdf", lambda *a: (_ for _ in ()).throw(PdfBusy("busy")))
    d = definition(store)
    with pytest.raises(PdfBusy):
        scheduler.generate_and_save(store, store.list_definitions()[0])
    assert store.list_definitions()[0].get("last_run_at") in (None, "")  # today's schedule is still due
    assert [r["status"] for r in store.list_runs()] == ["error"]


def test_the_lease_is_renewed_while_a_slow_build_runs(tmp_path, host, store, monkeypatch):
    monkeypatch.setattr(scheduler, "REPORTS_DIR", tmp_path / "out")
    monkeypatch.setattr(scheduler, "LEASE_SECONDS", 0.6)  # renewal every 0.2 s
    observed = []

    def slow(name, codes, start, end):
        run = store.list_runs()[0]["id"]
        for _ in range(8):
            time.sleep(0.25)
            observed.append(raw(store, "SELECT lease_expires_at FROM report_runs WHERE id=?", run)[0]["lease_expires_at"])
        return b"%PDF"
    monkeypatch.setattr(scheduler, "build_report_pdf", slow)
    d = definition(store)
    scheduler.generate_and_save(store, store.list_definitions()[0])
    assert len(set(observed)) > 1, "the lease was never renewed during a 2 s build"


def test_a_due_schedule_with_delivery_waits_for_a_slot_and_delivers_outside_it(tmp_path, host, store, scheduler_env, monkeypatch):
    """The delivering branch (run_definition): no slot -> no run recorded and still due; with a slot -> built inside, delivered outside."""
    seen = []
    monkeypatch.setattr(scheduler, "deliver_report", lambda *a, **k: seen.append(pdf_budget.holds_slot()) or {})
    store.add_definition(name="due-mail", metrics=["stocks"], schedule_type="daily", schedule_time="00:00", email_enabled=True)
    first, second = hold(tmp_path, "t1"), hold(tmp_path, "t2")
    try:
        assert scheduler.check_once(deliver=True) == 0
        assert store.list_runs() == [] and seen == []
    finally:
        stop(first, second)
    time.sleep(0.3)
    assert scheduler.check_once(deliver=True) == 1
    assert seen == [False] and scheduler_env == [("due-mail", True)]
    assert [r["status"] for r in store.list_runs()] == ["ok"]


def test_generate_now_route_answers_503_when_no_slot_is_free_and_creates_no_run(tmp_path, host, store, monkeypatch):
    import api_server
    from fastapi import HTTPException
    monkeypatch.setattr(api_server, "DB_PATH", store.db_path)
    definition(store)
    first, second = hold(tmp_path, "t1"), hold(tmp_path, "t2")
    monkeypatch.setattr(pdf_budget, "POLL_SECONDS", 0.05)
    try:
        started = time.monotonic()
        with pytest.raises(HTTPException) as busy:
            real_slot = pdf_budget.pdf_slot
            monkeypatch.setattr(pdf_budget, "pdf_slot", lambda wait=0.0: real_slot(wait=0.3))  # keep the test fast: the route asks for 15 s
            api_server.generate_report(store.list_definitions()[0]["id"])
        assert busy.value.status_code == 503 and busy.value.headers == {"Retry-After": "30"}
        assert time.monotonic() - started < 10
        assert store.list_runs() == []  # nothing was started for a request that never got a slot
    finally:
        stop(first, second)


def test_configured_unusable_shared_directory_refuses_render_and_releases_tenant_slot(tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-directory"
    blocker.write_bytes(b"a file where the slot directory should be")
    monkeypatch.setenv("WB_SAAS_PDF_SLOT_DIR", str(blocker / "slots"))
    monkeypatch.setenv("WB_SAAS_PDF_SLOTS", "2")
    monkeypatch.setattr(pdf_budget, "DATA_DIR", tmp_path / "tenant")
    assert try_pdf_slot() is None
    with pytest.raises(PdfBusy):
        with pdf_slot(wait=0):
            pytest.fail("Configured shared budget must not be bypassed")
    assert pdf_budget.status() == {"host_slots": 2, "host_busy": None, "configured": True, "healthy": False}
    monkeypatch.delenv("WB_SAAS_PDF_SLOT_DIR")
    with pdf_slot(wait=0):
        assert pdf_budget.holds_slot()  # refused host admission did not leak the tenant lock


def test_configured_missing_directory_is_not_created_or_bypassed(tmp_path, monkeypatch):
    slots = tmp_path / "missing-shared-mount"
    monkeypatch.setenv("WB_SAAS_PDF_SLOT_DIR", str(slots))
    monkeypatch.setattr(pdf_budget, "DATA_DIR", tmp_path / "tenant")
    assert try_pdf_slot() is None
    assert not slots.exists()
    assert pdf_budget.status()["configured"] is True
    assert pdf_budget.status()["healthy"] is False


def test_status_observes_existing_shared_slots_without_creating_files(tmp_path, host):
    assert list(host.iterdir()) == []
    assert pdf_budget.status() == {"host_slots": 2, "host_busy": 0, "configured": True, "healthy": True}
    assert list(host.iterdir()) == []
    with pdf_slot(wait=0):
        assert pdf_budget.status()["host_busy"] == 1


def test_direct_generation_holds_admission_until_file_save_and_completion(tmp_path, host, store, monkeypatch):
    definition(store)
    monkeypatch.setattr(scheduler, "REPORTS_DIR", tmp_path / "output")
    monkeypatch.setattr(scheduler, "build_report_pdf", lambda *args: b"%PDF dummy")
    writing, release = threading.Event(), threading.Event()
    real_write = Path.write_bytes
    errors = []
    def paused_write(path, value):
        if path.suffix == ".pdf":
            assert pdf_budget.holds_slot()
            writing.set()
            assert release.wait(10)
        return real_write(path, value)
    monkeypatch.setattr(Path, "write_bytes", paused_write)
    def generate():
        try:
            scheduler.generate_and_save(store, store.list_definitions()[0])
        except BaseException as error:
            errors.append(error)
    worker = threading.Thread(target=generate)
    worker.start()
    try:
        assert writing.wait(10)
        assert ReportStore(store.db_path).requeue_interrupted_runs() == 0
        assert try_pdf_slot() is None  # the same tenant cannot begin a new PDF in the file-save window
        assert row(store, store.list_runs()[0]["id"])["status"] == "running"
    finally:
        release.set()
        worker.join(timeout=10)
    assert not worker.is_alive() and errors == []
    assert store.list_runs()[0]["status"] == "ok"


def test_direct_generator_takes_admission_before_creating_a_run(tmp_path, host, store, monkeypatch):
    definition(store)
    first, second = hold(tmp_path, "blocking1"), hold(tmp_path, "blocking2")
    monkeypatch.setattr(scheduler, "pdf_slot", lambda wait=0: pdf_slot(wait=0))
    try:
        with pytest.raises(PdfBusy):
            scheduler.generate_and_save(store, store.list_definitions()[0])
        assert store.list_runs() == []
    finally:
        stop(first, second)


def test_private_shop_child_contends_for_the_same_cabinet_slot(tmp_path, host):
    with pdf_slot(wait=0):
        child = other_tenant(tmp_path, "tenant-a/.shop-workspace-private", """
            from reports.pdf_budget import PdfBusy, pdf_slot
            try:
                with pdf_slot(wait=0):
                    print('unexpected-acquisition')
            except PdfBusy:
                print('busy')
        """, env={"MH_INTERNAL_PDF_TENANT_DIR": str(tmp_path / "tenant-a")})
        output, error = child.communicate(timeout=15)
        assert child.returncode == 0 and output.strip() == "busy", error


def test_internal_cabinet_slot_path_requires_the_actual_private_child_parent(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_budget, "DATA_DIR", tmp_path / ".shop-workspace-child")
    monkeypatch.setenv("MH_INTERNAL_PDF_TENANT_DIR", str(tmp_path / "foreign"))
    with pytest.raises(ValueError, match="internal cabinet"):
        try_pdf_slot()
