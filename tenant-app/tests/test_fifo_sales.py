"""Unit tests for db/fifo_sales.py -- turns raw WB sale/return rows into
exact per-unit FIFO cost of goods sold. This is the sharpest edge of the
whole money-logic stack: a bug here means every P&L number downstream is
wrong, silently.

Like tests/test_fifo_finished_goods.py, this drives the real (private)
functions directly against a throwaway sqlite db -- no network, no
framework dependency, runs under plain stdlib unittest.
"""
from __future__ import annotations

import json

from db.core import connect
from db.fifo_finished_goods import _create_finished_goods_layer, _finished_layer_totals
from db.fifo_sales import (
    initialize_sales_fifo_tracking,
    process_sales_fifo_events,
    sales_fifo_tracking_status,
)

from .base import DbTestCase

NM_ID = 800001


class SalesFifoTestCase(DbTestCase):
    def _insert_sale(
        self, conn, sale_record_id: str, *, srid: str = "", is_return: int = 0,
        sale_date: str = "2026-01-01T10:00:00", nm_id: int = NM_ID, chrt_id: int = 0,
    ) -> None:
        conn.execute(
            """
            INSERT INTO sales(id,sale_date,last_change,nm_id,supplier_article,is_return,raw_json)
            VALUES (?,?,?,?,?,?,?)
            """,
            (sale_record_id, sale_date, sale_date, nm_id, "ART-1", is_return,
             json.dumps(({"srid": srid} if srid else {}) | ({"chrtID": chrt_id} if chrt_id else {}))),
        )


class InitializeSalesFifoTrackingTests(SalesFifoTestCase):
    def test_initialize_with_no_sales_marks_initialized_with_zero_baseline(self) -> None:
        result = initialize_sales_fifo_tracking()
        self.assertTrue(result["initialized"])
        self.assertEqual(result["baseline"], 0)
        status = sales_fifo_tracking_status()
        self.assertTrue(status["initialized"])
        self.assertEqual(status["total"], 0)

    def test_initialize_baselines_sales_that_existed_before_tracking_started(self) -> None:
        with connect() as conn:
            self._insert_sale(conn, "S1")
            self._insert_sale(conn, "S2")
            self._insert_sale(conn, "S3", is_return=1)
        result = initialize_sales_fifo_tracking()
        self.assertEqual(result["baseline"], 3)
        status = sales_fifo_tracking_status()
        self.assertEqual(status["baseline_rows"], 3)
        self.assertEqual(status["sales_applied"], 0)  # baseline rows are NOT "applied" FIFO events
        with connect() as conn:
            statuses = {row["status"] for row in conn.execute("SELECT status FROM sales_fifo_events").fetchall()}
        self.assertEqual(statuses, {"baseline"})

    def test_initialize_is_idempotent(self) -> None:
        with connect() as conn:
            self._insert_sale(conn, "S1")
        first = initialize_sales_fifo_tracking()
        second = initialize_sales_fifo_tracking()
        self.assertEqual(first["baseline"], 1)
        self.assertEqual(second["baseline"], 0)  # already initialized -- second call is a pure no-op
        with connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM sales_fifo_events").fetchone()["n"]
        self.assertEqual(count, 1)  # not duplicated


class ProcessSalesFifoWithoutInitializationTests(SalesFifoTestCase):
    def test_processing_before_initialization_is_a_safe_noop(self) -> None:
        with connect() as conn:
            self._insert_sale(conn, "S1")
        result = process_sales_fifo_events()
        self.assertFalse(result["initialized"])
        self.assertEqual(result["processed"], 0)


class ProcessSalesFifoNewSaleTests(SalesFifoTestCase):
    def test_sale_consumes_only_its_characteristic_and_persists_it(self) -> None:
        initialize_sales_fifo_tracking()
        with connect() as conn:
            first = _create_finished_goods_layer(
                conn, NM_ID, "ART-1", "Тест S", "opening", "variant:11", "2026-01-01", 1, 110.0, "wb", chrt_id=11
            )
            _create_finished_goods_layer(
                conn, NM_ID, "ART-1", "Тест M", "opening", "variant:22", "2026-01-01", 1, 220.0, "wb", chrt_id=22
            )
            self._insert_sale(conn, "S-11", srid="SR-V", chrt_id=11)
        result = process_sales_fifo_events()
        self.assertEqual(result["errors"], 0)
        with connect() as conn:
            event = conn.execute("SELECT * FROM sales_fifo_events WHERE sale_record_id='S-11'").fetchone()
            movement = conn.execute("SELECT * FROM inventory_movements WHERE id=?", (event["movement_id"],)).fetchone()
            allocation = conn.execute(
                "SELECT layer_id FROM finished_goods_fifo_allocations WHERE movement_id=? AND status='applied'",
                (event["movement_id"],),
            ).fetchone()
            left = conn.execute("SELECT SUM(wb_units) AS n FROM finished_goods_cost_layers WHERE chrt_id=22").fetchone()["n"]
        self.assertEqual(event["chrt_id"], 11)
        self.assertEqual(movement["chrt_id"], 11)
        self.assertEqual(allocation["layer_id"], first)
        self.assertEqual(left, 1)

    def test_new_sale_consumes_oldest_fifo_layer_and_records_exact_cogs(self) -> None:
        initialize_sales_fifo_tracking()  # no sales yet -> starts tracking from here
        with connect() as conn:
            _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:1", "2026-01-01", 10, 100.0, "wb")
            self._insert_sale(conn, "S1", srid="SR1")
        result = process_sales_fifo_events()
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["sales"], 1)
        self.assertEqual(result["errors"], 0)
        with connect() as conn:
            event = conn.execute("SELECT * FROM sales_fifo_events WHERE sale_record_id='S1'").fetchone()
            units, _ = _finished_layer_totals(conn, NM_ID, "wb")
        self.assertEqual(event["status"], "applied")
        self.assertEqual(event["event_type"], "sale")
        self.assertAlmostEqual(event["fifo_cost_rub"], 100.0, places=2)
        self.assertEqual(units, 9)  # 10 - 1

    def test_new_sale_without_any_fifo_stock_creates_synthetic_layer_at_baseline_cost(self) -> None:
        initialize_sales_fifo_tracking()
        with connect() as conn:
            conn.execute("INSERT INTO costs(nm_id,cost_per_wb_unit) VALUES (?,55.0)", (NM_ID,))
            self._insert_sale(conn, "S1")
        result = process_sales_fifo_events()
        self.assertEqual(result["errors"], 0)
        with connect() as conn:
            event = conn.execute("SELECT * FROM sales_fifo_events WHERE sale_record_id='S1'").fetchone()
            synthetic = conn.execute(
                "SELECT * FROM finished_goods_cost_layers WHERE nm_id=? AND source_type='synthetic_sale'", (NM_ID,)
            ).fetchone()
        self.assertAlmostEqual(event["fifo_cost_rub"], 55.0, places=2)
        self.assertIn("временный слой", event["note"])
        self.assertIsNotNone(synthetic)

    def test_reprocessing_does_not_double_charge_already_applied_sales(self) -> None:
        initialize_sales_fifo_tracking()
        with connect() as conn:
            _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:1", "2026-01-01", 10, 100.0, "wb")
            self._insert_sale(conn, "S1")
        first = process_sales_fifo_events()
        second = process_sales_fifo_events()
        self.assertEqual(first["processed"], 1)
        self.assertEqual(second["processed"], 0)
        with connect() as conn:
            units, _ = _finished_layer_totals(conn, NM_ID, "wb")
        self.assertEqual(units, 9)  # only charged once, not twice

    def test_only_new_untracked_sales_are_picked_up_in_one_run(self) -> None:
        initialize_sales_fifo_tracking()
        with connect() as conn:
            _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:1", "2026-01-01", 10, 100.0, "wb")
            self._insert_sale(conn, "S1", sale_date="2026-02-01T10:00:00")
            self._insert_sale(conn, "S2", sale_date="2026-02-02T10:00:00")
        result = process_sales_fifo_events()
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["sales"], 2)


class ProcessSalesFifoReturnTests(SalesFifoTestCase):
    def test_variant_return_matches_same_variant_and_restores_its_original_layer(self) -> None:
        initialize_sales_fifo_tracking()
        with connect() as conn:
            variant_layer = _create_finished_goods_layer(
                conn, NM_ID, "ART-1", "Тест S", "opening", "return-variant:11", "2026-01-01", 1, 110.0, "wb", chrt_id=11
            )
            _create_finished_goods_layer(
                conn, NM_ID, "ART-1", "Тест M", "opening", "return-variant:22", "2026-01-01", 1, 220.0, "wb", chrt_id=22
            )
            self._insert_sale(conn, "S-11", srid="SR-V", chrt_id=11)
        process_sales_fifo_events()
        with connect() as conn:
            self._insert_sale(conn, "R-11", srid="SR-V", is_return=1, chrt_id=11)
        result = process_sales_fifo_events()
        self.assertEqual(result["errors"], 0)
        with connect() as conn:
            sale = conn.execute("SELECT * FROM sales_fifo_events WHERE sale_record_id='S-11'").fetchone()
            ret = conn.execute("SELECT * FROM sales_fifo_events WHERE sale_record_id='R-11'").fetchone()
            restored = conn.execute("SELECT wb_units FROM finished_goods_cost_layers WHERE id=?", (variant_layer,)).fetchone()["wb_units"]
            other = conn.execute("SELECT wb_units FROM finished_goods_cost_layers WHERE chrt_id=22").fetchone()["wb_units"]
        self.assertEqual(ret["matched_sale_event_id"], sale["id"])
        self.assertEqual(ret["chrt_id"], 11)
        self.assertEqual(restored, 1)
        self.assertEqual(other, 1)

    def test_variant_return_does_not_match_another_known_variant(self) -> None:
        initialize_sales_fifo_tracking()
        with connect() as conn:
            _create_finished_goods_layer(
                conn, NM_ID, "ART-1", "Тест M", "opening", "mismatch-variant:22", "2026-01-01", 1, 220.0, "wb", chrt_id=22
            )
            self._insert_sale(conn, "S-22", srid="SR-V", chrt_id=22)
        process_sales_fifo_events()
        with connect() as conn:
            self._insert_sale(conn, "R-11", srid="SR-V", is_return=1, chrt_id=11)
        process_sales_fifo_events()
        with connect() as conn:
            ret = conn.execute("SELECT * FROM sales_fifo_events WHERE sale_record_id='R-11'").fetchone()
            sale = conn.execute("SELECT * FROM sales_fifo_events WHERE sale_record_id='S-22'").fetchone()
        self.assertIsNone(ret["matched_sale_event_id"])
        self.assertEqual(sale["chrt_id"], 22)

    def test_variant_return_does_not_match_legacy_nm_only_sale(self) -> None:
        initialize_sales_fifo_tracking()
        with connect() as conn:
            _create_finished_goods_layer(
                conn, NM_ID, "ART-1", "Legacy", "opening", "legacy-return", "2026-01-01", 1, 80.0, "wb", chrt_id=0
            )
            self._insert_sale(conn, "S-legacy", srid="SR-LEGACY", chrt_id=0)
        process_sales_fifo_events()
        with connect() as conn:
            self._insert_sale(conn, "R-variant", srid="SR-LEGACY", is_return=1, chrt_id=11)
        process_sales_fifo_events()
        with connect() as conn:
            ret = conn.execute("SELECT * FROM sales_fifo_events WHERE sale_record_id='R-variant'").fetchone()
        self.assertIsNone(ret["matched_sale_event_id"])

    def test_return_restores_cost_into_the_original_layer(self) -> None:
        initialize_sales_fifo_tracking()
        with connect() as conn:
            _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:1", "2026-01-01", 5, 80.0, "wb")
            self._insert_sale(conn, "S1", srid="SR1", sale_date="2026-02-01T10:00:00")
        process_sales_fifo_events()
        with connect() as conn:
            units_after_sale, _ = _finished_layer_totals(conn, NM_ID, "wb")
            self._insert_sale(conn, "R1", srid="SR1", is_return=1, sale_date="2026-02-05T10:00:00")
        result = process_sales_fifo_events()
        self.assertEqual(result["returns"], 1)
        self.assertEqual(result["errors"], 0)
        with connect() as conn:
            return_event = conn.execute("SELECT * FROM sales_fifo_events WHERE sale_record_id='R1'").fetchone()
            sale_event = conn.execute("SELECT * FROM sales_fifo_events WHERE sale_record_id='S1'").fetchone()
            units_after_return, _ = _finished_layer_totals(conn, NM_ID, "wb")
        self.assertEqual(units_after_sale, 4)
        self.assertEqual(units_after_return, 5)  # fully restored
        self.assertAlmostEqual(return_event["fifo_cost_rub"], 80.0, places=2)
        self.assertEqual(return_event["matched_sale_event_id"], sale_event["id"])

    def test_return_without_matching_sale_uses_baseline_rate_and_flags_unmatched(self) -> None:
        initialize_sales_fifo_tracking()
        with connect() as conn:
            conn.execute("INSERT INTO costs(nm_id,cost_per_wb_unit) VALUES (?,33.0)", (NM_ID,))
            self._insert_sale(conn, "R1", srid="NO-MATCH", is_return=1)
        result = process_sales_fifo_events()
        self.assertEqual(result["returns"], 1)
        with connect() as conn:
            event = conn.execute("SELECT * FROM sales_fifo_events WHERE sale_record_id='R1'").fetchone()
        self.assertAlmostEqual(event["fifo_cost_rub"], 33.0, places=2)
        self.assertIsNone(event["matched_sale_event_id"])
        self.assertIn("не удалось полностью сопоставить", event["note"])

    def test_second_return_for_an_already_returned_sale_does_not_match_again(self) -> None:
        """A sale can only be returned once -- _find_matching_sale_fifo_event
        excludes sale events that already have an applied return. A second
        return with the same srid must fall back to the unmatched path
        rather than double-crediting the same original layer."""
        initialize_sales_fifo_tracking()
        with connect() as conn:
            _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:1", "2026-01-01", 5, 80.0, "wb")
            self._insert_sale(conn, "S1", srid="SR1", sale_date="2026-02-01T10:00:00")
        process_sales_fifo_events()
        with connect() as conn:
            self._insert_sale(conn, "R1", srid="SR1", is_return=1, sale_date="2026-02-05T10:00:00")
        process_sales_fifo_events()
        with connect() as conn:
            self._insert_sale(conn, "R2", srid="SR1", is_return=1, sale_date="2026-02-06T10:00:00")
        process_sales_fifo_events()
        with connect() as conn:
            second_return = conn.execute("SELECT * FROM sales_fifo_events WHERE sale_record_id='R2'").fetchone()
            units, _ = _finished_layer_totals(conn, NM_ID, "wb")
        self.assertIsNone(second_return["matched_sale_event_id"])
        self.assertEqual(units, 5 + 1)  # original 5, +1 synthetic layer from the unmatched second return


if __name__ == "__main__":
    import unittest
    unittest.main()
