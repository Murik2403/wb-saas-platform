import importlib.util
import sys
import time
from datetime import date
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
from openpyxl import load_workbook

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR.parents[1] / "tenant-app"))
sys.path.insert(0, str(API_DIR))
spec = importlib.util.spec_from_file_location("finance_api_test_api", API_DIR / "api_server.py")
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)


def test_finance_endpoint_is_registered():
    paths = api.app.openapi()["paths"]
    assert "/api/v1/dashboard/finance" in paths
    assert "/api/v1/dashboard/finance/fifo-reconciliation" in paths
    assert "/api/v1/operations/finance/export" in paths
    assert "/api/v1/sync/finance" in paths


def test_fifo_reconciliation_details_are_read_only_and_paginated(monkeypatch):
    source = pd.DataFrame([
        {"rrd_id": 101, "operation_date": "2026-09-01", "sale_date": "2026-08-31", "nm_id": 77,
         "supplier_article": "SKU-A", "srid": "srid-a", "event_type": "sale", "units": 2,
         "status": "exact", "reason": "", "eligible_fifo_cogs_rub": 40.0,
         "observed_fifo_cost_rub": 40.0, "layer_sources": ["production"], "matched_event_ids": [1]},
        {"rrd_id": 102, "operation_date": "2026-09-02", "sale_date": "2026-09-01", "nm_id": 77,
         "supplier_article": "SKU-A", "srid": "srid-b", "event_type": "return", "units": 1,
         "status": "unmatched", "reason": "fifo_event_missing", "eligible_fifo_cogs_rub": None,
         "observed_fifo_cost_rub": None, "layer_sources": [], "matched_event_ids": []},
        {"rrd_id": 103, "operation_date": "2026-09-03", "sale_date": "", "nm_id": 88,
         "supplier_article": "SKU-B", "srid": "", "event_type": "", "units": 0,
         "status": "excluded", "reason": "not_sale_or_return_operation", "eligible_fifo_cogs_rub": None,
         "observed_fifo_cost_rub": None, "layer_sources": [], "matched_event_ids": []},
    ])
    calls = []
    monkeypatch.setattr(api, "read_financial_fifo_reconciliation", lambda start, end: calls.append((start, end)) or source)
    monkeypatch.setattr(api, "build_dashboard", lambda *_: (_ for _ in ()).throw(AssertionError("P&L must not be rebuilt")))

    result = api.get_finance_fifo_reconciliation_rows(
        period="custom", start=date(2026, 9, 1), end=date(2026, 9, 3), offset=1, limit=1,
    )

    assert calls == [("2026-09-01", "2026-09-03")]
    assert result["period"] == {"start": "2026-09-01", "end": "2026-09-03"}
    assert (result["total"], result["offset"], result["limit"]) == (2, 1, 1)
    assert result["rows"] == [{
        "rrd_id": 102, "operation_date": "2026-09-02", "sale_date": "2026-09-01", "nm_id": 77,
        "supplier_article": "SKU-A", "srid": "srid-b", "event_type": "return", "units": 1,
        "status": "unmatched", "reason": "fifo_event_missing", "eligible_fifo_cogs_rub": None,
        "observed_fifo_cost_rub": None, "layer_sources": [],
    }]


def test_fifo_reconciliation_details_use_shared_custom_period_normalization(monkeypatch):
    monkeypatch.setattr(api, "read_financial_fifo_reconciliation", lambda *_: pd.DataFrame())
    result = api.get_finance_fifo_reconciliation_rows(
        period="custom", start=date(2026, 9, 2), end=date(2026, 9, 1), offset=0, limit=25,
    )
    assert result["period"] == {"start": "2026-09-01", "end": "2026-09-02"}
    assert result["rows"] == []


def test_finance_response_exposes_fifo_absolute_unit_coverage_without_touching_profit(monkeypatch):
    import types

    monkeypatch.setattr(api, "_build_dashboard_cached", lambda *_: types.SimpleNamespace(
        financial={"profit": 123.45}, financial_products=pd.DataFrame(),
    ))
    monkeypatch.setattr(api, "read_sales_fifo_cogs", lambda *_: pd.DataFrame())
    monkeypatch.setattr(api, "read_financial_fifo_reconciliation", lambda *_: pd.DataFrame([
        {"event_type": "sale", "status": "exact", "units": 2, "eligible_fifo_cogs_rub": 40},
        {"event_type": "return", "status": "unmatched", "units": 1, "eligible_fifo_cogs_rub": None},
    ]))
    monkeypatch.setattr(api, "get_token", lambda: None)

    result = api.get_dashboard_finance(period="custom", start=date(2026, 9, 1), end=date(2026, 9, 2))

    assert result["financial"]["profit"] == 123.45
    assert result["fifo_reconciliation"]["sale_return_units"] == 3
    assert result["fifo_reconciliation"]["exact_units"] == 2
    assert result["fifo_reconciliation"]["exact_unit_coverage_percent"] == 66.67


def test_finance_export_contains_legacy_reconciliation_sheets():
    from report_tools import build_finance_excel, build_reconciliation

    financial = {
        "gross_sales": 1000,
        "to_pay": 800,
        "wb_expenses": 200,
        "profit": 300,
        "net_units": 10,
        "cost_coverage": 100,
    }
    workbook = build_finance_excel(
        start=date(2026, 9, 1),
        end=date(2026, 9, 7),
        financial=financial,
        ad_spend=50,
        financial_products=pd.DataFrame([{"SKU": "A", "Расчётная прибыль": 300}]),
        expense_rows=pd.DataFrame([["Логистика", -100]], columns=["Статья", "Влияние на результат, ₽"]),
        reconciliation=build_reconciliation(financial, financial),
        wb_detail=pd.DataFrame([["2026-09-01", 100]], columns=["operation_date", "amount"]),
        store_operations=pd.DataFrame([["2026-09-01", "sale"]], columns=["operation_date", "type"]),
    )
    sheets = set(load_workbook(BytesIO(workbook), read_only=True).sheetnames)
    assert {"Сводка", "Статьи расходов", "Юнит-экономика", "Сверка WB", "Выгрузка WB", "Операции магазина", "Методика"} <= sheets


def test_wb_token_routes_validate_before_persisting(monkeypatch):
    saved = []

    class FakeClient:
        def __init__(self, token, timeout=60):
            self.token = token
        def test_statistics(self):
            return True

    monkeypatch.setattr(api, "WBAPI", FakeClient)
    monkeypatch.setattr(api, "save_token", saved.append)
    result = api.connect_wb_token(api.WBTokenInput(token="x" * 32))
    assert result["connected"] is True
    assert saved == ["x" * 32]
    assert "token" not in str(result).lower()
    assert "/api/v1/settings/wb-token" in api.app.openapi()["paths"]


def test_wb_token_connection_exposes_real_scope_metadata(monkeypatch):
    saved = []

    class FakeClient:
        TOKEN_SCOPES = {5: "Статистика", 10: "Поставки", 13: "Финансы"}

        def __init__(self, token, timeout=60):
            self.token = token

        def test_statistics(self):
            return True

        def token_scope_info(self):
            return {
                "known": True,
                "scopes": ["Статистика", "Поставки"],
                "missing_scopes": ["Финансы"],
                "read_only": True,
                "expires_at": 4102444800,
            }

    monkeypatch.setattr(api, "WBAPI", FakeClient)
    monkeypatch.setattr(api, "save_token", saved.append)
    result = api.connect_wb_token(api.WBTokenInput(token="z" * 32))
    assert result["scopes"] == ["Статистика", "Поставки"]
    assert result["missing_scopes"] == ["Финансы"]
    assert result["scope_metadata_available"] is True
    assert result["read_only"] is True
    assert result["expires_at"] == 4102444800
    assert saved == ["z" * 32]


def test_wb_token_status_reads_scopes_without_returning_token(monkeypatch):
    class FakeClient:
        TOKEN_SCOPES = {5: "Статистика", 10: "Поставки"}

        def __init__(self, token, timeout=60):
            self.token = token

        def token_scope_info(self):
            return {"known": True, "scopes": ["Статистика"], "missing_scopes": ["Поставки"], "read_only": False, "expires_at": 4102444800}

    monkeypatch.setattr(api, "get_token", lambda: "jwt.secret")
    monkeypatch.setattr(api, "WBAPI", FakeClient)
    result = api.get_wb_token_status()
    assert result == {"connected": True, "scopes": ["Статистика"], "missing_scopes": ["Поставки"], "scope_metadata_available": True, "read_only": False, "expires_at": 4102444800}
    assert "token" not in str(result).lower()


def test_expired_wb_token_is_not_saved(monkeypatch):
    class FakeClient:
        def __init__(self, token, timeout=60):
            pass

        def test_statistics(self):
            return True

        def token_scope_info(self):
            return {"known": True, "scopes": ["Статистика"], "missing_scopes": [], "expires_at": 1}

    saved = []
    monkeypatch.setattr(api, "WBAPI", FakeClient)
    monkeypatch.setattr(api, "save_token", saved.append)
    try:
        api.connect_wb_token(api.WBTokenInput(token="e" * 32))
    except api.HTTPException as exc:
        assert exc.status_code == 422
        assert "истёк" not in str(exc.detail)
    else:
        raise AssertionError("expired token should be rejected")
    assert saved == []


def test_wb_token_is_not_saved_when_validation_fails(monkeypatch):
    class FakeClient:
        def __init__(self, token, timeout=60):
            pass
        def test_statistics(self):
            raise RuntimeError("provider 401 secret body")

    saved = []
    monkeypatch.setattr(api, "WBAPI", FakeClient)
    monkeypatch.setattr(api, "save_token", saved.append)
    try:
        api.connect_wb_token(api.WBTokenInput(token="y" * 32))
    except api.HTTPException as exc:
        assert exc.status_code == 422
        assert "401" not in str(exc.detail)
    else:
        raise AssertionError("invalid token should be rejected")
    assert saved == []


def test_ozon_credentials_routes_never_return_secret(monkeypatch):
    saved = []
    monkeypatch.setattr(api, "save_ozon_credentials", lambda client_id, api_key: saved.append((client_id, api_key)))
    result = api.connect_ozon_credentials(api.OzonCredentialsInput(client_id="123", api_key="k" * 16))
    assert result == {"connected": True}
    assert saved == [("123", "k" * 16)]
    assert "/api/v1/settings/ozon" in api.app.openapi()["paths"]


def test_balance_client_uses_finance_endpoint(monkeypatch):
    from wb_api import WBAPI

    client = WBAPI("token")
    request = Mock(return_value={"current": 1234.5, "for_withdraw": 900.0})
    monkeypatch.setattr(client, "_request", request)
    assert client.get_balance()["current"] == 1234.5
    request.assert_called_once_with("GET", "https://finance-api.wildberries.ru/api/v1/account/balance")


def test_fbs_and_acceptance_methods_use_current_hosts(monkeypatch):
    from wb_api import WBAPI

    client = WBAPI("token")
    calls = []
    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return []
    monkeypatch.setattr(client, "_request", fake_request)
    assert client.get_fbs_supplies() == []
    assert client.get_fbs_new_orders() == []
    assert client.get_acceptance_coefficients() == []
    assert calls[0][1].startswith("https://marketplace-api.wildberries.ru/api/v3/supplies")
    assert calls[1][1].endswith("/api/v3/orders/new")
    assert calls[2][1].endswith("/api/tariffs/v1/acceptance/coefficients")


def test_fbs_supply_detail_and_barcode_routes_are_read_only(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, token, timeout=60):
            assert token == "token"
            assert timeout == 15
        def get_fbs_supply_details(self, supply_id):
            calls.append(("details", supply_id))
            return {"supplyId": supply_id, "orders": [{"id": "o1"}]}
        def get_fbs_supply_barcode(self, supply_id, barcode_type):
            calls.append(("barcode", supply_id, barcode_type))
            return {"barcode": "base64-data", "type": barcode_type}

    monkeypatch.setattr(api, "get_token", lambda: "token")
    monkeypatch.setattr(api, "WBAPI", FakeClient)
    assert api.get_fbs_supply_details("S-123")["supplyId"] == "S-123"
    assert api.get_fbs_supply_barcode("S-123", "svg")["type"] == "svg"
    assert calls == [("details", "S-123"), ("barcode", "S-123", "svg")]
    paths = api.app.openapi()["paths"]
    assert "/api/v1/operations/supply/fbs/{supply_id}" in paths
    assert "/api/v1/operations/supply/fbs/{supply_id}/barcode" in paths


def test_fbs_supply_routes_reject_missing_token_and_invalid_type(monkeypatch):
    monkeypatch.setattr(api, "get_token", lambda: "")
    try:
        api.get_fbs_supply_details("S-123")
    except api.HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("missing WB token should be rejected")
    monkeypatch.setattr(api, "get_token", lambda: "token")
    try:
        api.get_fbs_supply_barcode("S-123", "pdf")
    except api.HTTPException as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("unsupported barcode type should be rejected")


def test_wb_cost_routes_are_registered():
    assert "/api/v1/dashboard/finance/wb-costs" in api.app.openapi()["paths"]


def test_wb_cost_background_returns_before_slow_wb_sections(monkeypatch):
    class SlowClient:
        def __init__(self, token, timeout=60):
            pass

        def get_deductions(self, *_):
            time.sleep(0.4)
            return []

        def get_measurement_penalties(self, *_):
            time.sleep(0.4)
            return []

        def get_paid_storage_report(self, *_):
            time.sleep(0.4)
            return []

    monkeypatch.setattr(api, "get_token", lambda: "background-test-token")
    monkeypatch.setattr(api, "WBAPI", SlowClient)
    started = time.monotonic()
    result = api.get_wb_cost_reports(period="7d", background=True)
    elapsed = time.monotonic() - started
    assert elapsed < 0.25
    assert result["paid_storage_status"] == "processing"
    assert result["errors"]


def test_price_routes_and_upload_contract(monkeypatch):
    from wb_api import WBAPI
    client = WBAPI("token")
    request = Mock(return_value={"data": {"uploadID": 42}})
    monkeypatch.setattr(client, "_request", request)
    assert client.upload_product_prices([{"nmID": 1, "price": 100, "discount": 10}])["data"]["uploadID"] == 42
    request.assert_called_once()
    paths = api.app.openapi()["paths"]
    assert "/api/v1/products/prices" in paths
    assert "/api/v1/products/prices/upload" in paths


def test_quality_client_methods_use_official_hosts(monkeypatch):
    from wb_api import WBAPI
    client = WBAPI("token")
    calls = []
    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("/rating"):
            return {"valuation": 4.8, "feedbackCount": 3}
        if url.endswith("/claims"):
            return {"claims": [], "total": 0}
        return {"data": []}
    monkeypatch.setattr(client, "_request", fake_request)
    assert client.get_seller_rating()["valuation"] == 4.8
    assert client.get_new_feedbacks_questions()["data"] == []
    assert client.get_feedbacks() == []
    assert client.get_questions() == []
    assert client.get_goods_returns() == []
    assert client.get_seller_chats() == []
    assert client.get_quarantine_goods() == []
    assert client.get_returns_claims()["total"] == 0
    assert calls[0][1] == "https://feedbacks-api.wildberries.ru/api/common/v1/rating"
    assert calls[-1][2]["params"] == {"is_archive": "false"}
    assert "/api/v1/dashboard/customer-quality" in api.app.openapi()["paths"]


def test_quality_action_methods_use_safe_payloads(monkeypatch):
    from wb_api import WBAPI
    client = WBAPI("token")
    request = Mock(return_value={"ok": True})
    monkeypatch.setattr(client, "_request", request)
    client.answer_feedback("f1", "Спасибо", edit=False)
    client.answer_question("q1", "Ответ", state="wbRu")
    client.process_return_claim("r1", "approve")
    client.send_seller_message("1:chat-signature", "Добрый день")
    assert request.call_args_list[0].args[:2] == ("POST", "https://feedbacks-api.wildberries.ru/api/v1/feedbacks/answer")
    assert request.call_args_list[1].args[:2] == ("PATCH", "https://feedbacks-api.wildberries.ru/api/v1/questions")
    assert request.call_args_list[2].args[:2] == ("PATCH", "https://returns-api.wildberries.ru/api/v1/claim")
    assert request.call_args_list[3].args[:2] == ("POST", "https://buyer-chat-api.wildberries.ru/api/v1/seller/message")
    paths = api.app.openapi()["paths"]
    assert "/api/v1/customer-quality/feedbacks/answer" in paths
    assert "/api/v1/customer-quality/questions/answer" in paths
    assert "/api/v1/customer-quality/returns/process" in paths
    assert "/api/v1/customer-quality/chats/message" in paths
    assert "/api/v1/operations/reports/definitions/{definition_id}/enabled" in paths


def test_customer_quality_errors_are_actionable_and_non_sensitive():
    assert "категории" in api._customer_quality_error("rating", RuntimeError("WB API 403: requestId=secret"))
    message = api._customer_quality_error("return_history", RuntimeError("WB API 404: token=hidden"))
    assert "временно" in message
    assert "hidden" not in message
