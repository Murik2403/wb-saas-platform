"""Unit tests for db/wb_fifo_reconciliation.py -- the "sync FIFO layers to
confirmed physical balances" workflow.

Only covers the parts of this module that actually move money/units
(preview + apply_finished_goods_reconciliation + status). The rest of the
module (read_wb_incident_reconciliation, finished_goods_fifo_guard_status)
is diagnostic-only reporting with no side effects -- lower stakes, and
mostly re-derives numbers already exercised by test_fifo_finished_goods.py
and test_fifo_sales.py, so it's left for a future pass.

The single most important invariant pinned down here (v5.9 in the source
comments): WB-location differences are NEVER safe to auto-reconcile --
only ready/inbound (locally-confirmed) balances are. Getting this wrong
would let a WB stock discrepancy (which can be in-transit, an aggregated
snapshot, or a real warehouse incident) silently consume FIFO cost as if
it were an ordinary local stocktake correction.
"""
from __future__ import annotations

from db.core import connect
from db.fifo_finished_goods import _create_finished_goods_layer, _finished_layer_totals
from db.wb_fifo_reconciliation import (
    apply_finished_goods_reconciliation,
    fifo_reconciliation_status,
    preview_finished_goods_reconciliation,
)

from .base import DbTestCase

NM_ID = 900001


class PreviewReconciliationTests(DbTestCase):
    def test_positive_ready_diff_is_flagged_safe_and_wants_a_new_layer(self) -> None:
        with connect() as conn:
            conn.execute(
                "INSERT INTO product_pipeline(nm_id,local_known,ready_units,inbound_known,inbound_units) VALUES (?,1,8,0,0)",
                (NM_ID,),
            )
        preview = preview_finished_goods_reconciliation()
        row = preview[(preview["nm_id"] == NM_ID) & (preview["location"] == "ready")].iloc[0]
        self.assertEqual(row["difference_units"], 8)
        self.assertEqual(row["safe_to_reconcile"], 1)
        self.assertEqual(row["action"], "Досоздать слой")

    def test_negative_inbound_diff_is_flagged_safe_and_wants_a_writeoff(self) -> None:
        with connect() as conn:
            _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:1", "2026-01-01", 5, 40.0, "inbound")
            conn.execute(
                "INSERT INTO product_pipeline(nm_id,local_known,ready_units,inbound_known,inbound_units) VALUES (?,0,0,1,2)",
                (NM_ID,),
            )
        preview = preview_finished_goods_reconciliation()
        row = preview[(preview["nm_id"] == NM_ID) & (preview["location"] == "inbound")].iloc[0]
        self.assertEqual(row["difference_units"], -3)
        self.assertEqual(row["safe_to_reconcile"], 1)
        self.assertEqual(row["action"], "Списать из слоёв")

    def test_wb_shortfall_is_never_safe_to_reconcile(self) -> None:
        with connect() as conn:
            _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:1", "2026-01-01", 10, 40.0, "wb")
            self.set_stock_snapshot(conn, NM_ID, quantity=5)
        preview = preview_finished_goods_reconciliation()
        row = preview[(preview["nm_id"] == NM_ID) & (preview["location"] == "wb")].iloc[0]
        self.assertEqual(row["difference_units"], -5)
        self.assertEqual(row["safe_to_reconcile"], 0)
        self.assertIn("утрат", row["action"])

    def test_wb_surplus_is_also_never_safe_to_reconcile(self) -> None:
        with connect() as conn:
            _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:1", "2026-01-01", 3, 40.0, "wb")
            self.set_stock_snapshot(conn, NM_ID, quantity=9)
        preview = preview_finished_goods_reconciliation()
        row = preview[(preview["nm_id"] == NM_ID) & (preview["location"] == "wb")].iloc[0]
        self.assertEqual(row["difference_units"], 6)
        self.assertEqual(row["safe_to_reconcile"], 0)

    def test_matching_physical_and_layered_units_produce_no_row(self) -> None:
        with connect() as conn:
            _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:1", "2026-01-01", 5, 40.0, "ready")
            conn.execute(
                "INSERT INTO product_pipeline(nm_id,local_known,ready_units,inbound_known,inbound_units) VALUES (?,1,5,0,0)",
                (NM_ID,),
            )
        preview = preview_finished_goods_reconciliation()
        # No difference anywhere for this (only) product -- preview has no rows at all.
        self.assertTrue(preview.empty)

    def test_unknown_physical_balance_is_excluded_entirely(self) -> None:
        with connect() as conn:
            conn.execute(
                "INSERT INTO product_pipeline(nm_id,local_known,ready_units,inbound_known,inbound_units) VALUES (?,0,999,0,0)",
                (NM_ID,),
            )
        preview = preview_finished_goods_reconciliation()
        # local_known=0 -- physical balance not confirmed, so it must never surface
        # as a reconcilable difference no matter how large ready_units looks.
        self.assertTrue(preview.empty)


class ApplyReconciliationTests(DbTestCase):
    def test_apply_is_noop_on_empty_db(self) -> None:
        result = apply_finished_goods_reconciliation()
        self.assertEqual(result["run_id"], 0)
        self.assertEqual(result["lines"], 0)

    def test_apply_creates_layer_for_positive_ready_diff(self) -> None:
        with connect() as conn:
            conn.execute("INSERT INTO costs(nm_id,cost_per_wb_unit) VALUES (?,25.0)", (NM_ID,))
            conn.execute(
                "INSERT INTO product_pipeline(nm_id,local_known,ready_units,inbound_known,inbound_units) VALUES (?,1,8,0,0)",
                (NM_ID,),
            )
        result = apply_finished_goods_reconciliation(note="test run")
        self.assertGreater(result["run_id"], 0)
        self.assertEqual(result["added_units"], 8)
        self.assertEqual(result["removed_units"], 0)
        with connect() as conn:
            units, amount = _finished_layer_totals(conn, NM_ID, "ready")
            run = conn.execute("SELECT * FROM fifo_reconciliation_runs WHERE id=?", (result["run_id"],)).fetchone()
            lines = conn.execute("SELECT * FROM fifo_reconciliation_lines WHERE run_id=?", (result["run_id"],)).fetchall()
        self.assertEqual(units, 8)
        self.assertAlmostEqual(amount, 8 * 25.0, places=2)
        self.assertEqual(run["status"], "applied")
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["action"], "Досоздать слой")

    def test_apply_writes_off_fifo_for_negative_inbound_diff(self) -> None:
        with connect() as conn:
            _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:1", "2026-01-01", 5, 40.0, "inbound")
            conn.execute(
                "INSERT INTO product_pipeline(nm_id,local_known,ready_units,inbound_known,inbound_units) VALUES (?,0,0,1,2)",
                (NM_ID,),
            )
        result = apply_finished_goods_reconciliation()
        self.assertEqual(result["removed_units"], 3)
        with connect() as conn:
            units, _ = _finished_layer_totals(conn, NM_ID, "inbound")
            movement = conn.execute(
                "SELECT * FROM inventory_movements WHERE movement_type='fifo_cost_reconciliation' AND nm_id=?", (NM_ID,)
            ).fetchone()
        self.assertEqual(units, 2)
        self.assertIsNotNone(movement)
        self.assertAlmostEqual(movement["goods_cost_rub"], 3 * 40.0, places=2)

    def test_apply_never_touches_wb_diffs_and_counts_them_as_skipped(self) -> None:
        with connect() as conn:
            _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:1", "2026-01-01", 10, 40.0, "wb")
            self.set_stock_snapshot(conn, NM_ID, quantity=5)
        result = apply_finished_goods_reconciliation()
        self.assertEqual(result["lines"], 0)  # nothing safe was applied
        self.assertEqual(result["skipped_lines"], 1)
        self.assertEqual(result["skipped_units"], 5)
        with connect() as conn:
            units, _ = _finished_layer_totals(conn, NM_ID, "wb")
        self.assertEqual(units, 10)  # completely untouched

    def test_apply_handles_mixed_safe_and_blocked_rows_in_one_run(self) -> None:
        with connect() as conn:
            conn.execute("INSERT INTO costs(nm_id,cost_per_wb_unit) VALUES (?,10.0)", (NM_ID,))
            conn.execute(
                "INSERT INTO product_pipeline(nm_id,local_known,ready_units,inbound_known,inbound_units) VALUES (?,1,4,0,0)",
                (NM_ID,),
            )
            _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:wb", "2026-01-01", 10, 40.0, "wb")
            self.set_stock_snapshot(conn, NM_ID, quantity=5)
        result = apply_finished_goods_reconciliation()
        self.assertEqual(result["lines"], 1)  # only the ready diff applied
        self.assertEqual(result["added_units"], 4)
        self.assertEqual(result["skipped_lines"], 1)  # the wb diff blocked
        self.assertEqual(result["skipped_units"], 5)
        with connect() as conn:
            wb_units, _ = _finished_layer_totals(conn, NM_ID, "wb")
        self.assertEqual(wb_units, 10)  # wb layer untouched despite the same run


class ReconciliationStatusTests(DbTestCase):
    def test_status_reflects_totals_after_a_run(self) -> None:
        with connect() as conn:
            conn.execute("INSERT INTO costs(nm_id,cost_per_wb_unit) VALUES (?,15.0)", (NM_ID,))
            conn.execute(
                "INSERT INTO product_pipeline(nm_id,local_known,ready_units,inbound_known,inbound_units) VALUES (?,1,6,0,0)",
                (NM_ID,),
            )
        apply_finished_goods_reconciliation()
        status = fifo_reconciliation_status()
        self.assertEqual(status["runs"], 1)
        self.assertEqual(status["added_units"], 6)
        self.assertEqual(status["removed_units"], 0)
        self.assertGreater(status["last_run_id"], 0)

    def test_status_on_empty_db_reports_zero_without_crashing(self) -> None:
        status = fifo_reconciliation_status()
        self.assertEqual(status["runs"], 0)
        self.assertEqual(status["last_run_id"], 0)


if __name__ == "__main__":
    import unittest
    unittest.main()
