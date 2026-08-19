"""Unit tests for db/fifo_materials.py -- the raw-material FIFO cost engine.

These are the tests README_REFACTOR.md flagged as missing ("Тестов
по-прежнему нет ... для денежной логики (FIFO, инциденты, закупки) стоит
закрыть хотя бы базовыми unit-тестами"). They exercise the private
consume/create/reverse functions directly against a real (throwaway) sqlite
schema, because that's exactly the money-critical logic every higher-level
flow (procurement receipt, WIP issue, reconciliation) depends on.
"""
from __future__ import annotations

from db import fifo_materials as fm
from db.core import connect

from .base import DbTestCase


class CreateMaterialLayerTests(DbTestCase):
    def test_create_layer_basic_fields(self) -> None:
        with connect() as conn:
            layer_id = fm._create_material_layer(
                conn, "red", "Красный ПВХ", "opening", "seed:1", "2026-01-01", 100.0, 400.0,
            )
            row = conn.execute("SELECT * FROM material_cost_layers WHERE id=?", (layer_id,)).fetchone()
        self.assertEqual(row["material_key"], "red")
        self.assertEqual(row["remaining_meters"], 100.0)
        self.assertEqual(row["original_meters"], 100.0)
        self.assertEqual(row["unit_cost_rub_m"], 400.0)
        self.assertEqual(row["original_amount_rub"], 40000.0)
        self.assertEqual(row["status"], "active")

    def test_create_layer_rejects_zero_meters(self) -> None:
        with connect() as conn:
            with self.assertRaises(ValueError):
                fm._create_material_layer(conn, "red", "Красный ПВХ", "opening", "seed:2", "2026-01-01", 0.0, 400.0)


class ConsumeMaterialFifoTests(DbTestCase):
    def _layer(self, conn, meters: float, rate: float, source_ref: str, source_date: str):
        return fm._create_material_layer(conn, "red", "Красный ПВХ", "opening", source_ref, source_date, meters, rate)

    def test_consumes_oldest_layer_first(self) -> None:
        """FIFO must drain the earliest source_date layer before touching newer ones."""
        with connect() as conn:
            older = self._layer(conn, 50.0, 300.0, "seed:older", "2026-01-01")
            newer = self._layer(conn, 50.0, 500.0, "seed:newer", "2026-02-01")
            cost, allocations = fm._consume_material_fifo(conn, movement_id=1, material_key="red",
                                                            material_name="Красный ПВХ", meters=30.0)
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0]["layer_id"], float(older))
        self.assertEqual(cost, 30.0 * 300.0)  # only the cheap/older layer was touched

    def test_consumption_spans_multiple_layers_in_order(self) -> None:
        with connect() as conn:
            older = self._layer(conn, 20.0, 300.0, "seed:older", "2026-01-01")
            newer = self._layer(conn, 50.0, 500.0, "seed:newer", "2026-02-01")
            cost, allocations = fm._consume_material_fifo(conn, movement_id=2, material_key="red",
                                                            material_name="Красный ПВХ", meters=45.0)
            older_row = conn.execute("SELECT * FROM material_cost_layers WHERE id=?", (older,)).fetchone()
            newer_row = conn.execute("SELECT * FROM material_cost_layers WHERE id=?", (newer,)).fetchone()
        expected_cost = round(20.0 * 300.0 + 25.0 * 500.0, 2)
        self.assertEqual(cost, expected_cost)
        self.assertEqual(len(allocations), 2)
        self.assertEqual(older_row["remaining_meters"], 0.0)
        self.assertEqual(older_row["status"], "depleted")
        self.assertAlmostEqual(newer_row["remaining_meters"], 25.0, places=6)
        self.assertEqual(newer_row["status"], "active")

    def test_consume_zero_meters_is_noop(self) -> None:
        with connect() as conn:
            self._layer(conn, 20.0, 300.0, "seed:a", "2026-01-01")
            cost, allocations = fm._consume_material_fifo(conn, movement_id=3, material_key="red",
                                                            material_name="Красный ПВХ", meters=0.0)
        self.assertEqual(cost, 0.0)
        self.assertEqual(allocations, [])

    def test_insufficient_fifo_and_no_physical_backup_raises(self) -> None:
        """Without any layer AND without physical stock recorded, FIFO must refuse to consume."""
        with connect() as conn:
            with self.assertRaises(ValueError):
                fm._consume_material_fifo(conn, movement_id=4, material_key="blue",
                                           material_name="Синий ПВХ", meters=10.0)

    def test_ensure_capacity_auto_creates_opening_layer_from_physical_stock(self) -> None:
        """If FIFO layers run short but physical inventory covers it, an 'opening_auto'
        layer must be created at the configured opening rate rather than failing."""
        with connect() as conn:
            self.set_material_physical_stock(conn, "green", "Зелёный ПВХ", full_rolls=4, partial_meters=2.0,
                                              roll_length=25.0)  # 102 m physical, no FIFO layers yet
            cost, allocations = fm._consume_material_fifo(conn, movement_id=5, material_key="green",
                                                            material_name="Зелёный ПВХ", meters=60.0)
            layers = conn.execute(
                "SELECT * FROM material_cost_layers WHERE material_key='green'"
            ).fetchall()
        self.assertEqual(len(layers), 1)
        self.assertEqual(layers[0]["source_type"], "opening_auto")
        self.assertAlmostEqual(cost, 60.0 * fm.DEFAULT_OPENING_MATERIAL_RATE_RUB_M, places=2)

    def test_ensure_capacity_still_raises_when_physical_stock_also_insufficient(self) -> None:
        with connect() as conn:
            self.set_material_physical_stock(conn, "green", "Зелёный ПВХ", full_rolls=1, partial_meters=0.0,
                                              roll_length=25.0)  # only 25 m physical
            with self.assertRaises(ValueError):
                fm._consume_material_fifo(conn, movement_id=6, material_key="green",
                                           material_name="Зелёный ПВХ", meters=60.0)


class ReverseFifoConsumptionTests(DbTestCase):
    def test_reverse_restores_layer_remaining_meters_and_status(self) -> None:
        with connect() as conn:
            layer_id = fm._create_material_layer(conn, "red", "Красный ПВХ", "opening", "seed:1",
                                                   "2026-01-01", 30.0, 300.0)
            fm._consume_material_fifo(conn, movement_id=7, material_key="red", material_name="Красный ПВХ",
                                       meters=30.0)
            depleted = conn.execute("SELECT * FROM material_cost_layers WHERE id=?", (layer_id,)).fetchone()
            self.assertEqual(depleted["status"], "depleted")
            self.assertEqual(depleted["remaining_meters"], 0.0)

            fm._reverse_fifo_consumption(conn, movement_id=7)
            restored = conn.execute("SELECT * FROM material_cost_layers WHERE id=?", (layer_id,)).fetchone()
            consumption = conn.execute(
                "SELECT * FROM material_fifo_consumptions WHERE movement_id=7"
            ).fetchone()
        self.assertEqual(restored["status"], "active")
        self.assertEqual(restored["remaining_meters"], 30.0)
        self.assertEqual(consumption["status"], "reversed")
        self.assertIsNotNone(consumption["reversed_at"])

    def test_reverse_is_idempotent_for_already_reversed_consumption(self) -> None:
        """Calling reverse twice must not double-credit the layer."""
        with connect() as conn:
            layer_id = fm._create_material_layer(conn, "red", "Красный ПВХ", "opening", "seed:1",
                                                   "2026-01-01", 30.0, 300.0)
            fm._consume_material_fifo(conn, movement_id=8, material_key="red", material_name="Красный ПВХ",
                                       meters=10.0)
            fm._reverse_fifo_consumption(conn, movement_id=8)
            fm._reverse_fifo_consumption(conn, movement_id=8)  # second call: nothing left to reverse
            row = conn.execute("SELECT * FROM material_cost_layers WHERE id=?", (layer_id,)).fetchone()
        self.assertEqual(row["remaining_meters"], 30.0)  # not 40 -- only the original 10 credited back once


if __name__ == "__main__":
    import unittest
    unittest.main()
