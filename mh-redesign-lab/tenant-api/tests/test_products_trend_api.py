import importlib.util
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from fastapi import HTTPException

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR.parents[1] / "tenant-app"))
sys.path.insert(0, str(API_DIR))
spec = importlib.util.spec_from_file_location("products_trend_test_api", API_DIR / "api_server.py")
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)


def test_routes_registered():
    paths = api.app.openapi()["paths"]
    assert "/api/v1/dashboard/products/trend" in paths
    assert "/api/v1/dashboard/products/previous" in paths


def test_trend_uses_same_window_as_products(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "compute_products_trend", lambda s, e: calls.append((s, e)) or {"items": [], "bucket_days": 1})
    api.get_products_trend(period="custom", start=date(2026, 9, 10), end=date(2026, 9, 1))  # backwards -> swapped
    assert calls == [(date(2026, 9, 1), date(2026, 9, 10))]


def test_custom_without_dates_is_422():
    for fn in (api.get_products_trend, api.get_products_previous):
        with pytest.raises(HTTPException) as exc:
            fn(period="custom", start=None, end=None)
        assert exc.value.status_code == 422


class _Data:
    def __init__(self, rows, fin):
        self.products = pd.DataFrame(rows)
        self.financial_products = pd.DataFrame(fin)


def test_previous_builds_prior_equal_window_with_same_totals(monkeypatch):
    seen = []
    data = _Data(
        [{"Артикул WB": 1, "Выручка": 1000.0, "Запас, дней": 3.0}, {"Артикул WB": 2, "Выручка": 500.0, "Запас, дней": 30.0}],
        [{"Артикул WB": 1, "Чистая прибыль": 150.0, "Чистая маржинальность, %": 15.0}],
    )
    monkeypatch.setattr(api, "_build_dashboard_cached", lambda s, e: seen.append((s, e)) or data)
    r = api.get_products_previous(period="custom", start=date(2026, 9, 11), end=date(2026, 9, 20))
    assert seen == [(date(2026, 9, 1), date(2026, 9, 10))]
    assert r["has_data"] is True
    assert r["totals"] == {"revenue": 1500.0, "margin_pct": 10.0, "at_risk": 1, "sku_count": 2}
    assert (r["start"], r["end"]) == ("2026-09-01", "2026-09-10")


def test_previous_without_products_reports_no_data(monkeypatch):
    monkeypatch.setattr(api, "_build_dashboard_cached", lambda s, e: _Data([], []))
    r = api.get_products_previous(period="custom", start=date(2026, 9, 11), end=date(2026, 9, 20))
    assert r["has_data"] is False and r["totals"] is None
