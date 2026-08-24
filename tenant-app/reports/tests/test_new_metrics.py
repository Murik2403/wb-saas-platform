"""Tests for the 7 metrics added on top of calculations.build_dashboard()'s
already-computed P&L / per-article / ads / stocks breakdowns (pnl,
abc_analysis, buyout_rate, oos_risk, wb_deductions, ad_performance,
warehouse_stocks). Fake DashboardData mirrors the exact dict keys / Russian
column names calculations.py actually produces -- see
calculations.py's DashboardData.financial and .financial_products for the
authoritative shape.
"""
from datetime import date

import pandas as pd

from reports import metrics


class FakeDashboardData:
    def __init__(self, financial=None, financial_products=None, ads=None, stocks=None):
        self.financial = financial or {}
        self.financial_products = financial_products if financial_products is not None else pd.DataFrame()
        self.ads = ads if ads is not None else pd.DataFrame()
        self.stocks = stocks if stocks is not None else pd.DataFrame()


def fake_financial() -> dict:
    return {
        "sales": 100000.0,
        "wb_expenses": 20000.0,
        "cost": 30000.0,
        "financial_ad_spend": 10000.0,
        "profit": 40000.0,
        "logistics": 12000.0,
        "rebill_logistics": 1000.0,
        "storage": 3000.0,
        "penalties": 500.0,
        "deductions": 2000.0,
        "acceptance": 1500.0,
    }


def fake_financial_products() -> pd.DataFrame:
    return pd.DataFrame({
        "Артикул WB": [111, 222, 333, 444],
        "Артикул продавца": ["A1", "A2", "A3", "A4"],
        "Расчётная прибыль": [7000.0, 2000.0, 900.0, 100.0],
        "Продажи, шт": [50, 30, 10, 5],
        "Возвраты, %": [10.0, 20.0, 0.0, 50.0],
        "Продано нетто": [45, 24, 10, 3],
        "Остаток": [100, 50, 0, 20],
        "Запас, дней": [5.0, 20.0, 30.0, 3.0],
    })


def fake_ads() -> pd.DataFrame:
    return pd.DataFrame({
        "Кампания": [1001, 1002],
        "Расход": [1000.0, 500.0],
        "Заказы": [10, 0],
        "CTR, %": [1.5, 0.8],
    })


def fake_stocks_by_warehouse() -> pd.DataFrame:
    return pd.DataFrame({
        "Склад": ["Коледино", "Коледино", "Электросталь"],
        "Остаток": [200, 100, 100],
    })


# --------------------------------------------------------------------------
# pnl
# --------------------------------------------------------------------------

def test_pnl_summary_uses_full_ad_spend_not_unallocated_only(monkeypatch):
    # Regression: financial_ad_spend is the TOTAL ad spend; unallocated_ad_spend
    # is only the leftover portion not attributed to a specific article --
    # using the latter here would make the P&L bars not sum to profit.
    fin = fake_financial()
    fin["unallocated_ad_spend"] = 1.0  # deliberately different from financial_ad_spend
    monkeypatch.setattr(metrics, "build_dashboard", lambda s, e: FakeDashboardData(financial=fin))
    result = metrics.build_metric("pnl", date(2026, 8, 1), date(2026, 8, 5))
    assert "10 000₽" in result.summary or "10000₽" in result.summary.replace(" ", "")
    assert "40 000₽" in result.summary or "40000₽" in result.summary.replace(" ", "")


def test_pnl_summary_reports_all_headline_numbers(monkeypatch):
    monkeypatch.setattr(metrics, "build_dashboard", lambda s, e: FakeDashboardData(financial=fake_financial()))
    result = metrics.build_metric("pnl", date(2026, 8, 1), date(2026, 8, 5))
    stripped = result.summary.replace(" ", "").replace(" ", "")
    assert "100000₽" in stripped   # sales
    assert "20000₽" in stripped    # wb_expenses
    assert "30000₽" in stripped    # cost
    assert "40000₽" in stripped    # profit


# --------------------------------------------------------------------------
# abc_analysis
# --------------------------------------------------------------------------

def test_abc_analysis_classifies_by_cumulative_profit_share(monkeypatch):
    monkeypatch.setattr(
        metrics, "build_dashboard",
        lambda s, e: FakeDashboardData(financial_products=fake_financial_products()),
    )
    result = metrics.build_metric("abc_analysis", date(2026, 8, 1), date(2026, 8, 5))
    # profits 7000/2000/900/100 -> cum% 70/90/99/100 -> groups A/B/C/C
    assert "A" in result.summary and "1" in result.summary
    assert "4" in result.summary  # total profitable products


def test_abc_analysis_handles_empty(monkeypatch):
    monkeypatch.setattr(metrics, "build_dashboard", lambda s, e: FakeDashboardData())
    result = metrics.build_metric("abc_analysis", date(2026, 8, 1), date(2026, 8, 5))
    assert "нет" in result.summary.lower()


# --------------------------------------------------------------------------
# buyout_rate
# --------------------------------------------------------------------------

def test_buyout_rate_average(monkeypatch):
    monkeypatch.setattr(
        metrics, "build_dashboard",
        lambda s, e: FakeDashboardData(financial_products=fake_financial_products()),
    )
    result = metrics.build_metric("buyout_rate", date(2026, 8, 1), date(2026, 8, 5))
    # buyout% per row: 90, 80, 100, 50 -> average 80.0
    assert "80.0%" in result.summary


def test_buyout_rate_handles_empty(monkeypatch):
    monkeypatch.setattr(metrics, "build_dashboard", lambda s, e: FakeDashboardData())
    result = metrics.build_metric("buyout_rate", date(2026, 8, 1), date(2026, 8, 5))
    assert "нет" in result.summary.lower()


# --------------------------------------------------------------------------
# oos_risk
# --------------------------------------------------------------------------

def test_oos_risk_counts_critical_items(monkeypatch):
    monkeypatch.setattr(
        metrics, "build_dashboard",
        lambda s, e: FakeDashboardData(financial_products=fake_financial_products()),
    )
    result = metrics.build_metric("oos_risk", date(2026, 8, 1), date(2026, 8, 5))
    # rows with Остаток > 0: days 5, 20, 3 -- both 5 and 3 are < 7 -> 2 critical
    assert "2 шт" in result.summary
    assert "3 дн" in result.summary  # minimum days


def test_oos_risk_handles_empty(monkeypatch):
    monkeypatch.setattr(metrics, "build_dashboard", lambda s, e: FakeDashboardData())
    result = metrics.build_metric("oos_risk", date(2026, 8, 1), date(2026, 8, 5))
    assert "нет" in result.summary.lower()


# --------------------------------------------------------------------------
# wb_deductions
# --------------------------------------------------------------------------

def test_wb_deductions_totals_and_top_category(monkeypatch):
    monkeypatch.setattr(metrics, "build_dashboard", lambda s, e: FakeDashboardData(financial=fake_financial()))
    result = metrics.build_metric("wb_deductions", date(2026, 8, 1), date(2026, 8, 5))
    # logistics(12000)+rebill(1000)=13000 is the largest bucket -> "Логистика"
    assert "Логистика" in result.summary
    stripped = result.summary.replace(" ", "").replace(" ", "")
    assert "20000₽" in stripped  # total = wb_expenses


def test_wb_deductions_handles_all_zero(monkeypatch):
    monkeypatch.setattr(metrics, "build_dashboard", lambda s, e: FakeDashboardData(financial={}))
    result = metrics.build_metric("wb_deductions", date(2026, 8, 1), date(2026, 8, 5))
    assert "нет" in result.summary.lower()


# --------------------------------------------------------------------------
# ad_performance
# --------------------------------------------------------------------------

def test_ad_performance_summary(monkeypatch):
    monkeypatch.setattr(metrics, "build_dashboard", lambda s, e: FakeDashboardData(ads=fake_ads()))
    result = metrics.build_metric("ad_performance", date(2026, 8, 1), date(2026, 8, 5))
    assert "2" in result.summary  # 2 campaigns
    stripped = result.summary.replace(" ", "").replace(" ", "")
    assert "1500₽" in stripped  # total spend 1000+500


def test_ad_performance_handles_zero_orders_without_dividing_by_zero(monkeypatch):
    # campaign 1002 has Заказы=0 -- CPO must not raise ZeroDivisionError.
    monkeypatch.setattr(metrics, "build_dashboard", lambda s, e: FakeDashboardData(ads=fake_ads()))
    result = metrics.build_metric("ad_performance", date(2026, 8, 1), date(2026, 8, 5))
    assert result.summary  # completed without raising


def test_ad_performance_handles_empty(monkeypatch):
    monkeypatch.setattr(metrics, "build_dashboard", lambda s, e: FakeDashboardData())
    result = metrics.build_metric("ad_performance", date(2026, 8, 1), date(2026, 8, 5))
    assert "нет" in result.summary.lower()


# --------------------------------------------------------------------------
# warehouse_stocks
# --------------------------------------------------------------------------

def test_warehouse_stocks_groups_by_warehouse(monkeypatch):
    monkeypatch.setattr(metrics, "build_dashboard", lambda s, e: FakeDashboardData(stocks=fake_stocks_by_warehouse()))
    result = metrics.build_metric("warehouse_stocks", date(2026, 8, 1), date(2026, 8, 5))
    # Коледино: 200+100=300 out of total 400 -> 75.0%
    assert "Коледино" in result.summary
    assert "300 шт" in result.summary
    assert "75.0%" in result.summary


def test_warehouse_stocks_handles_empty(monkeypatch):
    monkeypatch.setattr(metrics, "build_dashboard", lambda s, e: FakeDashboardData())
    result = metrics.build_metric("warehouse_stocks", date(2026, 8, 1), date(2026, 8, 5))
    assert "нет" in result.summary.lower()


# --------------------------------------------------------------------------
# All 10 metrics together in one PDF
# --------------------------------------------------------------------------

def test_all_ten_metrics_produce_a_valid_pdf(monkeypatch):
    from reports.pdf_builder import build_report_pdf

    fake_data = FakeDashboardData(
        financial=fake_financial(),
        financial_products=fake_financial_products(),
        ads=fake_ads(),
        stocks=fake_stocks_by_warehouse(),
    )
    fake_data.daily = pd.DataFrame({
        "day": pd.date_range("2026-08-01", periods=3).date,
        "orders": [1, 2, 1], "sales": [1, 1, 1],
        "ad_spend": [100, 200, 100], "drr": [10.0, 20.0, 10.0],
    })
    monkeypatch.setattr(metrics, "build_dashboard", lambda s, e: fake_data)

    def fake_read_table(name):
        if name == "stocks":
            return pd.DataFrame({"snapshot_at": ["2026-08-05"], "nm_id": [111], "quantity": [10]})
        return pd.DataFrame({"nm_id": [111], "supplier_article": ["A1"]})
    monkeypatch.setattr(metrics, "read_table", fake_read_table)

    all_codes = list(metrics.METRIC_BUILDERS.keys())
    pdf_bytes = build_report_pdf("Полный отчёт", all_codes, date(2026, 8, 1), date(2026, 8, 5))
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000
