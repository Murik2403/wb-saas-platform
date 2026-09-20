from datetime import date
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR.parents[1] / "tenant-app"))
sys.path.insert(0, str(API_DIR))
spec = importlib.util.spec_from_file_location("management_economics_test_api", API_DIR / "api_server.py")
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)


def test_management_economics_uses_real_product_columns_and_excludes_commission(monkeypatch):
    monkeypatch.setattr(api, "build_dashboard", lambda *_: SimpleNamespace(financial_products=pd.DataFrame([{
        "Артикул WB": 123,
        "Выкупы по цене покупателя": 1000,
        "Продано нетто": 5,
        "Чистая прибыль": 400,
    }])))
    monkeypatch.setattr(api, "read_manual_expenses", lambda **_: [
        {"id": 1, "nm_id": 123, "article": "packaging_labor", "amount_rub": 10, "allocation_basis": "per_unit", "source": "manual", "period_start": "2026-09-01", "period_end": "2026-09-10", "note": "", "created_at": "", "updated_at": ""},
        {"id": 2, "nm_id": 123, "article": "wb_commission", "amount_rub": 50, "allocation_basis": "total", "source": "manual", "period_start": "2026-09-01", "period_end": "2026-09-10", "note": "", "created_at": "", "updated_at": ""},
    ])

    result = api.get_management_economics(nm_id=123, start=date(2026, 9, 1), end=date(2026, 9, 10))

    assert result["data_status"] == "ok"
    assert result["revenue_rub"] == 1000
    assert result["units"] == 5
    assert result["manual_expenses_rub"] == 50
    assert result["management_profit_rub"] == 350
    assert result["excluded_wb_commission_rub"] == 50
    assert result["coverage"]["percent"] > 0
    assert result["items"][0]["allocated_rub"] == 50
    assert result["items"][1]["excluded_from_result"] is True


def test_management_economics_pro_rata_period_total(monkeypatch):
    monkeypatch.setattr(api, "build_dashboard", lambda *_: SimpleNamespace(financial_products=pd.DataFrame([{
        "Артикул WB": 123, "Выкупы по цене покупателя": 1000, "Продано нетто": 5, "Чистая прибыль": 400,
    }])))
    monkeypatch.setattr(api, "read_manual_expenses", lambda **_: [{
        "id": 1, "nm_id": 123, "article": "taxes", "amount_rub": 100, "allocation_basis": "total", "source": "manual",
        "period_start": "2026-09-01", "period_end": "2026-09-10", "note": "", "created_at": "", "updated_at": "",
    }])

    result = api.get_management_economics(nm_id=123, start=date(2026, 9, 1), end=date(2026, 9, 5))

    assert result["manual_expenses_rub"] == 50


def test_management_economics_reports_missing_product(monkeypatch):
    monkeypatch.setattr(api, "build_dashboard", lambda *_: SimpleNamespace(financial_products=pd.DataFrame(columns=["Артикул WB"])))
    monkeypatch.setattr(api, "read_manual_expenses", lambda **_: [])

    result = api.get_management_economics(nm_id=999, start=date(2026, 9, 1), end=date(2026, 9, 1))

    assert result["data_status"] == "no_product_data"
    assert result["revenue_rub"] == 0
