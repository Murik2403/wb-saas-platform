from unittest.mock import Mock

import pytest
from test_display_contract import api
from wb_api import WBAPI, WBAPIError


@pytest.mark.parametrize("method,endpoint", [("get_deductions", "deductions"), ("get_measurement_penalties", "measurement-penalties")])
def test_retention_contract_required_limit_dates_and_nested_rows(monkeypatch, method, endpoint):
    client = WBAPI("fixture")
    rows = [{"nmId": 1, "bonusSumm": 245, "penaltyAmount": 123}]
    request = Mock(return_value={"data": {"reports": rows, "total": 1}})
    monkeypatch.setattr(client, "_request", request)
    assert getattr(client, method)("2026-08-01", "2026-08-31") == rows
    assert request.call_args.args == ("GET", f"{client.ANALYTICS_URL}/api/analytics/v1/{endpoint}")
    assert request.call_args.kwargs["params"] == {"dateFrom": "2026-08-01T00:00:00+03:00", "dateTo": "2026-08-31T23:59:59+03:00", "limit": 1000, "offset": 0}


def test_retention_paginates_instead_of_reporting_partial_data(monkeypatch):
    client = WBAPI("fixture")
    first = [{"bonusSumm": 4, "id": i} for i in range(1000)]
    second = [{"bonusSumm": 5, "id": 1000}]
    request = Mock(side_effect=[{"data": {"reports": first, "total": 1001}}, {"data": {"reports": second, "total": 1001}}])
    monkeypatch.setattr(client, "_request", request)
    rows = client.get_deductions("2026-08-01", "2026-08-31")
    assert len(rows) == 1001
    assert request.call_args_list[1].kwargs["params"]["offset"] == 1000
    request.side_effect = None
    request.return_value = {"unexpected": []}
    with pytest.raises(WBAPIError):
        client.get_deductions("2026-08-01", "2026-08-31")


def test_retention_page_guard_allows_long_reports(monkeypatch):
    client = WBAPI("fixture")
    calls = []

    def request(method, url, **kwargs):
        calls.append(kwargs["params"]["offset"])
        offset = kwargs["params"]["offset"]
        if offset < 20 * 1000:
            return {"data": {"reports": [{"id": offset + index} for index in range(1000)], "total": 20 * 1000 + 1}}
        return {"data": {"reports": [{"id": offset}], "total": 20 * 1000 + 1}}

    monkeypatch.setattr(client, "_request", request)
    rows = client.get_deductions("2026-08-01", "2026-08-31")
    assert len(rows) == 20 * 1000 + 1
    assert calls[-1] == 20 * 1000
    assert client.RETENTION_MAX_PAGES >= 1000


def test_wb_cost_error_is_actionable_not_raw_json():
    message = api._wb_cost_error("Габариты", WBAPIError('WB API 400: {"requestId":"private-diagnostic", "detail":"limit required"}'))
    assert "{" not in message and "private-diagnostic" not in message
    assert "Габариты" in message


def test_wb_cost_error_explains_partial_large_report():
    message = api._wb_cost_error("Подмены", WBAPIError("retention_report_too_large"))
    assert "частичные данные" in message
    assert "больше 1000 записей" not in message


def test_paid_storage_report_polls_and_accepts_empty_204(monkeypatch):
    client = WBAPI("fixture")
    request = Mock(side_effect=[
        {"data": {"taskId": "task-1"}},
        {"data": {"status": "processing"}},
        {"data": {"status": "done"}},
        None,
    ])
    monkeypatch.setattr(client, "_request", request)
    monkeypatch.setattr("wb_api.time.sleep", lambda _seconds: None)
    assert client.get_paid_storage_report("2026-08-01", "2026-08-08") == []
    assert request.call_args_list[0].kwargs["params"] == {"dateFrom": "2026-08-01", "dateTo": "2026-08-08"}
    assert request.call_args_list[-1].args[1].endswith("/paid_storage/tasks/task-1/download")


def test_paid_storage_report_splits_windows_longer_than_eight_days(monkeypatch):
    client = WBAPI("fixture")
    request = Mock(side_effect=[
        {"data": {"taskId": "task-1"}},
        {"data": {"status": "done"}},
        [{"warehousePrice": 10}],
        {"data": {"taskId": "task-2"}},
        {"data": {"status": "done"}},
        [{"warehousePrice": 20}],
    ])
    monkeypatch.setattr(client, "_request", request)
    assert client.get_paid_storage_report("2026-08-01", "2026-08-09", poll_seconds=0) == [{"warehousePrice": 10}, {"warehousePrice": 20}]
    assert request.call_args_list[0].kwargs["params"] == {"dateFrom": "2026-08-01", "dateTo": "2026-08-08"}
    assert request.call_args_list[3].kwargs["params"] == {"dateFrom": "2026-08-09", "dateTo": "2026-08-09"}
