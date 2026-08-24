from datetime import date

import matplotlib.pyplot as plt
import pandas as pd

from reports import metrics
from reports.pdf_builder import build_report_pdf, format_page_number


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
        "snapshot_at": ["2026-08-05T12:00:00"] * 3,
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


def test_stocks_metric_only_uses_latest_snapshot(monkeypatch):
    # `stocks` accumulates one row per sync snapshot -- an older snapshot
    # (with a different quantity) sitting alongside the latest one must not
    # get summed in, or totals balloon with every historical sync.
    stale_and_fresh = pd.DataFrame({
        "snapshot_at": ["2026-08-01T00:00:00", "2026-08-01T00:00:00", "2026-08-05T12:00:00", "2026-08-05T12:00:00", "2026-08-05T12:00:00"],
        "nm_id": [111, 222, 111, 222, 333],
        "quantity": [999, 999, 10, 5, 20],
    })

    def fake_read_table(name):
        return stale_and_fresh if name == "stocks" else fake_catalog()
    monkeypatch.setattr(metrics, "read_table", fake_read_table)
    result = metrics.build_metric("stocks", date(2026, 8, 1), date(2026, 8, 5))
    assert "35" in result.summary  # only the 2026-08-05 snapshot: 10+5+20
    assert "999" not in result.summary


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


def test_format_page_number():
    assert format_page_number(1, 5) == "Стр. 1 из 5"
    assert format_page_number(10, 10) == "Стр. 10 из 10"


def test_classify_stock_risk():
    assert metrics.classify_stock_risk(3.5) == "critical"
    assert metrics.classify_stock_risk(6.9) == "critical"
    assert metrics.classify_stock_risk(7.0) == "warn"
    assert metrics.classify_stock_risk(14.0) == "warn"
    assert metrics.classify_stock_risk(14.1) == "good"
    assert metrics.classify_stock_risk(30.0) == "good"


def test_get_status_color():
    assert metrics.get_status_color("critical") == metrics.COLOR_CRITICAL
    assert metrics.get_status_color("warn") == metrics.COLOR_WARN
    assert metrics.get_status_color("good") == metrics.COLOR_GOOD
    assert metrics.get_status_color("accent") == metrics.COLOR_ACCENT
    assert metrics.get_status_color("unknown") == metrics.COLOR_TEXT


def test_apply_chart_style_hides_top_right_spines_by_default():
    fig, ax = plt.subplots()
    try:
        metrics._apply_chart_style(fig, ax)
        assert ax.spines["top"].get_visible() is False
        assert ax.spines["right"].get_visible() is False
        assert ax.spines["left"].get_visible() is True
        assert ax.spines["bottom"].get_visible() is True
    finally:
        plt.close(fig)
