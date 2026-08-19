"""Integration tests: procurement receipt -> material FIFO layer -> WIP issue.

These exercise the public, cross-module entry points (not the private
helpers) to confirm the refactor didn't break the call chain
db.procurement.post_procurement_receipt -> db.fifo_materials._create_material_layer
and db.wip.issue_wip_material -> db.fifo_materials._consume_material_fifo,
and that the landed-cost math (supplier price + delivery, converted, plus
extras, divided by roll length) survives end to end into the WIP batch's
unit cost.
"""
from __future__ import annotations

import pandas as pd

from db import procurement, wip
from db.core import connect

from .base import DbTestCase


class ProcurementReceiptCreatesMaterialLayerTests(DbTestCase):
    def test_receipt_landed_cost_creates_expected_fifo_layer(self) -> None:
        order_id = procurement.create_procurement_order(
            procurement_type="Сырьё",
            supplier_name="Тестовый поставщик",
            status="Запланировано",
            order_date="2026-01-01",
            payment_due_date=None,
            expected_date="2026-01-10",
            note="",
            items=pd.DataFrame([{
                "material_name": "Красный ПВХ",
                "unit": "рулон",
                "roll_length": 25.0,
                "quantity": 4,
                "supplier_unit_price": 10.0,   # foreign currency unit price
                "exchange_rate": 95.0,          # RUB per unit of foreign currency
                "delivery_unit_foreign": 1.0,
                "extra_unit_rub": 5.0,
            }]),
        )
        items = procurement.read_procurement_items(order_id)
        self.assertEqual(len(items), 1)
        item_id = int(items.iloc[0]["id"])

        result = procurement.post_procurement_receipt(
            order_id, pd.DataFrame([{"item_id": item_id, "receive_now": 4}])
        )
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["items"], 1)

        # landed unit price (per roll) = (10 + 1) * 95 + 5 = 1050 RUB/roll
        # rate per metre = 1050 / 25 = 42 RUB/m ; total metres = 4 rolls * 25 m = 100 m
        with connect() as conn:
            layers = conn.execute(
                "SELECT * FROM material_cost_layers WHERE material_key='красный пвх' OR material_name='Красный ПВХ'"
            ).fetchall()
        self.assertEqual(len(layers), 1)
        layer = layers[0]
        self.assertAlmostEqual(layer["unit_cost_rub_m"], 42.0, places=6)
        self.assertAlmostEqual(layer["original_meters"], 100.0, places=6)
        self.assertAlmostEqual(layer["original_amount_rub"], 4200.0, places=2)
        self.assertEqual(layer["source_type"], "procurement")


class ProcurementToWipFifoFlowTests(DbTestCase):
    def _receive_material(self, unit_price_per_roll: float, rolls: float, roll_length: float = 25.0) -> None:
        order_id = procurement.create_procurement_order(
            procurement_type="Сырьё", supplier_name="Поставщик", status="Запланировано",
            order_date="2026-01-01", payment_due_date=None, expected_date="2026-01-05", note="",
            items=pd.DataFrame([{
                "material_name": "Синий ПВХ", "unit": "рулон", "roll_length": roll_length,
                "quantity": rolls, "unit_price": unit_price_per_roll,
            }]),
        )
        items = procurement.read_procurement_items(order_id)
        item_id = int(items.iloc[0]["id"])
        result = procurement.post_procurement_receipt(order_id, pd.DataFrame([{"item_id": item_id, "receive_now": rolls}]))
        self.assertEqual(result["errors"], [])

    def test_wip_issue_consumes_procurement_layer_fifo_and_costs_correctly(self) -> None:
        # First (older, cheaper) delivery: 2 rolls x 25m @ 300 RUB/roll -> 12 RUB/m
        self._receive_material(unit_price_per_roll=300.0, rolls=2, roll_length=25.0)
        # Second (newer, pricier) delivery: 2 rolls x 25m @ 500 RUB/roll -> 20 RUB/m
        self._receive_material(unit_price_per_roll=500.0, rolls=2, roll_length=25.0)

        with connect() as conn:
            layers_before = conn.execute(
                "SELECT * FROM material_cost_layers WHERE material_name='Синий ПВХ' ORDER BY id"
            ).fetchall()
        self.assertEqual(len(layers_before), 2)
        self.assertAlmostEqual(layers_before[0]["unit_cost_rub_m"], 12.0, places=6)
        self.assertAlmostEqual(layers_before[1]["unit_cost_rub_m"], 20.0, places=6)

        # Issue 60m to WIP: FIFO must take all 50m from the cheap layer first, then 10m from the pricier one.
        issue = wip.issue_wip_material(
            batch_date="2026-01-15", material_name="Синий ПВХ", blank_type="Старые болванки",
            meters_used=60.0, note="test batch",
        )
        self.assertEqual(issue["errors"], [])
        self.assertEqual(issue["posted"], 1)
        expected_cost = round(50.0 * 12.0 + 10.0 * 20.0, 2)  # 600 + 200 = 800
        self.assertAlmostEqual(issue["cost_rub"], expected_cost, places=2)

        with connect() as conn:
            batch = conn.execute(
                "SELECT * FROM wip_blank_batches WHERE id=?", (issue["batch_id"],)
            ).fetchone()
            layers_after = conn.execute(
                "SELECT * FROM material_cost_layers WHERE material_name='Синий ПВХ' ORDER BY id"
            ).fetchall()
        self.assertAlmostEqual(batch["material_cost_rub"], expected_cost, places=2)
        self.assertAlmostEqual(batch["issued_meters"], 60.0, places=6)
        self.assertEqual(layers_after[0]["status"], "depleted")
        self.assertAlmostEqual(layers_after[0]["remaining_meters"], 0.0, places=6)
        self.assertEqual(layers_after[1]["status"], "active")
        self.assertAlmostEqual(layers_after[1]["remaining_meters"], 40.0, places=6)  # 50 - 10 consumed

    def test_wip_issue_beyond_available_fifo_and_physical_stock_fails_cleanly(self) -> None:
        self._receive_material(unit_price_per_roll=300.0, rolls=1, roll_length=25.0)  # only 25m total
        issue = wip.issue_wip_material(
            batch_date="2026-01-15", material_name="Синий ПВХ", blank_type="Старые болванки",
            meters_used=100.0, note="too much",
        )
        self.assertEqual(issue["posted"], 0)
        self.assertTrue(issue["errors"])
        # And the attempt must not have partially mutated state (savepoint rollback).
        with connect() as conn:
            layer = conn.execute(
                "SELECT * FROM material_cost_layers WHERE material_name='Синий ПВХ'"
            ).fetchone()
            batches = conn.execute("SELECT COUNT(*) AS n FROM wip_blank_batches").fetchone()
        self.assertAlmostEqual(layer["remaining_meters"], 25.0, places=6)  # untouched
        self.assertEqual(batches["n"], 0)


if __name__ == "__main__":
    import unittest
    unittest.main()
