from datetime import date

import pandas as pd

from reports import metrics
from reports.pdf_builder import build_report_pdf


class FakeDashboardData:
    def __init__(self, daily: pd.DataFrame):
        self.daily = daily


def fake_daily() -> pd.DataFrame:
    return pd.DataFrame({
        "day": pd.date_range("2026-08-01", periods=5).date,
        "orders": [1, 2, 0, 3, 1],
        "sales": [1, 1, 0, 2, 1],
        "order_amount": [1000, 2000, 0, 3000, 1000],
        "revenue": [1000, 1000, 0, 2000, 1000],
        "ad_spend": [100, 200, 0, 300, 100],
        "ad_revenue": [500, 500, 0, 1000, 500],
        "drr": [10.0, 20.0, 0.0, 15.0, 10.0],
    })


def fake_stocks() -> pd.DataFrame:
    return pd.DataFrame({
        "nm_id": [111, 222, 333],
        "quantity": [10, 5, 20],
    })


def fake_catalog() -> pd.DataFrame:
    return pd.DataFrame({
        "nm_id": [111, 222, 333],
        "supplier_article": ["A1", "A2", "A3"],
    })


def test_sales_orders_metric_summary(monkeypatch):
    monkeypatch.setattr(metrics, "build_dashboard", lambda start, end: FakeDashboardData(fake_daily()))
    result = metrics.build_metric("sales_orders", date(2026, 8, 1), date(2026, 8, 5))
    assert "заказов 7" in result.summary
    assert "продаж 5" in result.summary


def test_ads_metric_summary(monkeypatch):
    monkeypatch.setattr(metrics, "build_dashboard", lambda start, end: FakeDashboardData(fake_daily()))
    result = metrics.build_metric("ads", date(2026, 8, 1), date(2026, 8, 5))
    assert "700" in result.summary  # total ad_spend


def test_stocks_metric_handles_empty(monkeypatch):
    monkeypatch.setattr(metrics, "read_table", lambda name: pd.DataFrame())
    result = metrics.build_metric("stocks", date(2026, 8, 1), date(2026, 8, 5))
    assert "нет" in result.summary.lower()


def test_stocks_metric_with_data(monkeypatch):
    def fake_read_table(name):
        return fake_stocks() if name == "stocks" else fake_catalog()
    monkeypatch.setattr(metrics, "read_table", fake_read_table)
    result = metrics.build_metric("stocks", date(2026, 8, 1), date(2026, 8, 5))
    assert "35" in result.summary  # 10+5+20


def test_unknown_metric_raises():
    import pytest
    with pytest.raises(ValueError):
        metrics.build_metric("nonexistent", date(2026, 8, 1), date(2026, 8, 5))


def test_build_report_pdf_produces_valid_pdf_bytes(monkeypatch):
    monkeypatch.setattr(metrics, "build_dashboard", lambda start, end: FakeDashboardData(fake_daily()))

    def fake_read_table(name):
        return fake_stocks() if name == "stocks" else fake_catalog()
    monkeypatch.setattr(metrics, "read_table", fake_read_table)

    pdf_bytes = build_report_pdf("Тестовый отчёт", ["sales_orders", "ads", "stocks"], date(2026, 8, 1), date(2026, 8, 5))
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000
