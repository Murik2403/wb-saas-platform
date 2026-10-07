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


def _seed_two_months():
    from db.ingest import upsert_ads, upsert_financial

    def row(rrd_id, day, kind, qty, **kw):
        return dict(rrd_id=rrd_id, rr_dt=day, nm_id=100, doc_type_name=kind, quantity=qty, **kw)

    upsert_financial([
        row(1, "2026-08-10", "Продажа", 2, retail_price_withdisc_rub=1000, retail_amount=1800, ppvz_for_pay=1500,
            ppvz_sales_commission=200, acquiring_fee=40, delivery_rub=100, storage_fee=20, penalty=10,
            deduction=5, acceptance=3, additional_payment=7),
        row(2, "2026-08-20", "Возврат", 1, retail_price_withdisc_rub=1000, retail_amount=-900, ppvz_for_pay=-750,
            ppvz_sales_commission=-100, acquiring_fee=-20),
        row(3, "2026-09-05", "Продажа", 3, retail_price_withdisc_rub=500, retail_amount=1400, ppvz_for_pay=1100,
            ppvz_sales_commission=250, acquiring_fee=30, delivery_rub=60, rebill_logistic_cost=15),
    ])
    upsert_ads([dict(campaign_id=1, day="2026-08-12", nm_id=0, spend=50), dict(campaign_id=1, day="2026-09-04", nm_id=0, spend=70)])
    api.dashboard_data_cache.clear()


def test_management_pnl_previous_period_is_equal_window_before(monkeypatch):
    _seed_two_months()
    result = api.get_management_pnl(period="custom", start=date(2026, 9, 1), end=date(2026, 9, 30), compare_previous=True)
    assert result["accounting_profit_rub"] == 1100 - 75 - 70
    previous = result["previous"]
    assert previous["period"] == {"start": "2026-08-02", "end": "2026-08-31"}
    assert previous["revenue_rub"] == 900
    assert previous["accounting_profit_rub"] == 569  # hand-checked in tenant-app/tests/test_monthly_pnl.py
    assert "expenses" not in previous
    assert "previous" not in api.get_management_pnl(period="custom", start=date(2026, 9, 1), end=date(2026, 9, 30))


def test_management_pnl_previous_period_without_data(monkeypatch):
    _seed_two_months()
    result = api.get_management_pnl(period="custom", start=date(2026, 8, 1), end=date(2026, 8, 31), compare_previous=True)
    assert result["previous"]["data_status"] == "no_financial_data"
    assert result["previous"]["revenue_rub"] == 0


def test_monthly_pnl_columns_add_up_to_management_pnl_totals():
    _seed_two_months()
    start, end = date(2026, 8, 1), date(2026, 9, 30)
    total = api.get_management_pnl(period="custom", start=start, end=end)
    monthly = api.get_management_pnl_monthly(period="custom", start=start, end=end)
    assert [m["month"] for m in monthly["months"]] == ["2026-08", "2026-09"]
    assert sum(m["result"] for m in monthly["months"]) == pytest.approx(total["accounting_profit_rub"])
    assert monthly["total"]["result"] == pytest.approx(total["accounting_profit_rub"])
    assert monthly["total"]["sales"] == pytest.approx(total["revenue_rub"])
    assert monthly["total"]["units"] == pytest.approx(total["units"])


def test_monthly_pnl_rejects_too_long_period_and_handles_empty_store():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as err:
        api.get_management_pnl_monthly(period="custom", start=date(2020, 1, 1), end=date(2026, 9, 30))
    assert err.value.status_code == 422
    empty = api.get_management_pnl_monthly(period="custom", start=date(2026, 9, 1), end=date(2026, 9, 30))
    assert empty["data_status"] == "no_financial_data" and empty["total"]["result"] == 0


def _without_tax(monthly):
    import json
    copy = json.loads(json.dumps(monthly))
    for row in copy["months"] + [copy["total"]]:
        row.pop("tax")
    return json.dumps(copy, sort_keys=True)


def test_tax_block_is_additive_and_settings_roundtrip(monkeypatch):
    monkeypatch.setenv("WB_SAAS_TAX_ESTIMATE_ENABLED", "1")
    monkeypatch.setenv("WB_SAAS_TAX_ACCOUNTANT_REVIEW_REF", "test-review-2026")
    from fastapi import HTTPException
    _seed_two_months()
    kw = dict(period="custom", start=date(2026, 8, 1), end=date(2026, 9, 30))
    before = api.get_management_pnl_monthly(**kw)
    assert before["total"]["tax"] is None and all(m["tax"] is None for m in before["months"])

    assert api.get_tax_settings(year=2026)["regime"] is None
    saved = api.put_tax_settings(api.TaxSettingsInput(year=2026, regime="usn_income", rates=[
        api.TaxRateInput(month=8, usn_pct=6, vat_pct=20), api.TaxRateInput(month=9, usn_pct=6, vat_pct=20)]))
    assert saved["regime"] == "usn_income" and len(saved["rates"]) == 2
    after = api.get_management_pnl_monthly(**kw)
    assert _without_tax(after) == _without_tax(before)  # existing fields untouched
    aug = after["months"][0]
    assert aug["tax"]["vat_out"] == round(aug["sales"] * 20 / 120, 2)
    assert aug["tax"]["total"] == round(aug["tax"]["vat_out"] + aug["tax"]["usn"], 2)
    assert after["total"]["tax"]["status"] == "ok"

    for bad in (
        dict(year=2026, regime="osno"),
        dict(year=2019, regime="usn_income"),
        dict(year=2026, regime="usn_income", rates=[api.TaxRateInput(month=13, usn_pct=6, vat_pct=0)]),
        dict(year=2026, regime="usn_income", rates=[api.TaxRateInput(month=1, usn_pct=101, vat_pct=0)]),
        dict(year=2026, regime="usn_income", rates=[api.TaxRateInput(month=1, usn_pct=6, vat_pct=-1)]),
        dict(year=2026, regime="usn_income", rates=[api.TaxRateInput(month=1, usn_pct=6, vat_pct=0)] * 2),
    ):
        with pytest.raises(HTTPException) as err:
            api.put_tax_settings(api.TaxSettingsInput(**bad))
        assert err.value.status_code == 422
    assert api.get_tax_settings(year=2026)["regime"] == "usn_income"  # failed saves changed nothing


def test_tax_settings_write_is_owner_and_manager_only():
    from production_permissions import ROLES, write_allowed
    for role in ROLES:
        assert write_allowed(role, "PUT", "/api/v1/settings/tax") == (role in {"owner", "manager"})
        if role != "integration_reader":
            assert write_allowed(role, "GET", "/api/v1/settings/tax")


@pytest.mark.parametrize("flag,review", [(None,None),("1",None),("1"," "),("true","approved"),("0","approved")])
def test_tax_gate_defaults_off_without_complete_review_configuration(monkeypatch, flag, review):
    for name,value in (("WB_SAAS_TAX_ESTIMATE_ENABLED",flag),("WB_SAAS_TAX_ACCOUNTANT_REVIEW_REF",review)):
        if value is None: monkeypatch.delenv(name, raising=False)
        else: monkeypatch.setenv(name,value)
    def forbidden(*args,**kwargs):
        raise AssertionError("disabled tax must not read, write, or calculate")
    monkeypatch.setattr(api,"read_tax_settings",forbidden)
    monkeypatch.setattr(api,"save_tax_settings",forbidden)
    monkeypatch.setattr(api,"add_tax_block",forbidden)
    assert api.get_tax_settings(year=2026) == {"enabled":False,"year":2026,"regime":None,"cost_official":False,"rates":[],"supported_regimes":[]}
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        api.put_tax_settings(api.TaxSettingsInput(year=2026,regime="usn_income"))
    assert error.value.status_code == 404
    result=api.get_management_pnl_monthly(period="custom",start=date(2026,9,1),end=date(2026,9,30))
    assert result["tax_estimates_enabled"] is False
    assert "tax" not in result["total"]
    assert all("tax" not in month for month in result["months"])

def test_expense_windows_memoized_without_building_period_totals(monkeypatch):
    calls=[]
    def snapshot(start,end):
        calls.append((start,end))
        return SimpleNamespace(financial_products=pd.DataFrame([{'Артикул WB':123,'Выкупы по цене покупателя':200,'Продано нетто':2,'Чистая прибыль':100}]))
    monkeypatch.setattr(api,'_build_dashboard_cached',snapshot)
    base={'nm_id':123,'article':'packaging_labor','source':'manual','note':'','include_in_management_result':True,'created_at':'','updated_at':''}
    expenses=[{**base,'id':1,'allocation_basis':'per_unit','amount_rub':1,'period_start':'2026-09-01','period_end':'2026-09-01'},
              {**base,'id':2,'allocation_basis':'revenue_pct','amount_rub':5,'period_start':'2026-09-02','period_end':'2026-09-02'},
              {**base,'id':3,'allocation_basis':'per_unit','amount_rub':3,'period_start':'2026-09-01','period_end':'2026-09-01'},
              {**base,'id':4,'allocation_basis':'period_total','amount_rub':9,'period_start':'2026-09-10','period_end':'2026-09-12'}]
    monkeypatch.setattr(api,'read_manual_expenses',lambda **_:expenses)
    result=api.get_management_economics(123,date(2026,9,1),date(2026,9,30))
    assert calls==[(date(2026,9,1),date(2026,9,30)),(date(2026,9,1),date(2026,9,1)),(date(2026,9,2),date(2026,9,2))]
    assert [row['allocated_rub'] for row in result['items']]==[2,10,6,9]
