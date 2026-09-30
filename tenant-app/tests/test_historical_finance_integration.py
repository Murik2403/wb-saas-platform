from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd

from calculations import build_dashboard
from tests.base import DbTestCase


class HistoricalFinanceIntegrationTests(DbTestCase):
    def test_dashboard_replaces_legacy_cogs_for_store_and_sku_once(self) -> None:
        finance = pd.DataFrame([
            {"rrd_id": 101, "operation_date": "2026-09-10", "nm_id": 123, "quantity": 1,
             "retail_amount": 1000.0, "retail_price_withdisc_rub": 1000.0, "ppvz_for_pay": 900.0,
             "supplier_article": "A", "operation_name": "Продажа", "doc_type_name": "Продажа"},
            {"rrd_id": 102, "operation_date": "2026-09-11", "nm_id": 123, "quantity": 1,
             "retail_amount": 1000.0, "retail_price_withdisc_rub": 1000.0, "ppvz_for_pay": 900.0,
             "supplier_article": "A", "operation_name": "Продажа", "doc_type_name": "Продажа"},
            {"rrd_id": 103, "operation_date": "2026-09-12", "nm_id": 123, "quantity": -1,
             "retail_amount": -1000.0, "retail_price_withdisc_rub": 1000.0, "ppvz_for_pay": -900.0,
             "supplier_article": "A", "operation_name": "Возврат", "doc_type_name": "Возврат"},
        ])
        reconciliation = pd.DataFrame([
            {"rrd_id": 101, "nm_id": 123, "event_type": "sale", "units": 1, "status": "exact",
             "eligible_fifo_cogs_rub": 120.0, "baseline_cogs_rub": 999.0, "reason": ""},
            {"rrd_id": 102, "nm_id": 123, "event_type": "sale", "units": 1, "status": "ambiguous",
             "eligible_fifo_cogs_rub": None, "baseline_cogs_rub": 20.0, "reason": "ambiguous"},
            {"rrd_id": 103, "nm_id": 123, "event_type": "return", "units": 1, "status": "exact",
             "eligible_fifo_cogs_rub": -35.0, "baseline_cogs_rub": -999.0, "reason": ""},
        ])
        empty = pd.DataFrame()
        tables = {
            "orders": empty.copy(), "sales": empty.copy(), "ads_daily": empty.copy(),
            "stocks": empty.copy(), "costs": pd.DataFrame([{"nm_id": 123, "cost_per_wb_unit": 999.0}]),
            "products_catalog": pd.DataFrame([{"nm_id": 123, "supplier_article": "A", "product_name": "Item"}]),
        }
        with patch("calculations.read_table", side_effect=lambda name, **kwargs: tables[name].copy()), \
             patch("calculations.read_financial", return_value=finance), \
             patch("calculations.read_financial_fifo_reconciliation", return_value=reconciliation):
            result = build_dashboard(date(2026, 9, 10), date(2026, 9, 12))

        self.assertEqual(result.financial["cost"], 105.0)
        self.assertEqual(result.financial["profit"], 795.0)
        self.assertEqual(result.financial["exact_fifo_cogs_rub"], 85.0)
        self.assertEqual(result.financial["current_cost_estimate_rub"], 20.0)
        self.assertEqual(result.financial["exact_unit_coverage_percent"], 66.67)
        self.assertEqual(result.financial["cogs_quality"], "mixed")
        product = result.financial_products.iloc[0]
        self.assertEqual(product["Себестоимость"], 105.0)
        self.assertEqual(product["Маржинальная прибыль"], 795.0)


if __name__ == "__main__":
    import unittest
    unittest.main()
