from __future__ import annotations

from db import wip
from db.core import connect
from db.fifo_materials import _create_material_layer
from db.production import close_production_shift

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

    def test_fifo_consumes_only_exact_identity_and_reversal_restores_exact_batch(self) -> None:
        exact = self._batch("exact", 100, 501, 3, "2026-01-01")
        self._batch("other-variant", 100, 502, 8, "2026-01-02")
        self._batch("legacy", 0, 0, 9, "2025-01-01")
        now = self.now()
        with connect() as conn:
            movement = conn.execute(
                "INSERT INTO inventory_movements(event_key,movement_type,nm_id,chrt_id,quantity,movement_date,status,created_at) VALUES('pack-a','production_receipt_wip',100,501,2,'2026-01-03','applied',?)",
                (now,),
            ).lastrowid
            cost, allocations = wip._consume_wip_fifo(conn, int(movement), "canvas", "Canvas", "blank-A", 2, 100, 501)
            self.assertEqual(cost, 6)
            self.assertEqual([(a["batch_id"], a["units"]) for a in allocations], [(exact, 2)])
            wip._reverse_wip_allocations(conn, int(movement))
            self.assertEqual(conn.execute("SELECT remaining_units FROM wip_blank_batches WHERE id=?", (exact,)).fetchone()[0], 3)
            wip._reverse_wip_allocations(conn, int(movement))
            self.assertEqual(conn.execute("SELECT remaining_units FROM wip_blank_batches WHERE id=?", (exact,)).fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wip_blank_allocations WHERE movement_id=? AND status='reversed'", (movement,)).fetchone()[0], 1)

    def test_variant_shortage_never_consumes_other_variant_or_legacy_rows(self) -> None:
        self._batch("exact", 100, 501, 3, "2026-01-01")
        self._batch("other-variant", 100, 502, 8, "2026-01-02")
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
            values = {str(row[0]): tuple(row[1:]) for row in conn.execute("SELECT batch_key,nm_id,chrt_id,remaining_units FROM wip_blank_batches")}
            self.assertEqual(values, {"exact": (100, 501, 3), "other-variant": (100, 502, 8), "legacy": (0, 0, 9)})
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wip_blank_allocations").fetchone()[0], 0)
            self.assertIsNone(conn.execute("SELECT id FROM inventory_movements WHERE event_key='pack-short'").fetchone())

    def test_legacy_wip_is_available_only_for_base_product_identity(self) -> None:
        self._batch("legacy", 0, 0, 9, "2025-01-01")
        with connect() as conn:
            self.assertEqual(wip._wip_available_units(conn, "canvas", "blank-A", 0, 0), 9)
            self.assertEqual(wip._wip_available_units(conn, "canvas", "blank-A", 100, 0), 9)
            self.assertEqual(wip._wip_available_units(conn, "canvas", "blank-A", 100, 501), 0)

    def test_base_product_uses_exact_rows_before_generic_legacy_fallback(self) -> None:
        exact = self._batch("base", 100, 0, 2, "2026-01-01")
        legacy = self._batch("legacy", 0, 0, 9, "2025-01-01")
        with connect() as conn:
            movement = conn.execute("INSERT INTO inventory_movements(event_key,movement_type,nm_id,chrt_id,quantity,movement_date,status,created_at) VALUES('base-package','production_receipt_wip',100,0,3,'2026-01-03','applied',?)", (self.now(),)).lastrowid
            _, allocations = wip._consume_wip_fifo(conn, int(movement), "canvas", "Canvas", "blank-A", 3, 100, 0)
        self.assertEqual([(a["batch_id"], a["units"]) for a in allocations], [(exact, 2), (legacy, 1)])

    def test_init_db_migration_is_idempotent_and_preserves_legacy_links(self) -> None:
        with connect() as conn:
            now = self.now()
            issue = conn.execute(
                "INSERT INTO inventory_movements(event_key,movement_type,nm_id,chrt_id,quantity,movement_date,status,created_at) VALUES('migration-issue','wip_material_issue',0,0,0,'2026-01-01','applied',?)",
                (now,),
            ).lastrowid
            completion = conn.execute(
                "INSERT INTO inventory_movements(event_key,movement_type,nm_id,chrt_id,quantity,movement_date,status,created_at) VALUES('migration-completion','wip_blank_receipt',0,0,5,'2026-01-02','applied',?)",
                (now,),
            ).lastrowid
            conn.execute("INSERT INTO wip_blank_batches(batch_key,issue_movement_id,completion_movement_id,batch_date,material_key,material_name,blank_type,issued_meters,material_cost_rub,produced_units,scrap_units,remaining_units,unit_cost_rub,status,note,created_at,updated_at) VALUES('legacy-migration',?,?,'2026-01-01','canvas','Canvas','blank-A',2,20,5,1,4,4,'active','preserve',?,?)", (issue, completion, now, now))
            batch = conn.execute("SELECT id FROM wip_blank_batches WHERE batch_key='legacy-migration'").fetchone()[0]
            conn.execute("INSERT INTO wip_blank_allocations(movement_id,batch_id,units,amount_rub,status,created_at) VALUES(?,?,1,4,'applied',?)", (completion, batch, now))
            # Simulate the precise legacy layout and a pre-identity FIFO index.
            conn.execute("DROP INDEX idx_wip_blank_batches_fifo")
            conn.execute("CREATE INDEX idx_wip_blank_batches_fifo ON wip_blank_batches(material_key,blank_type,status,batch_date,id)")
            conn.execute("ALTER TABLE wip_blank_batches DROP COLUMN nm_id")
            conn.execute("ALTER TABLE wip_blank_batches DROP COLUMN chrt_id")

        from db.core import init_db
        init_db()
        init_db()
        with connect() as conn:
            row = conn.execute("SELECT * FROM wip_blank_batches WHERE batch_key='legacy-migration'").fetchone()
            allocation = conn.execute("SELECT movement_id,batch_id,units,amount_rub,status FROM wip_blank_allocations").fetchone()
            index_columns = [str(row[2]) for row in conn.execute("PRAGMA index_info(idx_wip_blank_batches_fifo)").fetchall()]
            foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        self.assertEqual((row["nm_id"], row["chrt_id"]), (0, 0))
        self.assertEqual((row["issue_movement_id"], row["completion_movement_id"], row["status"], row["issued_meters"], row["material_cost_rub"], row["produced_units"], row["remaining_units"], row["note"]), (issue, completion, "active", 2, 20, 5, 4, "preserve"))
        self.assertEqual(tuple(allocation), (completion, batch, 1, 4, "applied"))
        self.assertEqual(index_columns, ["material_key", "blank_type", "status", "nm_id", "chrt_id", "batch_date", "id"])
        self.assertEqual(foreign_key_errors, [])

    def test_fifo_index_is_rebuilt_when_identity_columns_are_in_the_wrong_order(self) -> None:
        from db.core import init_db
        with connect() as conn:
            conn.execute("DROP INDEX idx_wip_blank_batches_fifo")
            conn.execute("CREATE INDEX idx_wip_blank_batches_fifo ON wip_blank_batches(material_key,blank_type,status,chrt_id,nm_id,batch_date,id)")
        init_db()
        with connect() as conn:
            columns = [str(row[2]) for row in conn.execute("PRAGMA index_info(idx_wip_blank_batches_fifo)").fetchall()]
        self.assertEqual(columns, ["material_key", "blank_type", "status", "nm_id", "chrt_id", "batch_date", "id"])

    def test_fifo_index_is_created_when_missing(self) -> None:
        from db.core import init_db
        with connect() as conn:
            conn.execute("DROP INDEX idx_wip_blank_batches_fifo")
        init_db()
        with connect() as conn:
            columns = [str(row[2]) for row in conn.execute("PRAGMA index_info(idx_wip_blank_batches_fifo)").fetchall()]
        self.assertEqual(columns, ["material_key", "blank_type", "status", "nm_id", "chrt_id", "batch_date", "id"])

    def test_issue_retry_with_changed_variant_is_conflict_and_has_no_second_movement(self) -> None:
        with connect() as conn:
            self.set_material_physical_stock(conn, "canvas", "Canvas", full_rolls=1, roll_length=25)
            conn.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES(100,'A','A','{\"sizes\":[{\"chrtID\":501},{\"chrtID\":502}]}')")
            conn.execute("INSERT INTO production_settings(nm_id,supplier_article,enabled,material_name,blank_type,pack_size) VALUES(100,'A',1,'Canvas','blank-A',2)")
        first = wip.issue_wip_material("2026-01-01", "Canvas", "blank-A", 2, source_key="stable-key", nm_id=100, chrt_id=501)
        retry = wip.issue_wip_material("2026-01-01", "Canvas", "blank-A", 2, source_key="stable-key", nm_id=100, chrt_id=502)
        self.assertEqual(first["errors"], [])
        self.assertEqual(len(retry["errors"]), 1)
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wip_blank_batches WHERE batch_key='stable-key'").fetchone()[0], 1)
            movement = conn.execute("SELECT nm_id,chrt_id FROM inventory_movements WHERE source_task_key='stable-key'").fetchone()
            self.assertEqual(tuple(movement), (100, 501))

    def test_issue_rejects_unknown_catalog_variant_before_movement(self) -> None:
        with connect() as conn:
            self.set_material_physical_stock(conn, "canvas", "Canvas", full_rolls=1, roll_length=25)
            conn.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES(100,'A','A','{\"sizes\":[{\"chrtID\":501}]}')")
        result = wip.issue_wip_material("2026-01-01", "Canvas", "blank-A", 2, source_key="unknown-size", nm_id=100, chrt_id=502)
        self.assertTrue(result["errors"])
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wip_blank_batches").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM inventory_movements WHERE source_task_key='unknown-size'").fetchone()[0], 0)

    def test_immediate_issue_and_completion_wrapper_preserves_identity(self) -> None:
        with connect() as conn:
            self.set_material_physical_stock(conn, "canvas", "Canvas", full_rolls=1, roll_length=25)
            _create_material_layer(conn, "canvas", "Canvas", "opening", "wrapper-layer", "2026-01-01", 25, 10)
            conn.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES(100,'A','A','{\"sizes\":[{\"chrtID\":501}]}')")
            conn.execute("INSERT INTO production_settings(nm_id,supplier_article,enabled,material_name,blank_type,pack_size) VALUES(100,'A',1,'Canvas','blank-A',2)")
        result = wip.post_wip_blank_batch("2026-01-02", "Canvas", "blank-A", 5, 4, source_key="wrapper-variant", nm_id=100, chrt_id=501)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["posted"], 2)
        with connect() as conn:
            movements = [tuple(row) for row in conn.execute("SELECT movement_type,nm_id,chrt_id FROM inventory_movements WHERE source_task_key IN ('wrapper-variant',?) ORDER BY movement_type", (f"wip_batch:{result['batch_id']}:completion",))]
            batch = conn.execute("SELECT nm_id,chrt_id FROM wip_blank_batches WHERE id=?", (result["batch_id"],)).fetchone()
        self.assertEqual(movements, [("wip_blank_receipt", 100, 501), ("wip_material_issue", 100, 501)])
        self.assertEqual(tuple(batch), (100, 501))

    def test_variant_task_close_requires_and_consumes_the_matching_variant(self) -> None:
        now = self.now()
        with connect() as conn:
            self.set_material_physical_stock(conn, "canvas", "Canvas", full_rolls=1, roll_length=25)
            _create_material_layer(conn, "canvas", "Canvas", "opening", "variant-test-layer", "2026-01-01", 25, 10)
            conn.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES(100,'A','A','{\"sizes\":[{\"chrtID\":501},{\"chrtID\":502}]}')")
            conn.execute("INSERT INTO production_settings(nm_id,supplier_article,enabled,material_name,blank_type,pack_size) VALUES(100,'A',1,'Canvas','blank-A',2)")
            conn.execute("INSERT INTO costs(nm_id,packaging_cost_rub,packaging_cost_status,packaging_cost_source,packaging_cost_confirmed_at) VALUES(100,0,'confirmed','manual',?)", (now,))
            conn.execute("INSERT INTO manufacturing_boms(nm_id,chrt_id,version,materials_json,operations_json,packaging_per_unit,other_per_unit,note,created_at) VALUES(100,503,1,'[]','[]',0,0,'other variant','2026-01-01')")
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

    def test_issue_rejects_bom_mismatch_without_stock_or_fifo_debit(self) -> None:
        with connect() as conn:
            self.set_material_physical_stock(conn, "canvas", "Canvas", full_rolls=1, roll_length=25)
            _create_material_layer(conn, "canvas", "Canvas", "opening", "guard-layer", "2026-01-01", 25, 10)
            conn.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES(100,'A','A','{\"sizes\":[{\"chrtID\":501}]}')")
            conn.execute("INSERT INTO production_settings(nm_id,supplier_article,enabled,material_name,blank_type,pack_size) VALUES(100,'A',1,'Other material','blank-A',2)")
        result = wip.issue_wip_material("2026-01-02", "Canvas", "blank-A", 5, source_key="guard", nm_id=100, chrt_id=501)
        self.assertTrue(result["errors"])
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT remaining_meters FROM material_cost_layers WHERE source_ref='guard-layer'").fetchone()[0], 25)
            self.assertEqual(conn.execute("SELECT full_rolls FROM material_inventory_color WHERE material_key='canvas'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM inventory_movements WHERE source_task_key='guard'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wip_blank_batches").fetchone()[0], 0)

    def test_exact_issue_retry_still_skips_after_saved_bom_changes(self) -> None:
        with connect() as conn:
            self.set_material_physical_stock(conn, "canvas", "Canvas", full_rolls=1, roll_length=25)
            _create_material_layer(conn, "canvas", "Canvas", "opening", "retry-layer", "2026-01-01", 25, 10)
            conn.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES(100,'A','A','{\"sizes\":[{\"chrtID\":501}]}')")
            conn.execute("INSERT INTO production_settings(nm_id,supplier_article,enabled,material_name,blank_type,pack_size) VALUES(100,'A',1,'Canvas','blank-A',2)")
        first = wip.issue_wip_material("2026-01-02", "Canvas", "blank-A", 5, source_key="retry", nm_id=100, chrt_id=501)
        self.assertEqual(first["errors"], [])
        with connect() as conn:
            conn.execute("UPDATE production_settings SET material_name='Changed' WHERE nm_id=100")
        again = wip.issue_wip_material("2026-01-02", "Canvas", "blank-A", 5, source_key="retry", nm_id=100, chrt_id=501)
        self.assertEqual(again["errors"], [])
        self.assertEqual(again["skipped"], 1)
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT remaining_meters FROM material_cost_layers WHERE source_ref='retry-layer'").fetchone()[0], 20)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wip_blank_batches").fetchone()[0], 1)
