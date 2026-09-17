from datetime import date

import pandas as pd

from overview_service import compute_overview


class _Dashboard:
    kpi = {
        "orders": 10,
        "order_amount": 2000.0,
        "sales": 8,
        "buyout": 80.0,
        "returns": 1,
        "revenue": 900.0,
        "ad_spend": 50.0,
        "drr": 5.0,
        "stock": 12,
    }
    financial = {"profit": 500.0, "operational_buyout_amount": 1000.0}
    daily = pd.DataFrame()
    hourly = pd.DataFrame()
    products = pd.DataFrame()
    financial_products = pd.DataFrame()
    alerts = pd.DataFrame()


def test_finance_kpis_include_both_margin_denominators():
    result = compute_overview(
        {
            "data": _Dashboard(),
            "period": "30d",
            "period_label": "30 дней",
            "start": date(2026, 9, 1),
            "end": date(2026, 9, 30),
        }
    )
    kpis = {item["key"]: item for item in result["kpis"]}

    assert kpis["net_margin_pct"]["value"] == "50.0%"
    assert kpis["orders_profitability_pct"]["value"] == "25.0%"
    assert "все расходы" in kpis["net_margin_pct"]["note"]
    assert "сумма заказов" in kpis["orders_profitability_pct"]["note"]
