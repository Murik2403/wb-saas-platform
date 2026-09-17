from unittest.mock import Mock

from wb_api import WBAPI
import pandas as pd
from test_display_contract import api


def test_sales_funnel_products_uses_v3_period_and_paginates(monkeypatch):
    client = WBAPI("fixture")
    request = Mock(side_effect=[
        {"data": {"products": [{"product": {"nmId": 1}}]}},
        {"data": {"products": []}},
    ])
    monkeypatch.setattr(client, "_request", request)

    rows = client.get_sales_funnel_products("2026-09-01", "2026-09-07", limit=1)

    assert rows == [{"product": {"nmId": 1}}]
    assert request.call_args_list[0].args == ("POST", f"{client.ANALYTICS_URL}/api/analytics/v3/sales-funnel/products")
    body = request.call_args_list[0].kwargs["json"]
    assert body["selectedPeriod"] == {"start": "2026-09-01", "end": "2026-09-07"}
    assert body["pastPeriod"] == {"start": "2026-08-25", "end": "2026-08-31"}
    assert request.call_args_list[1].kwargs["json"]["offset"] == 1


def test_seller_warehouse_inventory_uses_marketplace_read_contract(monkeypatch):
    client = WBAPI("fixture")
    request = Mock(side_effect=[
        [{"id": 9, "name": "Склад FBS"}],
        {"stocks": [{"chrtId": 77, "amount": 12}]},
    ])
    monkeypatch.setattr(client, "_request", request)
    assert client.get_seller_warehouses() == [{"id": 9, "name": "Склад FBS"}]
    assert client.get_seller_warehouse_inventory(9, [77]) == [{"chrtId": 77, "amount": 12}]
    assert request.call_args_list[0].args == ("GET", f"{client.MARKETPLACE_URL}/api/v3/warehouses")
    assert request.call_args_list[1].kwargs["json"] == {"chrtIds": [77]}


def test_transit_tariffs_uses_supplies_read_contract(monkeypatch):
    client = WBAPI("fixture")
    request = Mock(return_value={"data": [{"transitWarehouseName": "Москва", "destinationWarehouseName": "Казань", "palletTariff": 7500}]})
    monkeypatch.setattr(client, "_request", request)
    assert client.get_transit_tariffs()[0]["palletTariff"] == 7500
    assert request.call_args.args == ("GET", f"{client.SUPPLIES_URL}/api/v1/transit-tariffs")


def test_analytics_dashboard_normalizes_funnel_and_keeps_stock_history(monkeypatch):
    api.dashboard_response_cache.clear()
    monkeypatch.setattr(api, "get_token", lambda: "token")
    monkeypatch.setattr(api, "IS_DEMO", False)
    monkeypatch.setattr(api.WBAPI, "get_sales_funnel_products", lambda self, start, end: [{
        "product": {"nmId": 7, "title": "Товар"},
        "statistic": {"selected": {"period": {"openCard": 100, "cartCount": 20, "orderCount": 5, "buyoutCount": 3}}},
    }])
    monkeypatch.setattr(api, "read_table", lambda name: pd.DataFrame([{"snapshot_at": "2026-09-10T10:00:00", "quantity": 12}, {"snapshot_at": "2026-09-11T10:00:00", "quantity": 9}]) if name == "stocks" else pd.DataFrame())

    result = api.get_dashboard_analytics(period="7d")

    assert [stage["value"] for stage in result["sales_funnel"]["stages"]] == [100, 20, 5, 3]
    assert result["stock_history"][-1] == {"date": "2026-09-11", "quantity": 9, "rows": 1}
    assert result["errors"] == {}
