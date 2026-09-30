from datetime import date
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR.parents[1] / "tenant-app"))
sys.path.insert(0, str(API_DIR))
spec = importlib.util.spec_from_file_location("management_economics_test_api", API_DIR / "api_server.py")
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)


@pytest.fixture(autouse=True)
def coverage_database(monkeypatch, tmp_path):
    import db.core as core
    monkeypatch.setattr(core, "DB_PATH", tmp_path / "coverage.sqlite3")
    core.init_db()


def test_management_economics_uses_real_product_columns_and_excludes_commission(monkeypatch):
    monkeypatch.setattr(api, "build_dashboard", lambda *_: SimpleNamespace(financial_products=pd.DataFrame([{
        "Артикул WB": 123,
        "Выкупы по цене покупателя": 1000,
        "Продано нетто": 5,
        "Чистая прибыль": 400,
    }])))
    monkeypatch.setattr(api, "read_manual_expenses", lambda **_: [
        {"id": 1, "nm_id": 123, "article": "packaging_labor", "amount_rub": 10, "allocation_basis": "per_unit", "source": "manual", "period_start": "2026-09-01", "period_end": "2026-09-10", "note": "", "include_in_management_result": False, "created_at": "", "updated_at": ""},
        {"id": 2, "nm_id": 123, "article": "wb_commission", "amount_rub": 50, "allocation_basis": "total", "source": "manual", "period_start": "2026-09-01", "period_end": "2026-09-10", "note": "", "include_in_management_result": True, "created_at": "", "updated_at": ""},
    ])

    result = api.get_management_economics(nm_id=123, start=date(2026, 9, 1), end=date(2026, 9, 10))

    assert result["data_status"] == "ok"
    assert result["revenue_rub"] == 1000
    assert result["units"] == 5
    assert result["manual_expenses_rub"] == 0
    assert result["management_profit_rub"] == 400
    assert result["excluded_manual_expenses_rub"] == 50
    assert result["items"][0]["excluded_from_result"] is True
    assert result["items"][0]["exclusion_reason"]
    assert result["excluded_wb_commission_rub"] == 50
    assert result["coverage"]["percent"] == 0
    assert result["items"][0]["allocated_rub"] == 50
    assert result["items"][1]["excluded_from_result"] is True


def test_management_economics_pro_rata_period_total(monkeypatch):
    monkeypatch.setattr(api, "build_dashboard", lambda *_: SimpleNamespace(financial_products=pd.DataFrame([{
        "Артикул WB": 123, "Выкупы по цене покупателя": 1000, "Продано нетто": 5, "Чистая прибыль": 400,
    }])))
    monkeypatch.setattr(api, "read_manual_expenses", lambda **_: [{
        "id": 1, "nm_id": 123, "article": "taxes", "amount_rub": 100, "allocation_basis": "total", "source": "manual",
        "period_start": "2026-09-01", "period_end": "2026-09-10", "note": "", "include_in_management_result": True, "created_at": "", "updated_at": "",
    }])

    result = api.get_management_economics(nm_id=123, start=date(2026, 9, 1), end=date(2026, 9, 5))

    assert result["manual_expenses_rub"] == 50


def test_management_economics_reports_missing_product(monkeypatch):
    monkeypatch.setattr(api, "build_dashboard", lambda *_: SimpleNamespace(financial_products=pd.DataFrame(columns=["Артикул WB"])))
    monkeypatch.setattr(api, "read_manual_expenses", lambda **_: [])

    result = api.get_management_economics(nm_id=999, start=date(2026, 9, 1), end=date(2026, 9, 1))

    assert result["data_status"] == "no_product_data"
    assert result["revenue_rub"] == 0


def test_management_economics_revenue_percentage_basis(monkeypatch):
    monkeypatch.setattr(api, "build_dashboard", lambda *_: SimpleNamespace(financial_products=pd.DataFrame([{
        "Артикул WB": 123, "Выкупы по цене покупателя": 2000, "Продано нетто": 4, "Чистая прибыль": 900,
    }])))
    monkeypatch.setattr(api, "read_manual_expenses", lambda **_: [{
        "id": 1, "nm_id": 123, "article": "advertising", "amount_rub": 5, "allocation_basis": "revenue_pct", "source": "manual",
        "period_start": "2026-09-01", "period_end": "2026-09-01", "note": "", "include_in_management_result": True, "created_at": "", "updated_at": "",
    }])
    result = api.get_management_economics(nm_id=123, start=date(2026, 9, 1), end=date(2026, 9, 1))
    assert result["manual_expenses_rub"] == 100


def test_management_economics_periodizes_rates_to_operation_overlap(monkeypatch):
    snapshots = {
        (date(2026, 9, 1), date(2026, 9, 30)): (30, 30000),
        (date(2026, 9, 1), date(2026, 9, 1)): (1, 1000),
        (date(2026, 9, 5), date(2026, 9, 15)): (6, 6000),
        (date(2026, 9, 5), date(2026, 9, 10)): (6, 6000),
    }

    def dashboard(start, end):
        units, revenue = snapshots[(start, end)]
        return SimpleNamespace(financial_products=pd.DataFrame([{
            "Артикул WB": 123, "Выкупы по цене покупателя": revenue,
            "Продано нетто": units, "Чистая прибыль": 0,
        }]))

    monkeypatch.setattr(api, "build_dashboard", dashboard)
    monkeypatch.setattr(api, "read_manual_expenses", lambda **_: [
        {"id": 1, "nm_id": 123, "article": "packaging_labor", "amount_rub": 10,
         "allocation_basis": "per_unit", "source": "manual", "period_start": "2026-09-01",
         "period_end": "2026-09-01", "note": "", "include_in_management_result": True, "created_at": "", "updated_at": ""},
        {"id": 2, "nm_id": 123, "article": "advertising", "amount_rub": 5,
         "allocation_basis": "revenue_pct", "source": "manual", "period_start": "2026-09-01",
         "period_end": "2026-09-01", "note": "", "include_in_management_result": True, "created_at": "", "updated_at": ""},
        {"id": 3, "nm_id": 123, "article": "packaging_labor", "amount_rub": 10,
         "allocation_basis": "per_unit", "source": "manual", "period_start": "2026-09-05",
         "period_end": "2026-09-15", "note": "", "include_in_management_result": True, "created_at": "", "updated_at": ""},
    ])

    result = api.get_management_economics(nm_id=123, start=date(2026, 9, 1), end=date(2026, 9, 30))

    # A one-day expense uses only the one-day operation snapshot, not the
    # 30-day product totals: 10 * 1 unit + 5% * 1,000 ₽.
    assert [item["allocated_rub"] for item in result["items"]] == [10, 50, 60]
    assert result["manual_expenses_rub"] == 120

    partial = api.get_management_economics(nm_id=123, start=date(2026, 9, 5), end=date(2026, 9, 10))
    assert partial["items"][0]["allocated_rub"] == 0
    assert partial["items"][1]["allocated_rub"] == 0
    assert partial["items"][2]["allocated_rub"] == 60


def test_management_economics_returns_and_zero_revenue_are_signed_and_safe(monkeypatch):
    def dashboard(start, end):
        if start == date(2026, 9, 2):
            units, revenue = 0, 0
        else:
            units, revenue = -1, -100
        return SimpleNamespace(financial_products=pd.DataFrame([{
            "Артикул WB": 123, "Выкупы по цене покупателя": revenue,
            "Продано нетто": units, "Чистая прибыль": 0,
        }]))

    monkeypatch.setattr(api, "build_dashboard", dashboard)
    monkeypatch.setattr(api, "read_manual_expenses", lambda **_: [
        {"id": 1, "nm_id": 123, "article": "packaging_labor", "amount_rub": 10,
         "allocation_basis": "per_unit", "source": "manual", "period_start": "2026-09-01",
         "period_end": "2026-09-01", "note": "", "include_in_management_result": True, "created_at": "", "updated_at": ""},
        {"id": 2, "nm_id": 123, "article": "advertising", "amount_rub": 5,
         "allocation_basis": "revenue_pct", "source": "manual", "period_start": "2026-09-01",
         "period_end": "2026-09-01", "note": "", "include_in_management_result": True, "created_at": "", "updated_at": ""},
    ])

    returned = api.get_management_economics(nm_id=123, start=date(2026, 9, 1), end=date(2026, 9, 1))
    assert [item["allocated_rub"] for item in returned["items"]] == [-10, -5]

    zero = api.get_management_economics(nm_id=123, start=date(2026, 9, 2), end=date(2026, 9, 2))
    assert [item["allocated_rub"] for item in zero["items"]] == [0, 0]


def test_management_economics_no_overlap_allocates_no_rate_expense(monkeypatch):
    monkeypatch.setattr(api, "build_dashboard", lambda *_: SimpleNamespace(financial_products=pd.DataFrame([{
        "Артикул WB": 123, "Выкупы по цене покупателя": 1000,
        "Продано нетто": 5, "Чистая прибыль": 0,
    }])))
    monkeypatch.setattr(api, "read_manual_expenses", lambda **_: [{
        "id": 1, "nm_id": 123, "article": "advertising", "amount_rub": 5,
        "allocation_basis": "revenue_pct", "source": "manual", "period_start": "2026-08-01",
        "period_end": "2026-08-01", "note": "", "created_at": "", "updated_at": "",
    }])

    result = api.get_management_economics(nm_id=123, start=date(2026, 9, 1), end=date(2026, 9, 30))

    assert result["items"][0]["allocated_rub"] == 0
    assert result["manual_expenses_rub"] == 0


def test_management_economics_does_not_double_subtract_overlapping_legacy_rows(monkeypatch):
    monkeypatch.setattr(api, "build_dashboard", lambda *_: SimpleNamespace(financial_products=pd.DataFrame([{
        "Артикул WB": 123, "Выкупы по цене покупателя": 1000,
        "Продано нетто": 1, "Чистая прибыль": 500,
    }])) )
    articles = ("purchase_production", "logistics_returns", "storage", "acceptance", "advertising", "packaging_labor", "wb_commission")
    monkeypatch.setattr(api, "read_manual_expenses", lambda **_: [
        {"id": index, "nm_id": 123, "article": article, "amount_rub": 10,
         "allocation_basis": "total", "source": "manual", "period_start": "2026-09-01",
         "period_end": "2026-09-01", "note": "legacy row without explicit opt-in"}
        for index, article in enumerate(articles, start=1)
    ])

    result = api.get_management_economics(nm_id=123, start=date(2026, 9, 1), end=date(2026, 9, 1))

    assert result["accounting_profit_rub"] == 500
    assert result["management_profit_rub"] == 500
    assert result["manual_expenses_rub"] == 0
    assert result["excluded_manual_expenses_rub"] == 60
    assert result["excluded_wb_commission_rub"] == 10
    assert all(item["excluded_from_result"] for item in result["items"])
    assert all(item["exclusion_reason"] for item in result["items"])


def test_management_economics_includes_attested_external_expense_only(monkeypatch):
    monkeypatch.setattr(api, "build_dashboard", lambda *_: SimpleNamespace(financial_products=pd.DataFrame([{
        "Артикул WB": 123, "Выкупы по цене покупателя": 1000,
        "Продано нетто": 1, "Чистая прибыль": 500,
    }])) )
    monkeypatch.setattr(api, "read_manual_expenses", lambda **_: [{
        "id": 1, "nm_id": 123, "article": "taxes", "amount_rub": 75,
        "allocation_basis": "total", "source": "manual", "period_start": "2026-09-01",
        "period_end": "2026-09-01", "note": "external tax payment", "include_in_management_result": True,
    }])

    result = api.get_management_economics(nm_id=123, start=date(2026, 9, 1), end=date(2026, 9, 1))

    assert result["accounting_profit_rub"] == 500
    assert result["management_profit_rub"] == 425
    assert result["manual_expenses_rub"] == 75
    assert result["items"][0]["excluded_from_result"] is False
    assert result["items"][0]["exclusion_reason"] is None


def test_manual_expense_api_defaults_to_excluded_and_exposes_opt_in(monkeypatch):
    saved = {}
    monkeypatch.setattr(api, "save_manual_expense", lambda **kwargs: saved.update(kwargs) or 7)
    payload = api.ManualExpenseInput(
        nm_id=123, article="taxes", amount_rub=25, allocation_basis="total",
        source="manual", period_start=date(2026, 9, 1), period_end=date(2026, 9, 1),
    )

    created = api.create_manual_expense(payload)

    assert created["id"] == 7
    assert saved["include_in_management_result"] is False

    toggled = {}
    monkeypatch.setattr(api, "set_manual_expense_inclusion", lambda expense_id, included: toggled.update(id=expense_id, included=included) or True)
    response = api.update_manual_expense_inclusion(7, api.ManualExpenseInclusionInput(include_in_management_result=True))
    assert response == {"id": 7, "include_in_management_result": True}
    assert toggled == {"id": 7, "included": True}


def test_management_economics_rejects_reversed_period():
    import pytest
    with pytest.raises(api.HTTPException) as error:
        api.get_management_economics(nm_id=123, start=date(2026, 9, 2), end=date(2026, 9, 1))
    assert error.value.status_code == 422
