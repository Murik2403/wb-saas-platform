from __future__ import annotations

from db import wip
from db.core import connect
from db.production import close_production_shift
from db.fifo_materials import _create_material_layer

from .base import DbTestCase


class WipVariantIdentityTests(DbTestCase):
    def _batch(self, key: str, nm_id: int, chrt_id: int, units: int, date: str) -> int:
        now = self.now()
        with connect() as conn:
            movement = conn.execute(
                "INSERT INTO inventory_movements(event_key,movement_type,nm_id,chrt_id,quantity,movement_date,status,created_at) VALUES(?, 'wip_blank_receipt', ?, ?, ?, ?, 'applied', ?)",
                (f"seed:{key}", nm_id, chrt_id, units, date, now),
            ).lastrowid
            issue = conn.execute(
                "INSERT INTO inventory_movements(event_key,movement_type,nm_id,chrt_id,quantity,movement_date,status,created_at) VALUES(?, 'wip_material_issue', ?, ?, 0, ?, 'applied', ?)",
                (f"issue:{key}", nm_id, chrt_id, date, now),
            ).lastrowid
            return int(conn.execute(
                "INSERT INTO wip_blank_batches(batch_key,issue_movement_id,completion_movement_id,batch_date,nm_id,chrt_id,material_key,material_name,blank_type,issued_meters,material_cost_rub,produced_units,scrap_units,remaining_units,unit_cost_rub,status,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,1,?, ?,0,?, ?, 'active','',?,?)",
                (key, issue, movement, date, nm_id, chrt_id, "canvas", "Canvas", "blank-A", 1, units, units, 3, now, now),
            ).lastrowid)

    def test_fifo_consumes_only_exact_nm_and_chrt_and_restores_exact_batch(self) -> None:
        variant_a = self._batch("a", 100, 501, 3, "2026-01-01")
        self._batch("b", 100, 502, 8, "2026-01-02")
        self._batch("legacy", 0, 0, 9, "2025-01-01")
        now = self.now()
        with connect() as conn:
            movement = conn.execute(
                "INSERT INTO inventory_movements(event_key,movement_type,nm_id,chrt_id,quantity,movement_date,status,created_at) VALUES('pack-a','production_receipt_wip',100,501,2,'2026-01-03','applied',?)",
                (now,),
            ).lastrowid
            cost, allocations = wip._consume_wip_fifo(conn, int(movement), "canvas", "Canvas", "blank-A", 2, 100, 501)
            self.assertEqual(cost, 6)
            self.assertEqual([(a["batch_id"], a["units"]) for a in allocations], [(variant_a, 2)])
            wip._reverse_wip_allocations(conn, int(movement))
            self.assertEqual(conn.execute("SELECT remaining_units FROM wip_blank_batches WHERE id=?", (variant_a,)).fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wip_blank_allocations WHERE movement_id=? AND status='reversed'", (movement,)).fetchone()[0], 1)

    def test_shortage_of_exact_variant_never_falls_back_to_other_or_legacy_and_rolls_back(self) -> None:
        self._batch("a", 100, 501, 3, "2026-01-01")
        self._batch("b", 100, 502, 8, "2026-01-02")
        self._batch("legacy", 0, 0, 9, "2025-01-01")
        with connect() as conn:
            conn.execute("SAVEPOINT tested_operation")
            movement = conn.execute(
                "INSERT INTO inventory_movements(event_key,movement_type,nm_id,chrt_id,quantity,movement_date,status,created_at) VALUES('pack-short','production_receipt_wip',100,501,4,'2026-01-03','applied',?)",
                (self.now(),),
            ).lastrowid
            with self.assertRaisesRegex(ValueError, "доступно 3 шт., требуется 4 шт"):
                wip._consume_wip_fifo(conn, int(movement), "canvas", "Canvas", "blank-A", 4, 100, 501)
            conn.execute("ROLLBACK TO SAVEPOINT tested_operation")
            conn.execute("RELEASE SAVEPOINT tested_operation")
            values = [tuple(row) for row in conn.execute("SELECT nm_id,chrt_id,remaining_units FROM wip_blank_batches ORDER BY batch_key")]
            self.assertEqual(values, [(100, 501, 3), (100, 502, 8), (0, 0, 9)])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wip_blank_allocations").fetchone()[0], 0)
            self.assertIsNone(conn.execute("SELECT id FROM inventory_movements WHERE event_key='pack-short'").fetchone())

    def test_legacy_rows_are_available_only_to_base_identity(self) -> None:
        self._batch("legacy", 0, 0, 9, "2025-01-01")
        with connect() as conn:
            self.assertEqual(wip._wip_available_units(conn, "canvas", "blank-A", 0, 0), 9)
            self.assertEqual(wip._wip_available_units(conn, "canvas", "blank-A", 100, 0), 9)
            self.assertEqual(wip._wip_available_units(conn, "canvas", "blank-A", 100, 501), 0)

    def test_base_product_consumes_its_exact_batch_before_generic_legacy_fallback(self) -> None:
        exact = self._batch("base", 100, 0, 2, "2026-01-01")
        legacy = self._batch("legacy", 0, 0, 9, "2025-01-01")
        with connect() as conn:
            movement = conn.execute("INSERT INTO inventory_movements(event_key,movement_type,nm_id,chrt_id,quantity,movement_date,status,created_at) VALUES('base-package','production_receipt_wip',100,0,3,'2026-01-03','applied',?)", (self.now(),)).lastrowid
            _, allocations = wip._consume_wip_fifo(conn, int(movement), "canvas", "Canvas", "blank-A", 3, 100, 0)
        self.assertEqual([(a["batch_id"], a["units"]) for a in allocations], [(exact, 2), (legacy, 1)])

    def test_init_db_migrates_populated_legacy_batch_without_losing_links(self) -> None:
        with connect() as conn:
            now = self.now()
            movement = conn.execute(
                "INSERT INTO inventory_movements(event_key,movement_type,nm_id,chrt_id,quantity,movement_date,status,created_at) VALUES('migration-movement','wip_material_issue',0,0,0,'2026-01-01','applied',?)",
                (now,),
            ).lastrowid
            package = conn.execute(
                "INSERT INTO inventory_movements(event_key,movement_type,nm_id,chrt_id,quantity,movement_date,status,created_at) VALUES('migration-package','production_receipt_wip',0,0,1,'2026-01-02','applied',?)",
                (now,),
            ).lastrowid
            conn.execute("INSERT INTO wip_blank_batches(batch_key,issue_movement_id,batch_date,material_key,material_name,blank_type,issued_meters,material_cost_rub,produced_units,scrap_units,remaining_units,unit_cost_rub,status,note,created_at,updated_at) VALUES('legacy-migration',?,'2026-01-01','canvas','Canvas','blank-A',2,20,5,1,4,4,'active','keep me',?,?)", (movement, now, now))
            batch = conn.execute("SELECT id FROM wip_blank_batches WHERE batch_key='legacy-migration'").fetchone()[0]
            conn.execute("INSERT INTO wip_blank_allocations(movement_id,batch_id,units,amount_rub,status,created_at) VALUES(?,?,1,4,'applied',?)", (package, batch, now))
            conn.execute("DROP INDEX idx_wip_blank_batches_fifo")
            conn.execute("CREATE INDEX idx_wip_blank_batches_fifo ON wip_blank_batches(material_key,blank_type,status,batch_date,id)")
            conn.execute("ALTER TABLE wip_blank_batches DROP COLUMN nm_id")
            conn.execute("ALTER TABLE wip_blank_batches DROP COLUMN chrt_id")
        from db.core import init_db
        init_db()
        with connect() as conn:
            row = conn.execute("SELECT * FROM wip_blank_batches WHERE batch_key='legacy-migration'").fetchone()
            index = conn.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_wip_blank_batches_fifo'").fetchone()[0]
            allocation = conn.execute("SELECT movement_id,batch_id,units,amount_rub,status FROM wip_blank_allocations").fetchone()
        self.assertEqual((row["nm_id"], row["chrt_id"]), (0, 0))
        self.assertEqual((row["issue_movement_id"], row["issued_meters"], row["material_cost_rub"], row["note"]), (movement, 2, 20, "keep me"))
        self.assertEqual(tuple(allocation), (package, batch, 1, 4, "applied"))
        self.assertIn("nm_id,chrt_id,batch_date,id", index.replace(" ", ""))

    def test_variant_task_close_fails_atomically_on_cross_variant_shortage(self) -> None:
        now = self.now()
        with connect() as conn:
            self.set_material_physical_stock(conn, "canvas", "Canvas", full_rolls=1, roll_length=25)
            _create_material_layer(conn, "canvas", "Canvas", "opening", "task-layer", "2026-01-01", 25, 10)
            conn.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES(100,'A','A','{\"sizes\":[{\"chrtID\":501},{\"chrtID\":502}]}')")
            conn.execute("INSERT INTO production_settings(nm_id,supplier_article,enabled,material_name,blank_type,pack_size) VALUES(100,'A',1,'Canvas','blank-A',2)")
            conn.execute("INSERT INTO manufacturing_boms(nm_id,chrt_id,version,materials_json,operations_json,packaging_per_unit,other_per_unit,note,created_at) VALUES(100,503,1,'[]','[]',0,0,'other variant','2026-01-01')")
            conn.execute("INSERT INTO costs(nm_id,packaging_cost_rub,packaging_cost_status,packaging_cost_source,packaging_cost_confirmed_at) VALUES(100,0,'confirmed','manual',?)", (now,))
        issued = wip.issue_wip_material("2026-01-02", "Canvas", "blank-A", 5, source_key="task-wip", nm_id=100, chrt_id=501)
        self.assertEqual(issued["errors"], [])
        self.assertEqual(wip.complete_wip_blank_batch(issued["batch_id"], 4)["errors"], [])
        with connect() as conn:
            conn.execute("INSERT INTO execution_tasks(task_key,task_type,task_date,nm_id,chrt_id,supplier_article,product_name,actual_units,status) VALUES('task-variant','production','2026-01-03',100,502,'A','A',1,'Упаковано')")
        failed = close_production_shift("2026-01-03")
        self.assertTrue(failed["errors"])
        self.assertEqual(failed["posted"], 0)
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM inventory_movements WHERE movement_type='production_receipt_wip'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM production_cost_batches").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wip_blank_allocations").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT remaining_units FROM wip_blank_batches WHERE id=?", (issued["batch_id"],)).fetchone()[0], 4)
            conn.execute("UPDATE execution_tasks SET chrt_id=501 WHERE task_key='task-variant'")
        posted = close_production_shift("2026-01-03")
        self.assertEqual(posted["errors"], [])
        self.assertEqual(posted["posted"], 1)
        with connect() as conn:
            movement = conn.execute("SELECT nm_id,chrt_id,quantity FROM inventory_movements WHERE movement_type='production_receipt_wip'").fetchone()
            cost = conn.execute("SELECT nm_id,chrt_id FROM production_cost_batches").fetchone()
            layer = conn.execute("SELECT nm_id,chrt_id FROM finished_goods_cost_layers WHERE source_type='production_wip'").fetchone()
            allocation = conn.execute("SELECT b.nm_id,b.chrt_id FROM wip_blank_allocations a JOIN wip_blank_batches b ON b.id=a.batch_id").fetchone()
        self.assertEqual(tuple(movement), (100, 501, 1))
        self.assertEqual(tuple(cost), (100, 501))
        self.assertEqual(tuple(layer), (100, 501))
        self.assertEqual(tuple(allocation), (100, 501))
