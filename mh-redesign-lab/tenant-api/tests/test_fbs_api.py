import base64
import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR.parents[1] / "tenant-app"))
sys.path.insert(0, str(API_DIR))
spec = importlib.util.spec_from_file_location("fbs_test_api", API_DIR / "api_server.py")
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)
import fbs_api  # noqa: E402
import fbs_orders  # noqa: E402
from wb_api import WBAPI  # noqa: E402
from db import core  # noqa: E402
from production_permissions import ROLES, write_allowed  # noqa: E402

CALLS: list = []


def jwt(mask: int) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"s": mask}).encode()).decode().rstrip("=")
    return f"h.{body}.sig"


class FakeWB(WBAPI):  # real token_scope_info, WB calls replaced: nothing here can reach the network

    def get_fbs_orders(self, *args):
        CALLS.append("orders")
        created = (datetime.now(timezone.utc) - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"orders": [{"id": 11, "nmId": 5, "article": "A", "createdAt": created, "price": 100000, "warehouseId": 7}], "next": 0}

    def get_fbs_order_statuses(self, ids):
        CALLS.append("statuses")
        return [{"id": i, "supplierStatus": "confirm", "wbStatus": "waiting"} for i in ids]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", tmp_path / "main.sqlite")
    monkeypatch.setattr(api, "IS_DEMO", False)
    monkeypatch.setattr(fbs_api, "WBAPI", FakeWB)
    monkeypatch.setattr(fbs_api, "get_token", lambda: jwt(1 << 4))
    monkeypatch.setattr(fbs_orders.time, "sleep", lambda s: None)
    core.init_db()
    CALLS.clear()
    return monkeypatch


def client(role="owner"):
    return TestClient(api.app, base_url="http://127.0.0.1", headers={"x-tenant-role": role})


def test_reads_work_on_empty_db_without_wb(setup):
    c = client("viewer")
    assert c.get("/api/v1/fbs/orders").json()["items"] == []
    a = c.get("/api/v1/fbs/analytics?period=7d").json()
    assert a["total"] == 0 and a["buyout_pct"] is None
    st = c.get("/api/v1/fbs/state").json()
    assert st["last_run_at"] is None and st["has_marketplace_scope"] is True and not CALLS


def test_validation(setup):
    c = client()
    assert c.get("/api/v1/fbs/orders?status=bogus").status_code == 422
    assert c.get("/api/v1/fbs/analytics?period=1y").status_code == 422
    assert c.get("/api/v1/fbs/orders?days=31").status_code == 422


def test_refresh_then_read_and_too_soon(setup):
    c = client("manager")
    r = c.post("/api/v1/fbs/refresh")
    assert r.status_code == 200 and r.json()["last_error"] is None and r.json()["orders_total"] == 1
    orders = c.get("/api/v1/fbs/orders?overdue=true").json()
    item = orders["items"][0]
    assert item["order_id"] == 11 and item["price"] == 1000.0 and item["overdue"] is True and item["state"] == "processing"
    assert 29.9 <= item["age_hours"] <= 30.2 and orders["overdue_is_estimate"] is True
    again = c.post("/api/v1/fbs/refresh")
    assert again.status_code == 429 and "Retry-After" in again.headers and again.json()["state"]["retry_after_seconds"] > 0
    assert CALLS == ["orders", "statuses"]


def test_missing_marketplace_scope_is_calm(setup):
    setup.setattr(fbs_api, "get_token", lambda: jwt(1 << 2))
    c = client()
    r = c.post("/api/v1/fbs/refresh")
    assert r.status_code == 200 and "Маркетплейс" in r.json()["last_error"] and not CALLS
    assert c.get("/api/v1/fbs/state").json()["has_marketplace_scope"] is False


def test_no_token_conflict(setup):
    setup.setattr(fbs_api, "get_token", lambda: None)
    assert client().post("/api/v1/fbs/refresh").status_code == 409
    assert client().get("/api/v1/fbs/state").json()["has_marketplace_scope"] is None


def test_refresh_write_permission_over_all_roles(setup):
    allowed = {r for r in ROLES if write_allowed(r, "POST", "/api/v1/fbs/refresh")}
    assert allowed == {"owner", "manager"}
    for role in sorted(ROLES - allowed):
        assert client(role).post("/api/v1/fbs/refresh").status_code == 403
    assert not CALLS
    for role in ROLES:
        if role != "integration_reader":
            assert client(role).get("/api/v1/fbs/state").status_code == 200


def test_refresh_keeps_dashboard_caches_and_is_shop_local(setup):
    assert "/api/v1/fbs/refresh" in api.READ_ONLY_POST_PATHS
    import shop_context
    for method, path in [("GET", "/api/v1/fbs/orders"), ("GET", "/api/v1/fbs/analytics"), ("GET", "/api/v1/fbs/state"), ("POST", "/api/v1/fbs/refresh")]:
        assert shop_context.route_scope(method, path)["scope"] == "shop_local"
