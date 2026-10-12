"""The full middleware bounds overview work while FIFO permissions stay fresh."""
import asyncio
from contextlib import closing
import sqlite3
import threading
import time

import httpx
import pandas as pd
import pytest

from test_cashflow_api import api
from db import core, wb_fifo_reconciliation as fifo

PATH = "/api/v1/control/data-quality"


@pytest.fixture
def quality_db(tmp_path, monkeypatch):
    path = tmp_path / "quality.sqlite3"
    monkeypatch.setattr(core, "DB_PATH", path)
    monkeypatch.setattr(api, "DB_PATH", path)
    monkeypatch.setattr(api, "IS_DEMO", False)
    monkeypatch.setattr(api, "TENANT_SLUG", "")
    core.init_db()
    with core.connect() as conn:
        conn.execute("INSERT INTO product_pipeline(nm_id,local_known,ready_units,inbound_known,inbound_units) VALUES(901,1,8,0,0)")
    context = {"settings": {}, "token": False, "sync": {"finished_at": "2026-10-13T12:00:00"}, "backup": None}
    monkeypatch.setattr(api, "load_settings", lambda: context["settings"])
    monkeypatch.setattr(api, "get_token", lambda: "synthetic-not-a-provider-key" if context["token"] else None)
    monkeypatch.setattr(api, "last_sync", lambda: context["sync"])
    monkeypatch.setattr(api, "latest_backup", lambda: context["backup"])
    counts = {"overview": 0, "guard": 0, "active": 0, "max_active": 0}
    lock = threading.Lock()
    def build(*args):
        with lock:
            counts["overview"] += 1
            generation = counts["overview"]
        return pd.DataFrame(), {"generation": generation}, pd.DataFrame(), pd.DataFrame()
    real_guard = fifo.finished_goods_fifo_guard_status
    def guard(*args, **kwargs):
        with lock:
            counts["guard"] += 1
            counts["active"] += 1
            counts["max_active"] = max(counts["max_active"], counts["active"])
        try:
            # Hold the heap lease briefly to make parallel entry observable;
            # SQLite calculation is real, including persistent observation.
            time.sleep(0.015)
            return real_guard(*args, **kwargs)
        finally:
            with lock:
                counts["active"] -= 1
    monkeypatch.setattr(api, "build_data_quality_overview", build)
    monkeypatch.setattr(api, "finished_goods_fifo_guard_status", guard)
    api._clear_dashboard_caches()
    return path, context, counts


def request(method="GET", path=PATH, *, body=None, count=1, role="owner"):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app, raise_app_exceptions=False),
                                     base_url="http://127.0.0.1", headers={"x-tenant-role": role, "x-tenant-account": "1"}) as client:
            return await asyncio.gather(*[client.request(method, path, json=body) for _ in range(count)])
    return asyncio.run(run())


def test_eight_full_requests_coalesce_overview_but_calculate_eight_fresh_guards(quality_db):
    _, _, counts = quality_db
    responses = request(count=8)
    assert [r.status_code for r in responses] == [200] * 8
    assert {r.json()["summary"]["generation"] for r in responses} == {1}
    assert counts["overview"] == 1 and counts["guard"] == 8
    assert counts["max_active"] == 1
    assert {r.json()["fifo_guard"]["cycles"] for r in responses} == {1}


def test_cached_overview_never_caches_reconciliation_permission(quality_db):
    path, _, counts = quality_db
    first = request()[0]
    assert first.status_code == 200
    assert first.json()["fifo_guard"]["manual_reconciliation_allowed"] is False
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("UPDATE product_pipeline SET ready_units=200 WHERE nm_id=901")
        conn.commit()
    large = request()[0]
    assert large.status_code == 200
    assert large.json()["fifo_guard"]["manual_reconciliation_allowed"] is True
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO stocks(snapshot_at,nm_id,chrt_id,warehouse_id,warehouse_name,region_name,quantity,in_way_to_client,in_way_from_client,raw_json) VALUES('2026-10-13T12:01:00',901,0,1,'Synthetic','',30,0,0,'{}')")
        conn.commit()
    blocked = request()[0]
    assert blocked.status_code == 200
    assert blocked.json()["fifo_guard"]["blocked_wb_units"] == 30
    assert blocked.json()["fifo_guard"]["manual_reconciliation_allowed"] is False
    assert counts["overview"] == 1 and counts["guard"] == 3


@pytest.mark.parametrize("changed", ["settings", "sync", "backup", "token"])
def test_background_context_changes_select_new_overview(quality_db, changed):
    _, context, counts = quality_db
    assert request()[0].status_code == 200
    context[changed] = {"settings": {"ui_mode": "simple"}, "sync": {"finished_at": "2026-10-13T12:05:00"},
                        "backup": {"modified_at": "2026-10-13T12:04:00"}, "token": True}[changed]
    second = request()[0]
    assert second.status_code == 200
    assert second.json()["summary"]["generation"] == 2
    assert counts["overview"] == 2 and counts["guard"] == 2


def test_successful_actual_cost_write_invalidates_overview(quality_db):
    _, _, counts = quality_db
    assert request()[0].status_code == 200
    changed = request("PATCH", "/api/v1/settings/product-costs", body={"items": [{"nm_id": 901, "cost_per_wb_unit": 25}]})[0]
    assert changed.status_code == 200
    assert request()[0].json()["summary"]["generation"] == 2
    assert counts["overview"] == 2


def test_denied_cost_write_does_not_clear_valid_overview(quality_db):
    _, _, counts = quality_db
    assert request()[0].status_code == 200
    rejected = request("PATCH", "/api/v1/settings/product-costs", body={"items": [{"nm_id": 901, "cost_per_wb_unit": 25}]}, role="viewer")[0]
    assert rejected.status_code == 403
    assert request()[0].json()["summary"]["generation"] == 1
    assert counts["overview"] == 1
