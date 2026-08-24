from dataclasses import dataclass
from datetime import date
import pandas as pd
import pytest

from reports import metrics


@dataclass
class DummyDashboard:
    financial_products: pd.DataFrame


START_DATE = date(2025, 1, 1)
END_DATE = date(2025, 1, 31)


def test_wb_incident_losses_normal(monkeypatch):
    cases_df = pd.DataFrame([
        {
            "id": 1,
            "incident_key": "INC-001",
            "incident_name": "Пожар на складе",
            "incident_date": "2025-01-15",
            "warehouse_label": "Электросталь",
            "status": "CLOSED",
            "confirmed_loss_units": 10,
            "confirmed_loss_cost_rub": 10000.0,
            "compensation_rub": 4000.0,
            "incident_result_rub": -6000.0,
        },
        {
            "id": 2,
            "incident_key": "INC-002",
            "incident_name": "Повреждение при приёмке",
            "incident_date": "2025-01-20",
            "warehouse_label": "Коледино",
            "status": "CLOSED",
            "confirmed_loss_units": 5,
            "confirmed_loss_cost_rub": 5000.0,
            "compensation_rub": 5000.0,
            "incident_result_rub": 0.0,
        },
    ])

    monkeypatch.setattr(metrics, "read_wb_incident_cases", lambda limit=200: cases_df)

    res = metrics.build_metric("wb_incident_losses", START_DATE, END_DATE)
    assert res.title == "Потери и компенсации на складах WB"
    assert res.figure is not None
    assert "15 000₽" in res.summary
    assert "9 000₽" in res.summary
    assert "6 000₽" in res.summary


def test_wb_incident_losses_empty(monkeypatch):
    monkeypatch.setattr(metrics, "read_wb_incident_cases", lambda limit=200: pd.DataFrame())

    res = metrics.build_metric("wb_incident_losses", START_DATE, END_DATE)
    assert res.title == "Потери и компенсации на складах WB"
    assert "нет" in res.summary.lower()


def test_frozen_capital_normal(monkeypatch):
    fin_products_df = pd.DataFrame([
        {
            "Артикул продавца": "SKU-SLOW-1",
            "Артикул WB": 101,
            "Остаток": 20,
            "Себестоимость ед.": 500.0,
            "Запас, дней": 75,
        },
        {
            "Артикул продавца": "SKU-FAST-1",
            "Артикул WB": 102,
            "Остаток": 100,
            "Себестоимость ед.": 300.0,
            "Запас, дней": 15,
        },
    ])

    monkeypatch.setattr(metrics, "build_dashboard", lambda start, end: DummyDashboard(financial_products=fin_products_df))

    res = metrics.build_metric("frozen_capital", START_DATE, END_DATE)
    assert res.title == "Замороженный капитал в неликвидных остатках"
    assert res.figure is not None
    assert "Неликвидных SKU" in res.summary
    assert "10 000₽" in res.summary


def test_frozen_capital_empty(monkeypatch):
    fin_products_df = pd.DataFrame([
        {
            "Артикул продавца": "SKU-FAST-1",
            "Артикул WB": 102,
            "Остаток": 100,
            "Себестоимость ед.": 300.0,
            "Запас, дней": 30,
        },
    ])

    monkeypatch.setattr(metrics, "build_dashboard", lambda start, end: DummyDashboard(financial_products=fin_products_df))

    res = metrics.build_metric("frozen_capital", START_DATE, END_DATE)
    assert res.title == "Замороженный капитал в неликвидных остатках"
    assert "не обнаружено" in res.summary.lower()


def test_roi_by_sku_normal(monkeypatch):
    fin_products_df = pd.DataFrame([
        {
            "Артикул продавца": "SKU-A",
            "Артикул WB": 201,
            "Рентабельность затрат, %": 45.5,
            "Продано нетто": 50,
        },
        {
            "Артикул продавца": "SKU-B",
            "Артикул WB": 202,
            "Рентабельность затрат, %": -12.0,
            "Продано нетто": 10,
        },
    ])

    monkeypatch.setattr(metrics, "build_dashboard", lambda start, end: DummyDashboard(financial_products=fin_products_df))

    res = metrics.build_metric("roi_by_sku", START_DATE, END_DATE)
    assert res.title == "Рентабельность инвестиций по товарам (ROI)"
    assert res.figure is not None
    assert "Средний ROI%" in res.summary
    assert "SKU-A" in res.summary
    assert "45.5%" in res.summary


def test_roi_by_sku_empty(monkeypatch):
    monkeypatch.setattr(metrics, "build_dashboard", lambda start, end: DummyDashboard(financial_products=pd.DataFrame()))

    res = metrics.build_metric("roi_by_sku", START_DATE, END_DATE)
    assert res.title == "Рентабельность инвестиций по товарам (ROI)"
    assert "нет" in res.summary.lower()
