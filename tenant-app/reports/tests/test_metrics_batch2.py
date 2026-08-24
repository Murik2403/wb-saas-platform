from datetime import date
import pandas as pd
import pytest

from reports import metrics
from reports.metrics import build_metric, MetricResult


def test_ad_funnel_success(monkeypatch):
    df = pd.DataFrame({
        "day": ["2025-01-01", "2025-01-02"],
        "views": [1000, 2000],
        "clicks": [100, 200],
        "atbs": [20, 30],
        "orders_count": [10, 20],
    })
    monkeypatch.setattr(metrics, "read_table", lambda table_name: df if table_name == "ads_daily" else pd.DataFrame())

    res = build_metric("ad_funnel", date(2025, 1, 1), date(2025, 1, 31))
    assert isinstance(res, MetricResult)
    assert res.title == "Воронка конверсии рекламных кампаний"
    assert "Показов: 3000" in res.summary
    assert "кликов: 300" in res.summary
    assert "заказов: 30" in res.summary
    assert "10.00%" in res.summary


def test_ad_funnel_empty(monkeypatch):
    monkeypatch.setattr(metrics, "read_table", lambda table_name: pd.DataFrame())
    res = build_metric("ad_funnel", date(2025, 1, 1), date(2025, 1, 31))
    assert isinstance(res, MetricResult)
    assert "нет" in res.summary.lower()


def test_cost_structure_success(monkeypatch):
    df = pd.DataFrame({
        "nm_id": [101],
        "supplier_article": ["ART-1"],
        "material_cost_rub": [50.0],
        "packaging_cost_rub": [10.0],
        "labor_cost_rub": [20.0],
        "other_cost_rub": [20.0],
    })
    monkeypatch.setattr(metrics, "read_table", lambda table_name: df if table_name == "costs" else pd.DataFrame())

    res = build_metric("cost_structure", date(2025, 1, 1), date(2025, 1, 31))
    assert isinstance(res, MetricResult)
    assert res.title == "Структура себестоимости товаров"
    assert "50.0%" in res.summary


def test_cost_structure_empty(monkeypatch):
    monkeypatch.setattr(metrics, "read_table", lambda table_name: pd.DataFrame())
    res = build_metric("cost_structure", date(2025, 1, 1), date(2025, 1, 31))
    assert isinstance(res, MetricResult)
    assert "нет" in res.summary.lower()


def test_cost_structure_falls_back_to_cost_per_wb_unit_when_breakdown_is_zero(monkeypatch):
    # Regression: sellers who fill in one combined per-unit cost (cost_per_wb_unit)
    # instead of the material/packaging/labor/other breakdown got an empty "no data"
    # chart, even though real cost data exists -- seen live in production 2026-08-25
    # on an account with 37 priced products, none using the breakdown columns.
    df = pd.DataFrame({
        "nm_id": [101, 102],
        "supplier_article": ["ART-1", "ART-2"],
        "cost_per_wb_unit": [110.0, 50.0],
        "material_cost_rub": [0.0, 0.0],
        "packaging_cost_rub": [0.0, 0.0],
        "labor_cost_rub": [0.0, 0.0],
        "other_cost_rub": [0.0, 0.0],
    })
    monkeypatch.setattr(metrics, "read_table", lambda table_name: df if table_name == "costs" else pd.DataFrame())

    res = build_metric("cost_structure", date(2025, 1, 1), date(2025, 1, 31))
    assert isinstance(res, MetricResult)
    assert "нет" not in res.summary.lower()
    assert "без разбивки" in res.summary.lower()


def test_wb_commission_rate_success(monkeypatch):
    fin_df = pd.DataFrame({
        "operation_date": ["2025-01-10", "2025-01-11"],
        "nm_id": [1001, 1001],
        "commission": [150.0, 150.0],
        "acquiring_fee": [10.0, 10.0],
        "retail_amount": [1000.0, 1000.0],
    })
    cat_df = pd.DataFrame({
        "nm_id": [1001],
        "subject_name": ["Одежда"],
    })

    def mock_read_table(table_name):
        if table_name == "financial_report":
            return fin_df
        if table_name == "products_catalog":
            return cat_df
        return pd.DataFrame()

    monkeypatch.setattr(metrics, "read_table", mock_read_table)

    res = build_metric("wb_commission_rate", date(2025, 1, 1), date(2025, 1, 31))
    assert isinstance(res, MetricResult)
    assert res.title == "Эффективная ставка комиссии WB по категориям"
    assert "Одежда" in res.summary
    assert "16.0%" in res.summary


def test_wb_commission_rate_empty(monkeypatch):
    monkeypatch.setattr(metrics, "read_table", lambda table_name: pd.DataFrame())
    res = build_metric("wb_commission_rate", date(2025, 1, 1), date(2025, 1, 31))
    assert isinstance(res, MetricResult)
    assert "нет" in res.summary.lower()


def test_stock_in_transit_success(monkeypatch):
    stocks_df = pd.DataFrame({
        "snapshot_at": ["2025-01-15 10:00:00", "2025-01-15 10:00:00"],
        "warehouse_name": ["Коледино", "Электросталь"],
        "quantity": [80, 100],
        "in_way_to_client": [15, 5],
        "in_way_from_client": [5, 0],
    })
    monkeypatch.setattr(metrics, "read_table", lambda table_name: stocks_df if table_name == "stocks" else pd.DataFrame())

    res = build_metric("stock_in_transit", date(2025, 1, 1), date(2025, 1, 31))
    assert isinstance(res, MetricResult)
    assert res.title == "Остаток на складе vs в пути"
    assert "25 шт" in res.summary
    assert "12.2%" in res.summary


def test_stock_in_transit_empty(monkeypatch):
    monkeypatch.setattr(metrics, "read_table", lambda table_name: pd.DataFrame())
    res = build_metric("stock_in_transit", date(2025, 1, 1), date(2025, 1, 31))
    assert isinstance(res, MetricResult)
    assert "нет" in res.summary.lower()


def test_weekday_pattern_success(monkeypatch):
    orders_df = pd.DataFrame({
        "order_date": ["2025-01-06", "2025-01-06", "2025-01-07"],  # Mon, Mon, Tue
        "is_cancel": [0, 0, 1],
    })
    monkeypatch.setattr(metrics, "read_table", lambda table_name: orders_df if table_name == "orders" else pd.DataFrame())

    res = build_metric("weekday_pattern", date(2025, 1, 1), date(2025, 1, 31))
    assert isinstance(res, MetricResult)
    assert res.title == "Заказы и отмены по дням недели"
    assert "Пн" in res.summary
    assert "Вт" in res.summary
    assert "100.0%" in res.summary


def test_weekday_pattern_empty(monkeypatch):
    monkeypatch.setattr(metrics, "read_table", lambda table_name: pd.DataFrame())
    res = build_metric("weekday_pattern", date(2025, 1, 1), date(2025, 1, 31))
    assert isinstance(res, MetricResult)
    assert "нет" in res.summary.lower()
