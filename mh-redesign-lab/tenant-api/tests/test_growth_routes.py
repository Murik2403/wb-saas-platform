import importlib.util
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from test_growth_activation import growth_db

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR.parents[1] / "tenant-app"))
sys.path.insert(0, str(API_DIR))
spec = importlib.util.spec_from_file_location("redesign_growth_test_api", API_DIR / "api_server.py")
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)


@pytest.fixture
def client(monkeypatch, growth_db):
    monkeypatch.setattr(api, "DB_PATH", growth_db)
    monkeypatch.setattr(api, "IS_DEMO", False)
    monkeypatch.setattr(api, "get_token", lambda: "test-only")
    monkeypatch.setattr(api, "deliver_pending_growth_events", lambda *_: {"disabled": True})
    data = SimpleNamespace(
        products=pd.DataFrame([{"Артикул WB": 123, "Товар": "Тестовый товар", "Выручка": 2400, "Продажи": 2, "Себестоимость ед.": 420}]),
        financial_products=pd.DataFrame([{"Артикул WB": 123, "Чистая прибыль": 1100, "Чистая маржинальность, %": 45.8}]),
    )
    monkeypatch.setattr(api, "build_dashboard", lambda *_: data)
    return TestClient(api.app, base_url="http://localhost")


AUTH = {"x-tenant-account": "1", "x-tenant-role": "owner"}


def test_report_get_is_read_only_and_view_ack_is_durable_and_idempotent(client, growth_db):
    response = client.get("/api/v1/growth/first-report?nm_id=123")
    assert response.status_code == 200
    report = response.json()
    assert report["margin"] == 1100
    assert report["unit_cost"] == 420
    assert "Налоги" in report["limitation"]
    assert not client.get("/api/v1/growth/activation?nm_id=123").json()["first_value_verified"]
    ack = {"nm_id": 123, "report_revision": report["report_revision"]}
    first = client.post("/api/v1/growth/report-viewed", json=ack, headers=AUTH)
    assert first.status_code == 200
    assert first.json()["first_value_verified"] is True
    assert client.post("/api/v1/growth/report-viewed", json=ack, headers=AUTH).json() == first.json()
    with sqlite3.connect(growth_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM growth_event_outbox").fetchone()[0] == 2


@pytest.mark.parametrize("change", ["error", "partial", "running", "zero_cost", "no_rows", "demo"])
def test_server_rechecks_evidence_before_recording(client, monkeypatch, growth_db, change):
    report = client.get("/api/v1/growth/first-report?nm_id=123").json()
    with sqlite3.connect(growth_db) as conn:
        if change in {"error", "partial", "running"}:
            conn.execute("UPDATE sync_log SET status=?", (change,))
        elif change == "zero_cost":
            conn.execute("UPDATE costs SET cost_per_wb_unit=0")
        elif change == "no_rows":
            conn.execute("DELETE FROM financial_report")
    if change == "demo":
        monkeypatch.setattr(api, "IS_DEMO", True)
    response = client.post("/api/v1/growth/report-viewed", json={"nm_id": 123, "report_revision": report["report_revision"]}, headers=AUTH)
    assert response.status_code in {403, 409}
    with sqlite3.connect(growth_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM growth_first_value").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM growth_event_outbox").fetchone()[0] == 0


def test_ack_requires_authenticated_identity_and_matching_report(client, growth_db):
    ack = {"nm_id": 123, "report_revision": "a" * 64}
    assert client.post("/api/v1/growth/report-viewed", json=ack).status_code == 403
    assert client.post("/api/v1/growth/report-viewed", json=ack, headers=AUTH).status_code == 409
    assert client.post("/api/v1/growth/report-viewed", json=ack, headers={**AUTH, "x-tenant-role": "viewer"}).status_code == 403
    with sqlite3.connect(growth_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM growth_first_value").fetchone()[0] == 0


def test_incomplete_computed_economics_never_becomes_first_value(client, monkeypatch):
    monkeypatch.setattr(api, "build_dashboard", lambda *_: SimpleNamespace(products=pd.DataFrame([{"Артикул WB": 123, "Выручка": 2400, "Продажи": 2, "Себестоимость ед.": 420}]), financial_products=pd.DataFrame()))
    assert client.get("/api/v1/growth/first-report?nm_id=123").status_code == 409
