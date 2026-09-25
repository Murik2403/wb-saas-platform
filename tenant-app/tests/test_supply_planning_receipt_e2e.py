"""Cross-module acceptance for supply-plan confirmation through material FIFO receipt."""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from db import procurement
from db.core import connect
from db.supply_planning import build_supply_preview, confirm_supply_plan, load_supply_plan_snapshot
from base import DbTestCase


class SupplyPlanReceiptFifoE2ETests(DbTestCase):
    def _seed_exact_variant_plan(self, conn) -> None:
        conn.execute(
            "INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES (10,'SKU-10','Товар 10',?)",
            (json.dumps({"sizes": [{"chrtID": 110}]}),),
        )
        conn.execute("INSERT INTO execution_tasks(task_key,task_type,task_date,nm_id,chrt_id,planned_units,actual_units,status,updated_at) VALUES ('task-10','production','2026-09-12',10,110,3,0,'Не начато','2026-09-01')")
        conn.execute("INSERT INTO manufacturing_boms(nm_id,chrt_id,version,materials_json,operations_json,packaging_per_unit,other_per_unit,note,created_at) VALUES (10,110,2,?, '[]',0,0,'','2026-09-01')", ('[{"material_key":"fabric-a","material_name":"Ткань A","quantity_per_unit":2,"unit":"м"}]',))
        conn.execute("INSERT INTO material_inventory_color(material_key,material_name,balance_known,full_rolls,partial_meters,roll_length,unit,tracking_mode,updated_at) VALUES ('fabric-a','Ткань A',1,0,0,1,'м','quantity','2026-09-01')")
        conn.execute("INSERT INTO suppliers(name,default_currency,lead_time_days,active,created_at,updated_at) VALUES ('Поставщик A','RUB',5,1,'2026-09-01','2026-09-01')")

    def test_exact_variant_confirm_stays_internal_until_receipt_then_creates_fifo_layer(self):
        key = "00000000-0000-4000-8000-000000000101"
        with connect() as conn:
            self._seed_exact_variant_plan(conn)
            preview = build_supply_preview(load_supply_plan_snapshot(conn, "2026-09-01", "2026-09-30"))
            self.assertEqual(preview["blockers"], [])
            self.assertEqual(preview["lines"][0]["required_base_qty"], 6)
            self.assertEqual(preview["lines"][0]["source_refs"][0]["chrt_id"], 110)
            before = tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("procurement_orders", "procurement_items", "inventory_movements", "material_cost_layers"))
            result = confirm_supply_plan(conn, key, "request-101", preview["preview_fingerprint"], preview, [{"material_key": "fabric-a", "unit": "м", "quantity": 6, "supplier_id": 1, "expected_date": "2026-09-10"}])
            self.assertFalse(result["replayed"])
            order_id = result["order_ids"][0]
            order = conn.execute("SELECT status,note FROM procurement_orders WHERE id=?", (order_id,)).fetchone()
            item = conn.execute("SELECT id,quantity,unit,material_key FROM procurement_items WHERE order_id=?", (order_id,)).fetchone()
            self.assertEqual(order["status"], "Запланировано")
            self.assertIn("не отправлена", order["note"])
            self.assertEqual((item["quantity"], item["unit"], item["material_key"]), (6.0, "м", "fabric-a"))
            self.assertEqual(before, (0, 0, 0, 0))
            after_confirm = tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("procurement_orders", "procurement_items", "inventory_movements", "material_cost_layers"))
            self.assertEqual(after_confirm, (1, 1, 0, 0))
            # A supplier quote and delivery cost are entered on the internal PO before receipt.
            conn.execute("UPDATE procurement_items SET supplier_unit_price=120,delivery_unit_foreign=2,extra_unit_rub=5 WHERE id=?", (item["id"],))
            item_id = int(item["id"])

        receipt = procurement.post_procurement_receipt(order_id, pd.DataFrame([{"item_id": item_id, "receive_now": 6}]))
        self.assertEqual(receipt["errors"], [])
        self.assertEqual(receipt["posted"], 6)
        with connect() as conn:
            order = conn.execute("SELECT status FROM procurement_orders WHERE id=?", (order_id,)).fetchone()
            movement = conn.execute("SELECT movement_type,material_key,material_delta,unit_cost_rub FROM inventory_movements WHERE procurement_order_id=?", (order_id,)).fetchone()
            movement_id = conn.execute("SELECT id FROM inventory_movements WHERE procurement_order_id=?", (order_id,)).fetchone()["id"]
            layer = conn.execute("SELECT material_key,source_type,source_ref,original_meters,remaining_meters,unit_cost_rub_m,original_amount_rub FROM material_cost_layers WHERE source_ref=?", (f"movement:{movement_id}",)).fetchone()
            inventory = conn.execute("SELECT full_rolls,partial_meters,roll_length,balance_known FROM material_inventory_color WHERE material_key='fabric-a'").fetchone()
        self.assertEqual(order["status"], "Получено")
        self.assertEqual(movement["movement_type"], "procurement_material_receipt")
        self.assertEqual(movement["material_key"], "fabric-a")
        self.assertEqual(movement["material_delta"], 6.0)
        self.assertEqual(movement["unit_cost_rub"], 127.0)
        self.assertEqual(tuple(layer), ("fabric-a", "procurement", f"movement:{movement_id}", 6.0, 6.0, 127.0, 762.0))
        self.assertEqual(inventory["full_rolls"] * inventory["roll_length"] + inventory["partial_meters"], 6.0)
        self.assertEqual(inventory["balance_known"], 1)

    def test_concurrent_same_key_confirm_creates_one_order_set_and_returns_same_ids(self):
        with connect() as conn:
            self._seed_exact_variant_plan(conn)
            preview = build_supply_preview(load_supply_plan_snapshot(conn, "2026-09-01", "2026-09-30"))
        key = "00000000-0000-4000-8000-000000000102"
        barrier = Barrier(2)

        def confirm_once():
            with connect() as conn:
                # Both callers confirm the same source snapshot and request concurrently.
                barrier.wait(timeout=5)
                return confirm_supply_plan(conn, key, "request-102", preview["preview_fingerprint"], preview, [{"material_key": "fabric-a", "unit": "м", "quantity": 6, "supplier_id": 1, "expected_date": "2026-09-10"}])

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: confirm_once(), range(2)))
        self.assertEqual(results[0]["order_ids"], results[1]["order_ids"])
        self.assertEqual(sorted(result["replayed"] for result in results), [False, True])
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM supply_plan_confirmations WHERE confirmation_key=?", (key,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM procurement_orders WHERE source_key LIKE ?", (f"supply-plan:{key}:%",)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM procurement_items WHERE order_id=?", (results[0]["order_ids"][0],)).fetchone()[0], 1)
