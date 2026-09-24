from __future__ import annotations

import math
import unittest

import pandas as pd

from db.historical_cogs import project_historical_cogs


def row(rrd_id: int, nm_id: int, kind: str, units: int, status: str,
        exact: float | None = None, baseline: float = 0.0) -> dict:
    return {
        "rrd_id": rrd_id, "nm_id": nm_id, "event_type": kind, "units": units,
        "status": status, "eligible_fifo_cogs_rub": exact,
        "baseline_cogs_rub": baseline, "reason": "test",
    }


class HistoricalCogsProjectionTests(unittest.TestCase):
    def test_exact_multilayer_sale_uses_signed_fifo_cost_without_repricing(self) -> None:
        detail, by_sku, store = project_historical_cogs(pd.DataFrame([
            row(1, 10, "sale", 2, "exact", 100.0, 500.0),
        ]))
        self.assertEqual(detail.loc[0, "cogs_source"], "exact_fifo")
        self.assertEqual(detail.loc[0, "cogs_rub"], 100.0)
        self.assertEqual(by_sku.loc[0, "exact_fifo_cogs_rub"], 100.0)
        self.assertEqual(store["cogs_rub"], 100.0)
        self.assertEqual(store["exact_unit_coverage_percent"], 100.0)

    def test_exact_return_is_negative_in_its_operation_period(self) -> None:
        detail, _, store = project_historical_cogs(pd.DataFrame([
            row(2, 10, "return", 1, "exact", -40.0, -99.0),
        ]))
        self.assertEqual(detail.loc[0, "cogs_rub"], -40.0)
        self.assertEqual(store["cogs_rub"], -40.0)

    def test_mixed_exact_and_current_estimate_for_same_sku(self) -> None:
        detail, by_sku, store = project_historical_cogs(pd.DataFrame([
            row(1, 10, "sale", 1, "exact", 100.0, 500.0),
            row(2, 10, "sale", 1, "ambiguous", None, 30.0),
        ]))
        self.assertEqual(list(detail["cogs_source"]), ["exact_fifo", "current_cost_estimate"])
        self.assertEqual(by_sku.loc[0, "cogs_rub"], 130.0)
        self.assertEqual(by_sku.loc[0, "exact_fifo_cogs_rub"], 100.0)
        self.assertEqual(by_sku.loc[0, "current_cost_estimate_rub"], 30.0)
        self.assertEqual(by_sku.loc[0, "cogs_quality"], "mixed")
        self.assertEqual(store["exact_unit_coverage_percent"], 50.0)

    def test_duplicate_rrd_id_fails_closed_and_keeps_both_rows(self) -> None:
        detail, _, store = project_historical_cogs(pd.DataFrame([
            row(1, 10, "sale", 1, "exact", 10.0, 20.0),
            row(1, 11, "sale", 1, "exact", 10.0, 20.0),
        ]))
        self.assertEqual(len(detail), 2)
        self.assertTrue((detail["cogs_source"] == "insufficient").all())
        self.assertEqual(store["insufficient_units"], 2)
        self.assertEqual(store["cogs_quality"], "insufficient")

    def test_zero_or_absent_baseline_is_insufficient_not_free(self) -> None:
        detail, _, store = project_historical_cogs(pd.DataFrame([
            row(1, 10, "sale", 2, "unmatched", None, 0.0),
            row(2, 11, "return", 1, "unmatched", None, float("nan")),
        ]))
        self.assertTrue((detail["cogs_source"] == "insufficient").all())
        self.assertEqual(detail["cogs_rub"].sum(), 0.0)
        self.assertEqual(store["insufficient_units"], 3)
        self.assertFalse(store["cogs_complete"])

    def test_fallback_return_reverses_only_positive_baseline(self) -> None:
        detail, _, _ = project_historical_cogs(pd.DataFrame([
            row(1, 10, "return", 2, "synthetic", None, -60.0),
        ]))
        self.assertEqual(detail.loc[0, "cogs_source"], "current_cost_estimate")
        self.assertEqual(detail.loc[0, "cogs_rub"], -60.0)

    def test_non_sale_and_nm_zero_are_preserved_but_only_events_affect_cogs(self) -> None:
        detail, by_sku, store = project_historical_cogs(pd.DataFrame([
            row(1, 0, "sale", 1, "unmatched", None, 12.0),
            row(2, 10, "", 9, "excluded", None, 0.0),
        ]))
        self.assertEqual(len(detail), 2)
        self.assertEqual(detail.iloc[1]["cogs_source"], "excluded")
        self.assertEqual(store["cogs_rub"], 12.0)
        self.assertEqual(store["sale_return_units"], 1)
        self.assertIn(0, set(by_sku["nm_id"]))

    def test_missing_or_nonfinite_ids_and_baselines_fail_closed(self) -> None:
        detail, _, store = project_historical_cogs(pd.DataFrame([
            row(0, 10, "sale", 1, "exact", 5.0, 5.0),
            row(2, 11, "sale", 1, "unmatched", None, math.inf),
        ]))
        self.assertTrue((detail["cogs_source"] == "insufficient").all())
        self.assertEqual(store["insufficient_units"], 2)

    def test_empty_projection_has_zero_coverage(self) -> None:
        detail, by_sku, store = project_historical_cogs(pd.DataFrame())
        self.assertTrue(detail.empty)
        self.assertTrue(by_sku.empty)
        self.assertEqual(store["cogs_quality"], "no_sales")
        self.assertEqual(store["exact_unit_coverage_percent"], 0.0)


if __name__ == "__main__":
    unittest.main()
