import importlib.util
import sqlite3
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR.parents[1] / "tenant-app"))
sys.path.insert(0, str(API_DIR))
spec = importlib.util.spec_from_file_location("rnp_test_api", API_DIR / "api_server.py")
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)
import rnp_api  # noqa: E402
from db import core  # noqa: E402
from production_permissions import ROLES, write_allowed  # noqa: E402

TODAY = date(2026, 9, 10)
URL = "/api/v1/dashboard/rnp"
FIN = {"Артикул WB": 1, "Артикул продавца": "A-1", "Продажи, шт": 20, "Продано нетто": 20,
       "Выкупы по цене покупателя": 20000.0, "К перечислению за товар": 16000.0, "Чистая прибыль": 2000.0,
       "Себестоимость": 8000.0, "Без себестоимости, шт.": 0, "Реклама": 1000.0, "Прямые расходы WB": 2000.0}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", tmp_path / "main.sqlite")
    monkeypatch.setattr(api, "IS_DEMO", False)
    monkeypatch.setattr(rnp_api, "_today", lambda: TODAY)
    core.init_db()
    frame = pd.DataFrame([FIN])
    monkeypatch.setattr(rnp_api, "_deps", {})
    rnp_api.configure(lambda s, e: SimpleNamespace(financial_products=frame), lambda k, b: b(), lambda: api._ADVICE_COLUMNS)
    with sqlite3.connect(tmp_path / "main.sqlite") as c:
        for i, (day, canceled) in enumerate([("2026-09-08T10:00:00", 0), ("2026-09-08T11:00:00", 0), ("2026-09-08T12:00:00", 1), ("2026-09-09T10:00:00", 0)]):
            c.execute("INSERT INTO orders (id, order_date, nm_id, finished_price, price_with_disc, total_price, is_cancel, raw_json) VALUES (?,?,?,?,?,?,?,'{}')",
                      (f"o{i}", day, 1, 900, 1000, 1250, canceled))
        for i, (day, ret) in enumerate([("2026-09-08T10:00:00", 0), ("2026-09-08T11:00:00", 0), ("2026-09-09T10:00:00", 1), ("2026-09-03T10:00:00", 0)]):
            c.execute("INSERT INTO sales (id, sale_date, nm_id, total_price, price_with_disc, finished_price, is_return, raw_json) VALUES (?,?,?,?,?,?,?,'{}')",
                      (f"s{i}", day, 1, 1250, 1000, 900, ret))
        c.execute("INSERT INTO ads_daily (campaign_id, day, nm_id, spend, raw_json) VALUES (7,'2026-09-08',1,150,'{}')")
        c.execute("INSERT INTO stocks (snapshot_at, nm_id, chrt_id, quantity, raw_json) VALUES ('2026-09-08T03:00:00',1,1,50,'{}'),('2026-09-10T03:00:00',1,1,40,'{}'),('2026-09-10T03:00:00',1,2,5,'{}')")
    return TestClient(api.app, base_url="http://127.0.0.1", headers={"x-tenant-role": "owner"})


def get(client, **kw):
    r = client.get(URL, params={"period": "custom", "start": "2026-09-08", "end": "2026-09-11", **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_empty_then_pin_plan_target_and_numbers(client):
    assert get(client)["items"] == []
    assert client.put(f"{URL}/favorites", json={"nm_ids": [1]}).json() == {"nm_ids": [1]}
    assert client.put(f"{URL}/plan", json={"nm_id": 1, "plan": {"2026-09-08": 1, "2026-09-09": 2, "2026-09-11": 9}}).json()["saved"] == 3
    assert client.put(f"{URL}/target", json={"nm_id": 1, "plan_margin_pct": 12.5}).status_code == 200
    item = get(client)["items"][0]
    d = {x["day"]: x for x in item["days"]}
    # orders: 09-08 two valid (the cancelled one is excluded), each finished_price 900; 09-09 one
    assert (d["2026-09-08"]["fact_orders"], d["2026-09-08"]["fact_orders_sum"]) == (2, 1800.0)
    assert (d["2026-09-08"]["deviation_abs"], d["2026-09-08"]["deviation_pct"]) == (1, 100.0)
    assert d["2026-09-09"]["fact_orders"] == 1 and d["2026-09-09"]["deviation_abs"] == -1
    # sales: 09-08 two sales x price_with_disc 1000; 09-09 one return = -1 unit, -1000 rub
    assert (d["2026-09-08"]["fact_sales"], d["2026-09-08"]["fact_sales_sum"]) == (2, 2000.0)
    assert (d["2026-09-09"]["fact_sales"], d["2026-09-09"]["fact_sales_sum"]) == (-1, -1000.0)
    assert (d["2026-09-08"]["price_before"], d["2026-09-08"]["price_after"]) == (1250.0, 1000.0)
    # forecast 09-08: first sale 09-03 -> days 03..07 (5 days) with one sale = 0.2
    assert d["2026-09-08"]["forecast_sales"] == 0.2
    assert d["2026-09-11"]["is_future"] and d["2026-09-11"]["fact_orders"] is None and d["2026-09-11"]["plan_orders"] == 9
    assert (d["2026-09-08"]["ad_spend"], d["2026-09-08"]["drr_pct"]) == (150.0, 7.5)   # 150 / 2000
    assert d["2026-09-08"]["stock"] == 50 and d["2026-09-09"]["stock"] is None and d["2026-09-10"]["stock"] == 45
    assert item["stock_now"] == 45 and item["current_price"] == 1250 and item["current_discount"] == 20 and item["current_final"] == 1000
    assert item["forecast_margin_pct"] == 10.0 and item["plan_margin_pct"] == 12.5 and item["margin_gap_pp"] == -2.5
    assert item["vendor_code"] == "A-1" and "7 дней" in get(client)["forecast_note"]


def test_no_ad_breakdown_and_no_economics(client):
    with sqlite3.connect(core.DB_PATH) as c:
        c.execute("DELETE FROM ads_daily")
    client.put(f"{URL}/favorites", json={"nm_ids": [1]})
    body = get(client)
    assert body["ads_note"] and body["items"][0]["totals"]["ad_spend"] is None and body["items"][0]["days"][0]["drr_pct"] is None
    rnp_api._deps["build"] = lambda s, e: SimpleNamespace(financial_products=pd.DataFrame())
    item = get(client)["items"][0]
    assert item["forecast_margin_pct"] is None and item["margin_status"] == "insufficient_data" and item["margin_note"]


def test_window_default_and_cap(client):
    r = client.get(URL).json()
    assert (r["start"], r["end"]) == ("2026-08-28", "2026-09-10")   # 14 days ending today
    r = client.get(URL, params={"period": "90d"}).json()
    assert r["capped"] and r["start"] == "2026-08-11" and r["end"] == "2026-09-10"
    assert client.get(URL, params={"period": "custom"}).status_code == 422
    assert client.get(URL, params={"period": "yearly"}).status_code == 422


def test_validation(client):
    def put(path, body):
        return client.put(f"{URL}/{path}", json=body)
    assert put("favorites", {"nm_ids": [1, 1]}).status_code == 422
    assert put("favorites", {"nm_ids": [999]}).status_code == 422                  # unknown article
    assert put("favorites", {"nm_ids": [0]}).status_code == 422
    assert put("favorites", {"nm_ids": list(range(1, 12))}).status_code == 422      # max 10
    assert put("plan", {"nm_id": 1, "plan": {"2026-09-08": 1}}).status_code == 422  # not pinned
    put("favorites", {"nm_ids": [1]})
    for bad in ({"2026-09-08": -1}, {"2026-09-08": 100001}, {"2026-09-08": 1.5}, {"2020-01-01": 1}, {"2030-01-01": 1}):
        assert put("plan", {"nm_id": 1, "plan": bad}).status_code == 422, bad
    assert put("plan", {"nm_id": 1, "plan": {"2026-09-08": 0}}).status_code == 200  # 0 is a valid plan
    assert put("plan", {"nm_id": 1, "plan": {"2026-09-08": 100000}}).status_code == 200
    assert put("plan", {"nm_id": 1, "plan": {"2026-09-08": None}}).status_code == 200  # clears
    assert get(client)["items"][0]["days"][0]["plan_orders"] is None
    assert put("target", {"nm_id": 1, "plan_margin_pct": 81}).status_code == 422
    assert put("target", {"nm_id": 1, "plan_margin_pct": -1}).status_code == 422
    assert put("target", {"nm_id": 1, "plan_margin_pct": 80}).status_code == 200
    assert put("target", {"nm_id": 1, "plan_margin_pct": None}).status_code == 200
    assert get(client)["items"][0]["plan_margin_pct"] is None
    assert put("favorites", {"nm_ids": []}).json() == {"nm_ids": []}               # unpin all


def test_favorites_keep_order_and_replace(client):
    with sqlite3.connect(core.DB_PATH) as c:
        c.execute("INSERT INTO products_catalog (nm_id, supplier_article, raw_json) VALUES (2,'B','{}')")
    assert client.put(f"{URL}/favorites", json={"nm_ids": [2, 1]}).json() == {"nm_ids": [2, 1]}
    assert [i["nm_id"] for i in get(client)["items"]] == [2, 1]
    assert client.put(f"{URL}/favorites", json={"nm_ids": [1]}).json() == {"nm_ids": [1]}


def test_write_roles_over_all_roles(client):
    client.put(f"{URL}/favorites", json={"nm_ids": [1]})
    bodies = {"favorites": {"nm_ids": [1]}, "plan": {"nm_id": 1, "plan": {"2026-09-08": 3}}, "target": {"nm_id": 1, "plan_margin_pct": 5}}
    for role in sorted(ROLES):
        allowed = role in {"owner", "manager"}
        for path, body in bodies.items():
            assert write_allowed(role, "PUT", f"{URL}/{path}") is allowed, (role, path)
            r = client.put(f"{URL}/{path}", json=body, headers={"x-tenant-role": role})
            assert (r.status_code == 200) if allowed else (r.status_code == 403), (role, path, r.status_code)
        if role != "integration_reader":
            assert client.get(URL, headers={"x-tenant-role": role}).status_code == 200, role
    assert client.get(URL, headers={"x-tenant-role": "integration_reader"}).status_code == 403


def test_routes_are_shop_local_and_registered():
    import shop_context
    for method, path in [("GET", URL), ("PUT", f"{URL}/favorites"), ("PUT", f"{URL}/plan"), ("PUT", f"{URL}/target")]:
        assert shop_context.route_scope(method, path)["scope"] == "shop_local"
    assert all(path in api.app.openapi()["paths"] for path in (URL, f"{URL}/favorites", f"{URL}/plan", f"{URL}/target"))


def test_plan_edits_keep_shared_caches_and_the_cached_get_is_not_stale(client, monkeypatch):
    """Every plan save used to wipe all dashboard caches (a cold recompute per edit); now only RNP's own cache version moves."""
    rnp_api.configure(lambda s, e: SimpleNamespace(financial_products=pd.DataFrame([FIN])), api._dashboard_response, lambda: api._ADVICE_COLUMNS)
    api.dashboard_response_cache.clear()
    wipes = []
    monkeypatch.setattr(api, "_clear_dashboard_caches", lambda: wipes.append(1))
    assert client.put(f"{URL}/favorites", json={"nm_ids": [1]}).status_code == 200
    before = get(client)["items"][0]["days"][0]
    assert before["plan_orders"] is None
    assert client.put(f"{URL}/plan", json={"nm_id": 1, "plan": {"2026-09-08": 7}}).status_code == 200
    after = get(client)["items"][0]["days"][0]
    assert after["plan_orders"] == 7                      # the cached GET was rebuilt after the write
    assert client.put(f"{URL}/target", json={"nm_id": 1, "plan_margin_pct": 12.5}).status_code == 200
    assert get(client)["items"][0]["plan_margin_pct"] == 12.5
    assert wipes == []                                    # the shared dashboard caches were never wiped
