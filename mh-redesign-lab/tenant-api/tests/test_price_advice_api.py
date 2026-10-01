import sqlite3
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import HTTPException
from test_display_contract import api

FIN = {
    "Артикул WB": 1, "Артикул продавца": "A-1", "Продажи, шт": 20, "Продано нетто": 20,
    "Выкупы по цене покупателя": 20000.0, "К перечислению за товар": 16000.0, "Чистая прибыль": 2000.0,
    "Себестоимость": 8000.0, "Без себестоимости, шт.": 0, "Реклама": 1000.0, "Прямые расходы WB": 2000.0,
}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "p.db")
    api.dashboard_response_cache.clear()
    frame = pd.DataFrame([FIN, {**FIN, "Артикул WB": 2, "Артикул продавца": "A-2"}])
    monkeypatch.setattr(api, "_build_dashboard_cached", lambda s, e: SimpleNamespace(financial_products=frame))
    with sqlite3.connect(tmp_path / "p.db") as conn:
        conn.execute("CREATE TABLE sales (nm_id INTEGER, sale_date TEXT, total_price REAL, price_with_disc REAL, is_return INTEGER)")
        conn.execute("INSERT INTO sales VALUES (1, '2026-09-20', 1250, 1000, 0)")
        conn.execute("INSERT INTO sales VALUES (2, '2026-09-20', 1000, 1000, 0)")
    return tmp_path


def call(**kw):
    return api.get_products_price_advice(period="custom", start=date(2026, 9, 1), end=date(2026, 9, 30), target_margin=kw.pop("target_margin", 20))


def test_get_uses_local_prices_and_reports_source(env):
    r = call()
    assert r["price_source"] == "local_sales" and "спрос" in r["disclaimer"]
    a, b = r["items"]
    assert (a["nm_id"], a["current_price"], a["current_discount"], a["current_final"]) == (1, 1250, 20, 1000)
    assert b["current_price"] == 1000 and b["current_discount"] == 0
    assert a["status"] == "recommend" and a["recommended_price"] == 1437  # capped +15%: floor(1437.5)


def test_post_uses_given_prices_and_upload_in_progress(env):
    now = "2099-01-01T00:00:00"  # updated_at in the future is "fresh"
    api._init_redesign_schema()
    with sqlite3.connect(env / "p.db") as conn:
        conn.execute("INSERT INTO redesign_price_uploads(wb_upload_id, goods_count, status, created_at, updated_at) VALUES (5,1,'В обработке',?,?)", (now, now))
        conn.execute("INSERT INTO price_upload_item(upload_id, nm_id) VALUES (5, 2)")
    body = api.PriceAdviceInput(prices=[{"nm_id": 1, "price": 1000, "discount": 0}, {"nm_id": 2, "price": 1000, "discount": 0}])
    r = api.post_products_price_advice(body, period="custom", start=date(2026, 9, 1), end=date(2026, 9, 30), target_margin=20)
    assert r["price_source"] == "request"
    assert [i["status"] for i in r["items"]] == ["recommend", "upload_in_progress"]
    assert r["items"][0]["recommended_price"] == 1150


def test_validation_and_routes(env):
    with pytest.raises(Exception):
        api.PriceAdviceInput(prices=[{"nm_id": 1, "price": 1, "discount": 100}])
    paths = api.app.openapi()["paths"]["/api/v1/dashboard/products/price-advice"]
    assert set(paths) == {"get", "post"}
    params = {p["name"]: p for p in paths["get"]["parameters"]}
    assert params["target_margin"]["schema"]["minimum"] == 0 and params["target_margin"]["schema"]["maximum"] == 80
    with pytest.raises(HTTPException) as exc:
        api.get_products_price_advice(period="custom", start=None, end=None, target_margin=20)
    assert exc.value.status_code == 422


def test_post_advice_does_not_wipe_dashboard_caches_but_real_writes_still_do(env, monkeypatch):
    """The advice POST only reads: if it cleared the caches every click would force a full recompute of every page."""
    from contextlib import closing
    from fastapi.testclient import TestClient

    wipes = []
    monkeypatch.setattr(api, "IS_DEMO", False)
    monkeypatch.setattr(api, "_clear_dashboard_caches", lambda: wipes.append(1))
    url = "/api/v1/dashboard/products/price-advice?period=custom&start=2026-09-01&end=2026-09-30&target_margin=20"
    with closing(TestClient(api.app, base_url="http://127.0.0.1")) as client:
        r = client.post(url, json={"prices": [{"nm_id": 1, "price": 1000, "discount": 0}]}, headers={"x-tenant-role": "owner"})
        assert r.status_code == 200, r.text
        assert wipes == []                                    # read-only POST: caches stay warm
        # a path outside the allow-list still wipes after a successful write
        monkeypatch.setattr(api, "READ_ONLY_POST_PATHS", set())
        r = client.post(url, json={"prices": [{"nm_id": 1, "price": 1000, "discount": 0}]}, headers={"x-tenant-role": "owner"})
        assert r.status_code == 200 and wipes == [1]
