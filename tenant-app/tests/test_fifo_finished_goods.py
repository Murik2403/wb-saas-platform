"""Unit tests for db/fifo_finished_goods.py -- the FIFO cost-layer engine
behind every finished-goods valuation, sale COGS, and incident write-off in
the app. Money-critical: a bug here silently mis-prices every sale.

Exercises the private helpers directly (the same pattern already used by
tests/test_wb_incidents.py, which imports _create_finished_goods_layer) --
these functions have no framework/network dependency, so they run fine
under plain stdlib unittest.
"""
from __future__ import annotations

from db.core import connect
from db.fifo_finished_goods import (
    _create_finished_goods_layer,
    _ensure_finished_goods_capacity,
    _finished_layer_totals,
    _move_finished_goods_fifo,
    _reverse_finished_goods_allocations,
    _reverse_finished_goods_source_layer,
    initialize_finished_goods_fifo,
)

from .base import DbTestCase

NM_ID = 700001


class CreateFinishedGoodsLayerTests(DbTestCase):
    def test_create_layer_basic_fields(self) -> None:
        with connect() as conn:
            layer_id = _create_finished_goods_layer(
                conn, NM_ID, "ART-1", "Тест", "opening", "seed:1", "2026-01-01",
                10, 50.0, "ready", "note",
            )
            row = conn.execute("SELECT * FROM finished_goods_cost_layers WHERE id=?", (layer_id,)).fetchone()
        self.assertEqual(row["ready_units"], 10)
        self.assertEqual(row["inbound_units"], 0)
        self.assertEqual(row["wb_units"], 0)
        self.assertEqual(row["original_units"], 10)
        self.assertAlmostEqual(row["original_amount_rub"], 500.0, places=2)
        self.assertEqual(row["status"], "active")

    def test_create_layer_rejects_zero_units(self) -> None:
        with connect() as conn:
            with self.assertRaises(ValueError):
                _create_finished_goods_layer(
                    conn, NM_ID, "ART-1", "Тест", "opening", "seed:2", "2026-01-01",
                    0, 50.0, "ready",
                )

    def test_create_layer_rejects_unknown_location(self) -> None:
        with connect() as conn:
            with self.assertRaises(ValueError):
                _create_finished_goods_layer(
                    conn, NM_ID, "ART-1", "Тест", "opening", "seed:3", "2026-01-01",
                    5, 50.0, "warehouse-on-the-moon",
                )


class FinishedLayerTotalsTests(DbTestCase):
    def test_totals_sum_active_layers_and_exclude_reversed(self) -> None:
        with connect() as conn:
            _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:1", "2026-01-01", 10, 50.0, "wb")
            _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:2", "2026-01-02", 5, 60.0, "wb")
            reversed_id = _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:3", "2026-01-03", 100, 999.0, "wb")
            conn.execute("UPDATE finished_goods_cost_layers SET status='reversed',wb_units=0 WHERE id=?", (reversed_id,))
            units, amount = _finished_layer_totals(conn, NM_ID, "wb")
        self.assertEqual(units, 15)
        self.assertAlmostEqual(amount, 10 * 50.0 + 5 * 60.0, places=2)


class EnsureFinishedGoodsCapacityTests(DbTestCase):
    def test_noop_when_layered_units_already_cover_required(self) -> None:
        with connect() as conn:
            _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:1", "2026-01-01", 20, 50.0, "wb")
            _ensure_finished_goods_capacity(conn, NM_ID, "ART-1", "Тест", "wb", 10)
            units, _ = _finished_layer_totals(conn, NM_ID, "wb")
        self.assertEqual(units, 20)  # untouched, no auto layer created

    def test_auto_creates_opening_layer_from_confirmed_physical_stock(self) -> None:
        """If FIFO layers are short but the physical balance is confirmed
        (known=1) and covers the gap, a layer is auto-created for the
        *entire* missing amount (not just what's required right now) --
        mirrors the materials FIFO behaviour already pinned in
        tests/test_fifo_materials.py."""
        with connect() as conn:
            conn.execute(
                "INSERT INTO product_pipeline(nm_id,local_known,ready_units,inbound_known,inbound_units) VALUES (?,1,15,0,0)",
                (NM_ID,),
            )
            conn.execute("INSERT INTO costs(nm_id,cost_per_wb_unit) VALUES (?,42.0)", (NM_ID,))
            _ensure_finished_goods_capacity(conn, NM_ID, "ART-1", "Тест", "ready", 10)
            units, amount = _finished_layer_totals(conn, NM_ID, "ready")
        self.assertEqual(units, 15)
        self.assertAlmostEqual(amount, 15 * 42.0, places=2)

    def test_raises_when_physical_stock_confirmed_but_still_insufficient(self) -> None:
        with connect() as conn:
            conn.execute(
                "INSERT INTO product_pipeline(nm_id,local_known,ready_units,inbound_known,inbound_units) VALUES (?,1,3,0,0)",
                (NM_ID,),
            )
            with self.assertRaises(ValueError):
                _ensure_finished_goods_capacity(conn, NM_ID, "ART-1", "Тест", "ready", 10)

    def test_raises_when_physical_stock_not_confirmed(self) -> None:
        with connect() as conn:
            conn.execute(
                "INSERT INTO product_pipeline(nm_id,local_known,ready_units,inbound_known,inbound_units) VALUES (?,0,999,0,0)",
                (NM_ID,),
            )
            with self.assertRaises(ValueError):
                _ensure_finished_goods_capacity(conn, NM_ID, "ART-1", "Тест", "ready", 10)


class MoveFinishedGoodsFifoTests(DbTestCase):
    def test_move_zero_units_is_noop(self) -> None:
        with connect() as conn:
            amount, allocations = _move_finished_goods_fifo(conn, 1, NM_ID, "ART-1", "Тест", 0, "wb", None)
        self.assertEqual(amount, 0.0)
        self.assertEqual(allocations, [])

    def test_consumes_oldest_layer_first(self) -> None:
        with connect() as conn:
            _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:old", "2026-01-01", 5, 100.0, "wb")
            _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:new", "2026-01-10", 20, 200.0, "wb")
            amount, allocations = _move_finished_goods_fifo(conn, 1, NM_ID, "ART-1", "Тест", 8, "wb", None)
            units, _ = _finished_layer_totals(conn, NM_ID, "wb")
        # 5 units @100 (all of the old layer) + 3 units @200 (from the new layer)
        self.assertAlmostEqual(amount, 5 * 100.0 + 3 * 200.0, places=2)
        self.assertEqual(len(allocations), 2)
        self.assertEqual(units, 17)  # 25 - 8

    def test_move_from_ready_to_inbound_shifts_both_columns(self) -> None:
        with connect() as conn:
            layer_id = _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:1", "2026-01-01", 10, 50.0, "ready")
            _move_finished_goods_fifo(conn, 1, NM_ID, "ART-1", "Тест", 4, "ready", "inbound")
            row = conn.execute("SELECT ready_units,inbound_units FROM finished_goods_cost_layers WHERE id=?", (layer_id,)).fetchone()
        self.assertEqual(row["ready_units"], 6)
        self.assertEqual(row["inbound_units"], 4)

    def test_insufficient_units_raises_and_does_not_partially_apply(self) -> None:
        with connect() as conn:
            _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:1", "2026-01-01", 3, 50.0, "wb")
            with self.assertRaises(ValueError):
                _move_finished_goods_fifo(conn, 1, NM_ID, "ART-1", "Тест", 10, "wb", None)

    def test_cost_pending_layer_blocks_wb_dispatch_when_regular_units_insufficient(self) -> None:
        """A layer created via retroactive reconstruction with unresolved cost
        (source_type='reconstruction_cost_pending') must never be silently
        consumed at a synthetic rate -- selling through it before the real
        cost is resolved would silently mis-price COGS."""
        with connect() as conn:
            _create_finished_goods_layer(
                conn, NM_ID, "ART-1", "Тест", "reconstruction_cost_pending", "pending:1", "2026-01-01",
                5, 0.0, "wb",
            )
            _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:regular", "2026-01-02", 2, 100.0, "wb")
            with self.assertRaises(ValueError) as ctx:
                _move_finished_goods_fifo(conn, 1, NM_ID, "ART-1", "Тест", 3, "wb", None)
        self.assertIn("COST_PENDING", str(ctx.exception))

    def test_cost_pending_layer_does_not_block_when_regular_units_suffice(self) -> None:
        with connect() as conn:
            _create_finished_goods_layer(
                conn, NM_ID, "ART-1", "Тест", "reconstruction_cost_pending", "pending:1", "2026-01-01",
                5, 0.0, "wb",
            )
            _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:regular", "2026-01-02", 10, 100.0, "wb")
            amount, _ = _move_finished_goods_fifo(conn, 1, NM_ID, "ART-1", "Тест", 3, "wb", None)
        self.assertAlmostEqual(amount, 3 * 100.0, places=2)


class ReverseFinishedGoodsAllocationsTests(DbTestCase):
    def test_reverse_restores_units_and_marks_allocation_reversed(self) -> None:
        with connect() as conn:
            layer_id = _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:1", "2026-01-01", 10, 50.0, "wb")
            _move_finished_goods_fifo(conn, 42, NM_ID, "ART-1", "Тест", 4, "wb", None)
            _reverse_finished_goods_allocations(conn, 42)
            row = conn.execute("SELECT wb_units,status FROM finished_goods_cost_layers WHERE id=?", (layer_id,)).fetchone()
            alloc = conn.execute("SELECT status FROM finished_goods_fifo_allocations WHERE movement_id=?", (42,)).fetchone()
        self.assertEqual(row["wb_units"], 10)
        self.assertEqual(row["status"], "active")
        self.assertEqual(alloc["status"], "reversed")

    def test_reverse_blocked_when_units_already_moved_further(self) -> None:
        with connect() as conn:
            _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:1", "2026-01-01", 10, 50.0, "ready")
            # ready -> inbound (movement 1)
            _move_finished_goods_fifo(conn, 1, NM_ID, "ART-1", "Тест", 10, "ready", "inbound")
            # inbound -> wb (movement 2), consumes everything the first move produced
            _move_finished_goods_fifo(conn, 2, NM_ID, "ART-1", "Тест", 10, "inbound", "wb")
            with self.assertRaises(ValueError):
                _reverse_finished_goods_allocations(conn, 1)


class ReverseFinishedGoodsSourceLayerTests(DbTestCase):
    def test_reverses_untouched_opening_layer(self) -> None:
        with connect() as conn:
            layer_id = _create_finished_goods_layer(
                conn, NM_ID, "ART-1", "Тест", "opening", "movement:99", "2026-01-01", 10, 50.0, "wb",
            )
            _reverse_finished_goods_source_layer(conn, 99)
            row = conn.execute("SELECT status,wb_units FROM finished_goods_cost_layers WHERE id=?", (layer_id,)).fetchone()
        self.assertEqual(row["status"], "reversed")
        self.assertEqual(row["wb_units"], 0)

    def test_noop_when_no_matching_layer(self) -> None:
        with connect() as conn:
            _reverse_finished_goods_source_layer(conn, 12345)  # must not raise

    def test_blocked_when_layer_has_active_allocations(self) -> None:
        with connect() as conn:
            _create_finished_goods_layer(
                conn, NM_ID, "ART-1", "Тест", "opening", "movement:99", "2026-01-01", 10, 50.0, "wb",
            )
            _move_finished_goods_fifo(conn, 100, NM_ID, "ART-1", "Тест", 3, "wb", None)
            with self.assertRaises(ValueError):
                _reverse_finished_goods_source_layer(conn, 99)

    def test_blocked_when_layer_partially_consumed_even_without_active_allocations(self) -> None:
        """Belt-and-braces guard: original_units no longer equals the sum of
        current location columns (e.g. reconciled elsewhere) -- must not
        silently zero out a layer that's not fully intact."""
        with connect() as conn:
            layer_id = _create_finished_goods_layer(
                conn, NM_ID, "ART-1", "Тест", "opening", "movement:99", "2026-01-01", 10, 50.0, "wb",
            )
            conn.execute("UPDATE finished_goods_cost_layers SET wb_units=4 WHERE id=?", (layer_id,))  # units "vanished" without an allocation row
            with self.assertRaises(ValueError):
                _reverse_finished_goods_source_layer(conn, 99)


class InitializeFinishedGoodsFifoTests(DbTestCase):
    def test_creates_opening_layers_from_confirmed_physical_balances(self) -> None:
        with connect() as conn:
            conn.execute(
                "INSERT INTO product_pipeline(nm_id,local_known,ready_units,inbound_known,inbound_units) VALUES (?,1,7,1,3)",
                (NM_ID,),
            )
            conn.execute("INSERT INTO costs(nm_id,cost_per_wb_unit) VALUES (?,20.0)", (NM_ID,))
        result = initialize_finished_goods_fifo(reconcile_wb=False)
        self.assertEqual(result["created"], 2)  # ready + inbound
        self.assertEqual(result["units"], 10)
        with connect() as conn:
            ready_units, _ = _finished_layer_totals(conn, NM_ID, "ready")
            inbound_units, _ = _finished_layer_totals(conn, NM_ID, "inbound")
        self.assertEqual(ready_units, 7)
        self.assertEqual(inbound_units, 3)

    def test_warns_without_reconcile_when_layers_exceed_physical_wb_balance(self) -> None:
        with connect() as conn:
            _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:1", "2026-01-01", 20, 50.0, "wb")
            self.set_stock_snapshot(conn, NM_ID, quantity=12)
        result = initialize_finished_goods_fifo(reconcile_wb=False)
        self.assertTrue(any("больше физического остатка" in w for w in result["warnings"]))
        with connect() as conn:
            units, _ = _finished_layer_totals(conn, NM_ID, "wb")
        self.assertEqual(units, 20)  # untouched -- no reconciliation requested

    def test_reconcile_wb_consumes_fifo_down_to_the_confirmed_snapshot(self) -> None:
        with connect() as conn:
            _create_finished_goods_layer(conn, NM_ID, "ART-1", "Тест", "opening", "seed:1", "2026-01-01", 20, 50.0, "wb")
            self.set_stock_snapshot(conn, NM_ID, quantity=12)
        result = initialize_finished_goods_fifo(reconcile_wb=True)
        self.assertEqual(result["wb_consumed"], 8)
        with connect() as conn:
            units, _ = _finished_layer_totals(conn, NM_ID, "wb")
            movement = conn.execute(
                "SELECT * FROM inventory_movements WHERE movement_type='wb_cost_reconciliation' AND nm_id=?", (NM_ID,)
            ).fetchone()
        self.assertEqual(units, 12)
        self.assertIsNotNone(movement)
        self.assertAlmostEqual(movement["goods_cost_rub"], 8 * 50.0, places=2)

    def test_is_idempotent_when_run_twice_with_no_new_physical_movement(self) -> None:
        with connect() as conn:
            conn.execute(
                "INSERT INTO product_pipeline(nm_id,local_known,ready_units,inbound_known,inbound_units) VALUES (?,1,5,0,0)",
                (NM_ID,),
            )
            conn.execute("INSERT INTO costs(nm_id,cost_per_wb_unit) VALUES (?,10.0)", (NM_ID,))
        first = initialize_finished_goods_fifo(reconcile_wb=False)
        second = initialize_finished_goods_fifo(reconcile_wb=False)
        self.assertEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)  # already matches physical -- nothing new to create
        with connect() as conn:
            units, _ = _finished_layer_totals(conn, NM_ID, "ready")
        self.assertEqual(units, 5)


if __name__ == "__main__":
    import unittest
    unittest.main()
