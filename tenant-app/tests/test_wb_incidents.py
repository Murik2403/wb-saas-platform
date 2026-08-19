"""Unit tests for db/wb_incidents.py -- warehouse-loss write-offs.

apply_wb_incident_loss() is one of the highest-stakes functions in the app:
it recognises a permanent inventory loss and consumes finished-goods FIFO
cost outside normal sale COGS. It carries a deliberate "v6.1 hard safety
barrier" that refuses to let a write-off push FIFO below the latest known
WB stock contour. That guard is exactly the kind of money-safety logic
README_REFACTOR.md asks to have tests before moving further -- these tests
pin its behaviour down.
"""
from __future__ import annotations

from db import wb_incidents
from db.core import connect
from db.fifo_finished_goods import _create_finished_goods_layer

from .base import DbTestCase

NM_ID = 555111


class WbIncidentLossTests(DbTestCase):
    def _receive_finished_goods(self, units: int, unit_price: float) -> None:
        """Seed a WB-location FIFO layer directly, the way
        initialize_finished_goods_fifo()/WB reconciliation would after goods
        have actually travelled ready -> inbound -> wb. apply_wb_incident_loss
        only ever draws down wb_units, so that's the location that matters here;
        exercising the full ready->inbound->wb dispatch pipeline belongs to the
        inventory_movements tests, not this incident-safety-guard test."""
        with connect() as conn:
            _create_finished_goods_layer(
                conn, NM_ID, "ART-1", "Плейсмат тест", "opening", f"seed:{NM_ID}:{units}:{unit_price}",
                "2026-01-01", units, unit_price, "wb", "test seed",
            )

    def _open_case(self, document_ref: str = "ACT-1") -> int:
        case_id = wb_incidents.create_wb_incident_case(
            incident_name="Недостача на складе", incident_date="2026-02-01",
            warehouse_label="Коледино", document_ref=document_ref, note="",
        )
        return case_id

    # -- safety guard: no stock snapshot at all ------------------------
    def test_blocked_without_any_stock_snapshot(self) -> None:
        self._receive_finished_goods(units=10, unit_price=100.0)
        case_id = self._open_case()
        with self.assertRaises(ValueError):
            wb_incidents.apply_wb_incident_loss(case_id, NM_ID, units=2)

    # -- safety guard: write-off must not go below current WB contour --
    def test_blocked_when_writeoff_would_exceed_safe_capacity(self) -> None:
        self._receive_finished_goods(units=10, unit_price=100.0)
        with connect() as conn:
            self.set_stock_snapshot(conn, NM_ID, quantity=10)  # current contour == full FIFO -> safe_capacity 0
        case_id = self._open_case()
        with self.assertRaises(ValueError):
            wb_incidents.apply_wb_incident_loss(case_id, NM_ID, units=1)

    def test_blocked_without_document_ref(self) -> None:
        self._receive_finished_goods(units=10, unit_price=100.0)
        with connect() as conn:
            self.set_stock_snapshot(conn, NM_ID, quantity=5)
        case_id = wb_incidents.create_wb_incident_case(
            incident_name="Недостача", incident_date="2026-02-01", warehouse_label="Коледино",
            document_ref="", note="",
        )
        with self.assertRaises(ValueError):
            wb_incidents.apply_wb_incident_loss(case_id, NM_ID, units=1)

    def test_blocked_beyond_total_fifo_units(self) -> None:
        self._receive_finished_goods(units=10, unit_price=100.0)
        with connect() as conn:
            self.set_stock_snapshot(conn, NM_ID, quantity=0)
        case_id = self._open_case()
        with self.assertRaises(ValueError):
            wb_incidents.apply_wb_incident_loss(case_id, NM_ID, units=999)

    # -- happy path: within safe capacity, cost + FIFO consumption correct
    def test_successful_loss_consumes_fifo_and_records_cost(self) -> None:
        self._receive_finished_goods(units=10, unit_price=100.0)
        with connect() as conn:
            self.set_stock_snapshot(conn, NM_ID, quantity=6)  # FIFO 10, contour 6 -> safe capacity 4
        case_id = self._open_case()

        outcome = wb_incidents.apply_wb_incident_loss(case_id, NM_ID, units=3, note="test loss")

        self.assertEqual(outcome["units"], 3)
        self.assertAlmostEqual(outcome["fifo_cost_rub"], 300.0, places=2)  # 3 units * 100 RUB
        self.assertEqual(outcome["fifo_before_units"], 10)
        self.assertEqual(outcome["fifo_after_units"], 7)
        self.assertEqual(outcome["safe_capacity_before_units"], 4)

        with connect() as conn:
            case = conn.execute("SELECT * FROM wb_incident_cases WHERE id=?", (case_id,)).fetchone()
            loss_lines = conn.execute(
                "SELECT * FROM wb_incident_loss_lines WHERE case_id=?", (case_id,)
            ).fetchall()
            remaining_wb_units = conn.execute(
                "SELECT COALESCE(SUM(wb_units),0) AS n FROM finished_goods_cost_layers WHERE nm_id=? AND status<>'reversed'",
                (NM_ID,),
            ).fetchone()
        self.assertEqual(case["status"], "confirmed")
        self.assertEqual(len(loss_lines), 1)
        self.assertEqual(loss_lines[0]["confirmed_units"], 3)
        self.assertAlmostEqual(loss_lines[0]["fifo_cost_rub"], 300.0, places=2)
        self.assertEqual(remaining_wb_units["n"], 7)

    def test_cannot_apply_loss_at_exactly_the_safe_capacity_boundary_plus_one(self) -> None:
        self._receive_finished_goods(units=10, unit_price=100.0)
        with connect() as conn:
            self.set_stock_snapshot(conn, NM_ID, quantity=6)  # safe capacity == 4
        case_id = self._open_case()
        with self.assertRaises(ValueError):
            wb_incidents.apply_wb_incident_loss(case_id, NM_ID, units=5)  # one more than allowed

        # exactly at the boundary must succeed
        outcome = wb_incidents.apply_wb_incident_loss(case_id, NM_ID, units=4)
        self.assertEqual(outcome["units"], 4)


if __name__ == "__main__":
    import unittest
    unittest.main()
