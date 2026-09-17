from datetime import date
from unittest.mock import Mock
import sqlite3
import time

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from test_display_contract import api
from wb_api import WBAPI, WBAPIError
import requests


def test_plan_result_and_timestamps_survive_reload(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "plan.db")
    item = api.create_action_plan_item(api.ActionPlanCreate(title="Проверить остатки", sku="test"))
    item_id = int(item["id"])
    with pytest.raises(HTTPException) as exc:
        api.update_action_plan_item(item_id, api.ActionPlanPatch(status="done"))
    assert exc.value.status_code == 422
    started = api.update_action_plan_item(item_id, api.ActionPlanPatch(status="in_progress"))
    assert started["started_at"] and not started["completed_at"]
    api.update_action_plan_item(item_id, api.ActionPlanPatch(status="done", result="Сверка проведена №42"))
    with sqlite3.connect(api.DB_PATH) as conn:
        row = conn.execute("SELECT status,result,started_at,completed_at FROM redesign_action_plan WHERE id=?", (item_id,)).fetchone()
    assert row[:2] == ("done", "Сверка проведена №42")
    assert row[2] and row[3]
    with pytest.raises(HTTPException) as exc:
        api.update_action_plan_item(item_id, api.ActionPlanPatch(status="in_progress"))
    assert exc.value.status_code == 409


def test_finance_costs_use_historical_window(monkeypatch):
    api.wb_cost_response_cache.clear()
    client = Mock()
    client.get_deductions.return_value = []
    client.get_measurement_penalties.return_value = []
    client.get_paid_storage_report.return_value = []
    monkeypatch.setattr(api, "get_token", lambda: "fixture")
    monkeypatch.setattr(api, "WBAPI", lambda *a, **kw: client)
    result = api.get_wb_cost_reports("custom", date(2026, 5, 1), date(2026, 5, 7))
    assert result["period"] == {"start": "2026-05-01", "end": "2026-05-07"}
    client.get_deductions.assert_called_once_with("2026-05-01", "2026-05-07")
    result = api.get_wb_cost_reports("90d")
    assert result["errors"] == [] and client.get_deductions.call_count == 2
    assert (date.fromisoformat(result["period"]["end"]) - date.fromisoformat(result["period"]["start"])).days == 89
    with pytest.raises(HTTPException):
        api.get_wb_cost_reports("custom")


def test_finance_costs_background_does_not_wait_for_paid_storage(monkeypatch):
    api.wb_cost_response_cache.clear()
    api._wb_cost_jobs.clear()
    client = Mock()
    client.get_deductions.return_value = [{"bonusSumm": 12, "photoUrls": ["large"]}]
    client.get_measurement_penalties.return_value = [{"penaltyAmount": 7, "photoUrls": ["large"]}]
    client.get_paid_storage_report.return_value = []
    monkeypatch.setattr(api, "get_token", lambda: "fixture")
    monkeypatch.setattr(api, "WBAPI", lambda *a, **kw: client)
    result = api.get_wb_cost_reports("custom", date(2026, 5, 1), date(2026, 5, 7), background=True)
    assert result["paid_storage_status"] == "processing"
    for _ in range(20):
        time.sleep(0.01)
        result = api.get_wb_cost_reports("custom", date(2026, 5, 1), date(2026, 5, 7), background=True)
        if result["paid_storage_status"] == "ready":
            break
    assert result["paid_storage_status"] == "ready"
    assert "photoUrls" not in result["deductions"][0]
    assert result["measurement_penalties"][0]["penaltyAmount"] == 7


@pytest.mark.parametrize("status,terminal", [(1, False), (3, True), (4, True), (5, True), (6, True), (None, False)])
def test_numeric_price_status_and_item_errors(monkeypatch, status, terminal):
    client = WBAPI("fixture")
    def request(method, url, **kwargs):
        assert method == "GET" and kwargs["params"]["uploadID"] == 42
        if url.endswith("/history/tasks"):
            return {"data": {"status": status}}
        assert url.endswith("/history/goods/task")
        return {"data": {"historyGoods": [{"nmID": 1, "errorText": "price rejected"}]}}
    monkeypatch.setattr(client, "_request", request)
    result = client.get_price_upload_status(42)
    assert result["terminal"] is terminal
    if status in {3, 5, 6}:
        assert result["items"][0]["errorText"] == "price rejected"


def test_price_timeout_does_not_repeat_mutation(monkeypatch):
    client = WBAPI("fixture")
    request = Mock(side_effect=requests.Timeout())
    monkeypatch.setattr(client.session, "request", request)
    with pytest.raises(WBAPIError):
        client.upload_product_prices([{"nmID": 1, "price": 100, "discount": 0}])
    assert request.call_count == 1


@pytest.mark.parametrize("price,discount", [(0, 0), (-1, 0), (100, 100), (100, -1), (100, 1.5)])
def test_invalid_price_never_reaches_wb(price, discount):
    with pytest.raises(ValidationError):
        api.PriceUploadInput(goods=[{"nmID": 1, "price": price, "discount": discount}])
