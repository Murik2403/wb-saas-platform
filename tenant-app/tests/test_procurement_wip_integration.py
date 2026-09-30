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
    def test_centimetres_receipt_preserves_known_packaged_catalog_length(self):
        with connect() as conn:
            self.set_material_physical_stock(conn,'ткань','Ткань',partial_meters=0,roll_length=25.5)
            conn.execute("UPDATE material_inventory_color SET unit='м',tracking_mode='packaged' WHERE material_key='ткань'")
        order=procurement.create_procurement_order(procurement_type='Сырьё',supplier_name='Supplier',status='Запланировано',order_date='2026-09-01',payment_due_date=None,expected_date=None,note='',items=pd.DataFrame([{'material_name':'Ткань','unit':'см','quantity':2600,'unit_price':0.1}]))
        item=procurement.read_procurement_items(order).iloc[0]
        result=procurement.post_procurement_receipt(order,pd.DataFrame([{'item_id':int(item['id']),'receive_now':2600}]))
        self.assertEqual(result['errors'],[])
        with connect() as conn:
            self.assertEqual(tuple(conn.execute("SELECT full_rolls,partial_meters,roll_length,tracking_mode FROM material_inventory_color WHERE material_key='ткань'").fetchone()),(1,0.5,25.5,'packaged'))
            self.assertEqual(tuple(conn.execute("SELECT original_meters,unit_cost_rub_m,original_amount_rub FROM material_cost_layers WHERE material_key='ткань'").fetchone()),(26,10,260))

    def test_partial_grams_receipt_keeps_currency_total_and_immutable_base_provenance(self):
        import json
        import sqlite3
        with connect() as conn:
            self.set_material_physical_stock(conn,'нить','Нить',partial_meters=0,roll_length=1)
            conn.execute("UPDATE material_inventory_color SET unit='кг',tracking_mode='quantity' WHERE material_key='нить'")
        order=procurement.create_procurement_order(procurement_type='Сырьё',supplier_name='Supplier',status='Запланировано',order_date='2026-09-01',payment_due_date=None,expected_date=None,note='',currency='CNY',exchange_rate=13,items=pd.DataFrame([{'material_name':'Нить','unit':'г','quantity':1000,'supplier_unit_price':0.1,'exchange_rate':13}]))
        item=procurement.read_procurement_items(order).iloc[0]
        first=procurement.post_procurement_receipt(order,pd.DataFrame([{'item_id':int(item['id']),'receive_now':250}]))
        self.assertEqual(first['errors'],[])
        with connect() as conn:
            layer=conn.execute("SELECT * FROM material_cost_layers WHERE material_key='нить'").fetchone()
            self.assertEqual((layer['original_meters'],layer['unit_cost_rub_m'],layer['original_amount_rub']),(0.25,1300,325))
            self.assertEqual(conn.execute("SELECT partial_meters FROM material_inventory_color WHERE material_key='нить'").fetchone()[0],0.25)
            proof=json.loads(conn.execute('SELECT payload_json FROM material_unit_conversion_receipts').fetchone()[0])
            self.assertEqual((proof['entered_quantity'],proof['base_quantity'],proof['source_total_rub']),('250','0.25','325'))
            with self.assertRaises(sqlite3.IntegrityError):conn.execute('DELETE FROM material_unit_conversion_receipts')
        second=procurement.post_procurement_receipt(order,pd.DataFrame([{'item_id':int(item['id']),'receive_now':750}]))
        self.assertEqual(second['errors'],[])
        replay=procurement.post_procurement_receipt(order,pd.DataFrame([{'item_id':int(item['id']),'receive_now':1000}]))
        self.assertEqual(replay['items'],0)
        updated=procurement.read_procurement_items(order).iloc[0]
        self.assertEqual((updated['quantity'],updated['unit'],updated['unit_price'],updated['received_quantity']),(1000,'г',1.3,1000))
        with connect() as conn:
            self.assertEqual(conn.execute('SELECT SUM(original_amount_rub) FROM material_cost_layers').fetchone()[0],1300)
            movement=conn.execute("SELECT id FROM inventory_movements WHERE movement_type='procurement_material_receipt' ORDER BY id DESC LIMIT 1").fetchone()[0]
        from db.inventory_movements import undo_inventory_movement
        reversed_receipt=undo_inventory_movement(movement)
        self.assertTrue(reversed_receipt['ok'],reversed_receipt)
        self.assertEqual(procurement.read_procurement_items(order).iloc[0]['received_quantity'],250)
        with connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM material_unit_conversion_receipts').fetchone()[0],2)
            self.assertEqual(conn.execute("SELECT full_rolls+partial_meters FROM material_inventory_color WHERE material_key='нить'").fetchone()[0],0.25)
        replacement=procurement.post_procurement_receipt(order,pd.DataFrame([{'item_id':int(item['id']),'receive_now':750}]))
        self.assertEqual(replacement['errors'],[])
        issue=wip.issue_wip_material(batch_date='2026-09-30',material_name='Нить',blank_type='Тип А',meters_used=0.5,note='converted-base')
        self.assertEqual(issue['errors'],[])
        self.assertEqual(issue['cost_rub'],650)

    def test_receipt_cross_dimension_rejects_without_inventory_or_receipt_mutation(self):
        with connect() as conn:
            self.set_material_physical_stock(conn,'нить','Нить',partial_meters=0,roll_length=1)
            conn.execute("UPDATE material_inventory_color SET unit='кг',tracking_mode='quantity' WHERE material_key='нить'")
        order=procurement.create_procurement_order(procurement_type='Сырьё',supplier_name='Supplier',status='Запланировано',order_date='2026-09-01',payment_due_date=None,expected_date=None,note='',items=pd.DataFrame([{'material_name':'Нить','unit':'л','quantity':1,'unit_price':100}]))
        item=procurement.read_procurement_items(order).iloc[0]
        result=procurement.post_procurement_receipt(order,pd.DataFrame([{'item_id':int(item['id']),'receive_now':1}]))
        self.assertTrue(result['errors'])
        with connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM material_cost_layers').fetchone()[0],0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM inventory_movements WHERE movement_type='procurement_material_receipt'").fetchone()[0],0)
        self.assertEqual(procurement.read_procurement_items(order).iloc[0]['received_quantity'],0)

    def test_converted_receipt_preserves_exact_physical_quantity_without_tolerance_carry(self):
        with connect() as conn:
            self.set_material_physical_stock(conn,'нить','Нить',partial_meters=0,roll_length=1)
            conn.execute("UPDATE material_inventory_color SET unit='кг',tracking_mode='quantity' WHERE material_key='нить'")
        order=procurement.create_procurement_order(procurement_type='Сырьё',supplier_name='Supplier',status='Запланировано',order_date='2026-09-01',payment_due_date=None,expected_date=None,note='',items=pd.DataFrame([{'material_name':'Нить','unit':'г','quantity':999,'unit_price':0.1}]))
        item=procurement.read_procurement_items(order).iloc[0]
        result=procurement.post_procurement_receipt(order,pd.DataFrame([{'item_id':int(item['id']),'receive_now':999}]))
        self.assertEqual(result['errors'],[])
        self.assertEqual(result['items'],1)
        with connect() as conn:
            self.assertEqual(tuple(conn.execute('SELECT original_meters,original_amount_rub FROM material_cost_layers').fetchone()),(0.999,99.9))
            self.assertEqual(tuple(conn.execute("SELECT full_rolls,partial_meters FROM material_inventory_color WHERE material_key='нить'").fetchone()),(0,0.999))

    def test_product_receipt_keeps_variant_on_item_movement_and_fifo_layer(self) -> None:
        order_id = procurement.create_procurement_order(
            procurement_type="Товар", supplier_name="Supplier", status="Запланировано",
            order_date="2026-01-01", payment_due_date=None, expected_date="2026-01-10", note="",
            items=pd.DataFrame([{
                "nm_id": 981235, "chrt_id": 321, "supplier_article": "SKU-321",
                "product_name": "Variant product", "quantity": 2, "unit_price": 75,
            }]),
        )
        item = procurement.read_procurement_items(order_id).iloc[0]
        self.assertEqual(int(item["chrt_id"]), 321)
        result = procurement.post_procurement_receipt(
            order_id, pd.DataFrame([{"item_id": int(item["id"]), "receive_now": 2}])
        )
        self.assertEqual(result["errors"], [])
        with connect() as conn:
            movement = conn.execute(
                "SELECT chrt_id FROM inventory_movements WHERE movement_type='procurement_product_receipt'"
            ).fetchone()
            layer = conn.execute(
                "SELECT chrt_id,ready_units,unit_cost_rub FROM finished_goods_cost_layers WHERE nm_id=981235"
            ).fetchone()
        self.assertEqual(movement["chrt_id"], 321)
        self.assertEqual(tuple(layer), (321, 2, 75.0))

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
            batch_date="2026-01-15", material_name="Синий ПВХ", blank_type="Тип А",
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
            batch_date="2026-01-15", material_name="Синий ПВХ", blank_type="Тип А",
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
