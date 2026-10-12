"""Real WAL snapshots and concurrent HTTP reads, not mocked SQLite locking."""
import asyncio
from contextlib import closing
import json
import sqlite3
import threading
import time

import httpx
import pandas as pd
import pytest

from test_cashflow_api import api
from db import core
from db import wb_fifo_reconciliation as fifo


@pytest.fixture
def guard_db(tmp_path, monkeypatch):
    path = tmp_path / "guard.sqlite3"
    monkeypatch.setattr(core, "DB_PATH", path)
    monkeypatch.setattr(api, "DB_PATH", path)
    monkeypatch.setattr(api, "IS_DEMO", False)
    monkeypatch.setattr(api, "TENANT_SLUG", "")
    core.init_db()
    with core.connect() as conn:
        conn.execute("INSERT INTO product_pipeline(nm_id,local_known,ready_units,inbound_known,inbound_units) VALUES(901,1,8,0,0)")
    # Unrelated presentation dependencies cannot cause provider/file activity.
    # The actual route, guard, valuation reads and SQLite writes remain real.
    monkeypatch.setattr(api, "load_settings", lambda: {})
    monkeypatch.setattr(api, "get_token", lambda: None)
    monkeypatch.setattr(api, "last_sync", lambda: {"finished_at": "2026-10-13T12:00:00"})
    monkeypatch.setattr(api, "latest_backup", lambda: None)
    monkeypatch.setattr(api, "build_data_quality_overview", lambda *a: (pd.DataFrame(), {}, pd.DataFrame(), {}))
    # A single worker's heap lease now serializes this heavy endpoint. Bypass
    # ONLY that lease here to reproduce separate workers/processes sharing
    # SQLite; the real route, auth middleware, SQL and WAL snapshots remain.
    import dashboard_work_guard
    actual_guarded = dashboard_work_guard.guarded_request
    monkeypatch.setattr(dashboard_work_guard, "guarded_request", lambda scope:
                        False if scope.get("path") == "/api/v1/control/data-quality"
                        else actual_guarded(scope))
    return path


def read_guard(path):
    with closing(sqlite3.connect(path)) as conn:
        row = conn.execute("SELECT value FROM app_meta WHERE key=?", (fifo.FIFO_SYNC_GUARD_META_KEY,)).fetchone()
        return json.loads(row[0]) if row else None


def simultaneous_first_previews(monkeypatch):
    """Hold all 8 actual read snapshots before allowing any guard write."""
    barrier = threading.Barrier(8, timeout=15)
    called = threading.local()
    real = fifo._fifo_reconciliation_preview_with_conn
    def preview(conn):
        result = real(conn)
        if not getattr(called, "first_completed", False):
            called.first_completed = True
            barrier.wait()
        return result
    monkeypatch.setattr(fifo, "_fifo_reconciliation_preview_with_conn", preview)


def eight_http_reads():
    async def run():
        transport = httpx.ASGITransport(app=api.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1", headers={"x-tenant-role": "owner", "x-tenant-account": "1"}) as client:
            return await asyncio.gather(*[client.get("/api/v1/control/data-quality") for _ in range(8)])
    return asyncio.run(run())


def test_eight_concurrent_http_reads_do_not_upgrade_stale_wal_snapshot(guard_db, monkeypatch):
    simultaneous_first_previews(monkeypatch)
    responses = eight_http_reads()
    assert [response.status_code for response in responses] == [200] * 8
    guards = [response.json()["fifo_guard"] for response in responses]
    assert {guard["guard_mode"] for guard in guards} == {"local_reconciliation"}
    assert {guard["cycles"] for guard in guards} == {1}
    assert {guard["total_units"] for guard in guards} == {8}
    assert all(not guard["manual_reconciliation_allowed"] for guard in guards)
    assert read_guard(guard_db)["cycles"] == 1
    with closing(sqlite3.connect(guard_db)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM finished_goods_cost_layers").fetchone()[0] == 0


def test_parallel_repeat_reads_count_changed_sync_once_and_preserve_first_seen(guard_db, monkeypatch):
    first = fifo.finished_goods_fifo_guard_status("2026-10-13T11:00:00")
    simultaneous_first_previews(monkeypatch)
    responses = eight_http_reads()
    assert [response.status_code for response in responses] == [200] * 8
    guards = [response.json()["fifo_guard"] for response in responses]
    assert {guard["cycles"] for guard in guards} == {2}
    assert {guard["first_seen_at"] for guard in guards} == {first["first_seen_at"]}
    assert read_guard(guard_db)["cycles"] == 2


def test_basis_change_after_read_retries_and_keeps_wb_reconciliation_blocked(guard_db, monkeypatch):
    real = fifo._fifo_reconciliation_preview_with_conn
    observed = []
    def preview(conn):
        result = real(conn)
        observed.append(len(result))
        if len(observed) == 1:
            # This actual independent writer can commit while the expensive
            # reader holds its WAL snapshot, proving no writer reservation is
            # held across valuation/Pandas work. The first basis becomes stale.
            with closing(sqlite3.connect(guard_db, timeout=0.2)) as writer:
                writer.execute("UPDATE product_pipeline SET ready_units=200 WHERE nm_id=901")
                writer.execute("INSERT INTO stocks(snapshot_at,nm_id,chrt_id,warehouse_id,warehouse_name,region_name,quantity,in_way_to_client,in_way_from_client,raw_json) VALUES('2026-10-13T12:01:00',901,0,1,'Synthetic','',30,0,0,'{}')")
                writer.commit()
        return result
    monkeypatch.setattr(fifo, "_fifo_reconciliation_preview_with_conn", preview)
    guard = fifo.finished_goods_fifo_guard_status("2026-10-13T12:00:00")
    assert len(observed) >= 2
    assert guard["total_units"] == 230
    assert guard["blocked_wb_units"] == 30
    assert guard["guard_mode"] == "wb_incident_review"
    assert guard["manual_reconciliation_allowed"] is False
    stored = read_guard(guard_db)
    assert stored["total_units"] == 230 and stored["blocked_wb_units"] == 30
    assert stored["cycles"] == 1
    with closing(sqlite3.connect(guard_db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM finished_goods_cost_layers").fetchone()[0] == 0


def test_existing_persistent_local_guard_decision_is_preserved(guard_db):
    first = fifo.finished_goods_fifo_guard_status("2026-10-13T11:00:00")
    first["first_seen_at"] = "2026-01-01T00:00:00"
    first["cycles"] = 2
    with core.connect() as conn:
        conn.execute("UPDATE app_meta SET value=? WHERE key=?", (json.dumps(first), fifo.FIFO_SYNC_GUARD_META_KEY))
    guard = fifo.finished_goods_fifo_guard_status("2026-10-13T11:00:00")
    assert guard["cycles"] == 2
    assert guard["status"] == "Критично"
    assert guard["is_persistent"] is True
    assert guard["manual_reconciliation_allowed"] is True
    assert guard["first_seen_at"] == first["first_seen_at"]


def test_four_bursts_of_eight_real_http_reads_have_no_500_or_cycle_inflation(guard_db, record_property):
    start = time.monotonic()
    for _ in range(4):
        responses = eight_http_reads()
        assert [response.status_code for response in responses] == [200] * 8
        assert {r.json()["fifo_guard"]["cycles"] for r in responses} == {1}
    record_property("parallel_requests", 32)
    record_property("elapsed_seconds", round(time.monotonic() - start, 3))
    assert read_guard(guard_db)["cycles"] == 1


def test_continuously_changing_basis_exhausts_bounded_retry_without_persisting_ok(guard_db, monkeypatch):
    real = fifo._fifo_reconciliation_preview_with_conn
    attempts = []
    def preview(conn):
        result = real(conn)
        attempts.append(1)
        with closing(sqlite3.connect(guard_db, timeout=0.2)) as writer:
            writer.execute("UPDATE product_pipeline SET ready_units=ready_units+1 WHERE nm_id=901")
            writer.commit()
        return result
    monkeypatch.setattr(fifo, "_fifo_reconciliation_preview_with_conn", preview)
    with pytest.raises(sqlite3.OperationalError, match="FIFO guard observation changed"):
        fifo.finished_goods_fifo_guard_status("2026-10-13T12:00:00")
    assert len(attempts) == 32
    assert read_guard(guard_db) is None
