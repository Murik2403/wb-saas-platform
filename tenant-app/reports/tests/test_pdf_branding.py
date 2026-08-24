"""Tests for PDF report branding styling and layout structure."""
from datetime import date
import inspect
import pandas as pd
import pytest

from reports import metrics, pdf_builder


class DummyDashboard:
    """Mock dashboard object for headless metric generation in tests."""

    def __init__(self):
        dates = pd.date_range("2024-01-01", "2024-01-05")
        self.daily = pd.DataFrame(
            {
                "day": dates,
                "orders": [10, 20, 15, 30, 25],
                "sales": [8, 18, 12, 28, 22],
                "ad_spend": [1000, 1500, 1200, 2000, 1800],
                "drr": [10.0, 12.0, 11.0, 9.0, 10.5],
            }
        )
        self.financial = {
            "sales": 100000.0,
            "wb_expenses": 20000.0,
            "cost": 40000.0,
            "financial_ad_spend": 7500.0,
            "profit": 32500.0,
        }
        self.financial_products = pd.DataFrame()
        self.ads = pd.DataFrame()
        self.stocks = pd.DataFrame()


def test_build_report_pdf_returns_valid_pdf_bytes(monkeypatch):
    monkeypatch.setattr(metrics, "build_dashboard", lambda start, end: DummyDashboard())
    monkeypatch.setattr(metrics, "read_table", lambda table_name: pd.DataFrame())

    pdf_bytes = pdf_builder.build_report_pdf(
        "Тестовый сводный отчёт",
        ["sales_orders", "ads", "returns_cancellations"],
        date(2024, 1, 1),
        date(2024, 1, 5),
    )

    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000


def test_keeptogether_used_for_metric_sections():
    source = inspect.getsource(pdf_builder)
    assert "KeepTogether" in source
    assert "KeepTogether(metric_story)" in source
