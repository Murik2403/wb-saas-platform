import importlib.util
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR.parents[1] / "tenant-app"))
sys.path.insert(0, str(API_DIR))
spec = importlib.util.spec_from_file_location("self_buyouts_test_api", API_DIR / "api_server.py")
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)
import self_buyouts_api  # noqa: E402
from db import core  # noqa: E402
from production_permissions import ROLES, write_allowed  # noqa: E402

TODAY = date(2026, 9, 10)  # Thursday
URL = "/api/v1/self-buyouts"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", tmp_path / "main.sqlite")
    monkeypatch.setattr(api, "IS_DEMO", False)
    monkeypatch.setattr(self_buyouts_api, "_today", lambda: TODAY)
    core.init_db()
    with sqlite3.connect(tmp_path / "main.sqlite") as c:
        c.execute("INSERT INTO products_catalog (nm_id, supplier_article, raw_json) VALUES (1, 'ART-1', '{}')")
        c.execute("INSERT INTO sales (id, sale_date, nm_id, raw_json) VALUES ('s1', '2026-09-01T10:00:00', 2, '{}')")
    return TestClient(api.app, base_url="http://127.0.0.1", headers={"x-tenant-role": "owner"})


def rec(day, nm=1, units=1, paid=100, cb=0, **extra):
    return {"day": day, "nm_id": nm, "units": units, "buyout_amount_kop": paid, "cashback_kop": cb, **extra}


def seed(client):
    for body in (rec("2026-09-07", units=2, paid=150000, cb=30000), rec("2026-09-06", units=1, paid=50000),
                 rec("2026-08-31", units=3, paid=90000, cb=9000), rec("2026-08-01", nm=2, units=5, paid=100000)):
        assert client.post(URL, json=body).status_code == 200


def test_totals_groups_and_deltas_hand_checked(client):
    seed(client)
    r = client.get(URL, params={"period": "30d", "group": "week"})
    assert r.status_code == 200
    body = r.json()
    assert (body["start"], body["end"]) == ("2026-08-12", "2026-09-10")
    assert body["previous_window"] == {"start": "2026-07-13", "end": "2026-08-11"}
    assert body["totals"] == {"records": 3, "units": 6, "paid_kop": 290000, "cashback_kop": 39000, "net_cost_kop": 251000}
    assert body["previous_totals"]["net_cost_kop"] == 100000 and body["previous_totals"]["units"] == 5
    d = body["deltas"]
    assert d["units"] == {"value": 6, "previous": 5, "delta": 1, "delta_pct": 20.0}
    assert d["paid_kop"]["delta"] == 190000 and d["paid_kop"]["delta_pct"] == 190.0
    assert d["cashback_kop"] == {"value": 39000, "previous": 0, "delta": 39000, "delta_pct": None}  # zero previous -> null pct
    assert d["net_cost_kop"]["delta"] == 151000
    # ISO weeks: Sun 09-06 belongs to the week of Mon 08-31, with 08-31 itself
    assert body["groups"] == [
        {"start": "2026-09-07", "records": 1, "units": 2, "paid_kop": 150000, "cashback_kop": 30000, "net_cost_kop": 120000},
        {"start": "2026-08-31", "records": 2, "units": 4, "paid_kop": 140000, "cashback_kop": 9000, "net_cost_kop": 131000}]
    months = client.get(URL, params={"group": "month"}).json()["groups"]
    assert [(m["start"], m["units"], m["net_cost_kop"]) for m in months] == [("2026-09-01", 3, 170000), ("2026-08-01", 3, 81000)]
    days = client.get(URL, params={"group": "day"}).json()["groups"]
    assert [x["start"] for x in days] == ["2026-09-07", "2026-09-06", "2026-08-31"]


def test_records_newest_first_with_vendor_and_net(client):
    seed(client)
    recs = client.get(URL).json()["records"]
    assert [r["day"] for r in recs] == ["2026-09-07", "2026-09-06", "2026-08-31"]
    assert recs[0]["vendor_code"] == "ART-1" and recs[0]["net_cost_kop"] == 120000 and recs[0]["actor"] == "local-owner"
    assert client.get(URL, params={"nm_id": 2, "period": "90d"}).json()["totals"]["units"] == 5  # nm_id filter, 90d covers 08-01


def test_empty_and_custom_period_errors(client):
    body = client.get(URL).json()
    assert body["records"] == [] and body["totals"]["net_cost_kop"] == 0 and body["deltas"]["units"]["delta_pct"] is None
    assert client.get(URL, params={"period": "custom"}).status_code == 422
    assert client.get(URL, params={"period": "custom", "start": "2025-01-01", "end": "2026-09-10"}).status_code == 422
    ok = client.get(URL, params={"period": "custom", "start": "2026-09-10", "end": "2026-09-01"})  # swapped dates are fixed
    assert ok.status_code == 200 and ok.json()["start"] == "2026-09-01"
    assert client.get(URL, params={"group": "year"}).status_code == 422


def test_validation_field_errors(client):
    def errors(body):
        r = client.post(URL, json=body)
        assert r.status_code == 422
        return r.json()["detail"]["errors"]
    assert "day" in errors(rec("2025-08-01"))  # 405 days back
    assert "day" in errors(rec("2026-09-12"))  # +2 days
    assert "day" in errors(rec("not-a-date"))
    assert client.post(URL, json=rec("2026-09-11")).status_code == 200  # tomorrow is allowed
    assert client.post(URL, json=rec("2025-08-06")).status_code == 200  # exactly 400 days back
    assert "units" in errors(rec("2026-09-01", units=0)) and "units" in errors(rec("2026-09-01", units=10001))
    assert "buyout_amount_kop" in errors(rec("2026-09-01", paid=-1))
    assert "buyout_amount_kop" in errors(rec("2026-09-01", paid=100_000_001))
    assert "cashback_kop" in errors(rec("2026-09-01", cb=-5))
    assert "nm_id" in errors(rec("2026-09-01", nm=999))
    assert "format" in errors(rec("2026-09-01", format="x" * 41)) and "note" in errors(rec("2026-09-01", note="x" * 201))
    assert set(errors(rec("2026-09-01", units=0, paid=-1))) == {"units", "buyout_amount_kop"}
    assert client.post(URL, json={**rec("2026-09-01"), "units": 1.5}).status_code == 422  # floats rejected
    assert client.post(URL, json={**rec("2026-09-01"), "buyout_amount_kop": "100"}).status_code == 422
    assert client.post(URL, json={**rec("2026-09-01"), "extra": 1}).status_code == 422
    ok = client.post(URL, json=rec("2026-09-01", nm=2, units=10000, paid=100_000_000, cb=0, format=" Свой ", note="n"))
    assert ok.status_code == 200 and ok.json()["format"] == "Свой" and ok.json()["vendor_code"] == ""


def test_update_and_delete_by_body_id(client):
    created = client.post(URL, json=rec("2026-09-01", units=2, paid=1000, cb=100)).json()
    upd = client.post(f"{URL}/update", json={**rec("2026-09-02", units=3, paid=2000, cb=0, note="x"), "id": created["id"]})
    assert upd.status_code == 200 and upd.json()["units"] == 3 and upd.json()["day"] == "2026-09-02" and upd.json()["net_cost_kop"] == 2000
    assert client.post(f"{URL}/update", json={**rec("2026-09-02"), "id": 999}).status_code == 404
    assert client.post(f"{URL}/update", json={**rec("2026-09-02", units=0), "id": created["id"]}).status_code == 422
    assert client.get(URL).json()["records"][0]["units"] == 3  # failed update changed nothing
    assert client.post(f"{URL}/delete", json={"id": 999}).status_code == 404
    assert client.post(f"{URL}/delete", json={"id": created["id"]}).json() == {"deleted": created["id"]}
    assert client.get(URL).json()["records"] == []
    assert client.post(f"{URL}/delete", json={"id": "1"}).status_code == 422


def test_roles_write_only_owner_manager(client):
    for role in sorted(ROLES):
        expected = role in {"owner", "manager"}
        for path in (URL, f"{URL}/update", f"{URL}/delete"):
            assert write_allowed(role, "POST", path) is expected, (role, path)
        r = client.post(URL, json=rec("2026-09-01"), headers={"x-tenant-role": role})
        assert (r.status_code == 200) is expected, (role, r.status_code)
        if role != "integration_reader":
            assert client.get(URL, headers={"x-tenant-role": role}).status_code == 200
    assert client.get(URL, headers={"x-tenant-role": "integration_reader"}).status_code == 403


def test_writes_keep_dashboard_caches_and_routes_are_shop_local(client, monkeypatch):
    import shop_context
    cleared = []
    monkeypatch.setattr(api, "_clear_dashboard_caches", lambda: cleared.append(1))
    created = client.post(URL, json=rec("2026-09-01")).json()
    client.post(f"{URL}/update", json={**rec("2026-09-02"), "id": created["id"]})
    client.post(f"{URL}/delete", json={"id": created["id"]})
    assert cleared == []
    for method, path in (("GET", URL), ("POST", URL), ("POST", f"{URL}/update"), ("POST", f"{URL}/delete")):
        assert shop_context.route_scope(method, path)["scope"] == "shop_local"
    fixture = API_DIR.parent / "src" / "test-fixtures" / "shop-route-scopes-stage4.json"
    assert json.loads(fixture.read_text(encoding="utf-8")) == shop_context.ROUTES


def test_ledger_does_not_touch_financial_tables(client, tmp_path):
    seed(client)
    with sqlite3.connect(tmp_path / "main.sqlite") as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "self_buyout" in tables
        assert c.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 1  # only the fixture sale
        assert c.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
