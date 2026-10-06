"""The public purchase payload must retain its price in the real SQLite store."""
from contextlib import closing
import sqlite3

import pytest
from fastapi.testclient import TestClient

from test_display_contract import api
import db.core as core


@pytest.mark.parametrize(
    "price_fields,currency,rate,expected_supplier,expected_rate,expected_rub",
    [
        ({"supplier_unit_price": 125.5}, "RUB", 1, 125.5, 1, 125.5),
        ({"supplier_unit_price": 12.5}, "CNY", 13, 12.5, 13, 162.5),
        # The older API field is already a RUB cost, not a foreign-currency price.
        ({"unit_price": 126}, "CNY", 13, 126, 1, 126),
    ],
)
def test_create_purchase_retains_price_and_currency_conversion(
    monkeypatch, tmp_path, price_fields, currency, rate,
    expected_supplier, expected_rate, expected_rub,
):
    database = tmp_path / "procurement.sqlite3"
    monkeypatch.setattr(core, "DB_PATH", database)
    monkeypatch.setattr(api, "DB_PATH", database)
    monkeypatch.setattr(api, "IS_DEMO", False)
    core.init_db()

    # Use actual routing, validation, business rules and persistence. No lifespan
    # means no background jobs, WB connection or production settings are loaded.
    with closing(TestClient(api.app, base_url="http://127.0.0.1",headers={"x-tenant-role":"owner","x-tenant-account":"1"})) as client:
        response = client.post("/api/v1/operations/procurement", json={
            "supplier_name": "Synthetic supplier",
            "procurement_type": "Товар",
            "status": "Запланировано",
            "order_date": "2026-09-19",
            "currency": currency,
            "exchange_rate": rate,
            "items": [{
                "supplier_article": "QA-PRICE-ONLY",
                "product_name": "Synthetic product",
                "quantity": 3,
                **price_fields,
            }],
        })
        assert response.status_code == 200, response.text
        order_id = response.json()["id"]
        listing = client.get("/api/v1/operations/procurement")
        assert listing.status_code == 200, listing.text

    # A new connection verifies committed values, beyond the creation response.
    with closing(sqlite3.connect(database)) as connection:
        item = connection.execute(
            "SELECT supplier_unit_price,exchange_rate,unit_price "
            "FROM procurement_items WHERE order_id=?", (order_id,),
        ).fetchone()
        assert item == pytest.approx((expected_supplier, expected_rate, expected_rub))

    order = next(row for row in listing.json()["orders"] if row["id"] == order_id)
    assert order["currency"] == currency
    assert order["total_amount"] == pytest.approx(3 * expected_rub)
    assert order["outstanding_amount"] == pytest.approx(3 * expected_rub)
