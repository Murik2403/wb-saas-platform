from __future__ import annotations

import sqlite3
import pandas as pd

from db.core import connect, init_db
from db.fifo_materials import apply_forecast_costs, save_auto_cost_settings
from db.fifo_materials import _create_material_layer
from db.inventory_movements import undo_inventory_movement
from db import manufacturing as m
from db.production import close_production_shift
from db.wip import post_manual_wip_packaging, post_wip_blank_batch
from .base import DbTestCase


class ManufacturingTests(DbTestCase):
    def test_volume_length_and_inverse_conversion_release_fifo_in_base_units(self):
        for index,(base,entered,amount,canonical) in enumerate([('л','мл',250,0.25),('м','см',25,0.25),('г','кг',0.0005,0.5)]):
            name=f'Материал {index}'
            self.seed(name,base,2,100)
            bom=m.save_bom_version(123,[{'material_name':name,'quantity_per_unit':amount,'unit':entered}])
            release=self.release(key=f'conversion-{index}',quantity=2,bom_version_id=bom['id'])
            self.assertEqual(bom['materials'][0]['quantity_per_unit'],canonical)
            self.assertEqual(release['material_cost_rub'],canonical*200)
            self.assertEqual(self.physical(name),2-canonical*2)

    def test_converted_bom_consumes_catalog_fifo_and_keeps_old_recipe_snapshot(self):
        self.seed('Нить','кг',2,100)
        old=m.save_bom_version(123,[{'material_name':'Нить','quantity_per_unit':0.25,'unit':'кг'}])
        bom=m.save_bom_version(123,[{'material_name':'Нить','quantity_per_unit':125,'unit':'г'}])
        component=bom['materials'][0]
        self.assertEqual(component['quantity_per_unit'],0.125)
        self.assertEqual(component['unit'],'кг')
        self.assertEqual(component['entered_quantity_per_unit'],'125')
        self.assertEqual(component['entered_unit'],'г')
        self.assertEqual(component['conversion_factor'],'0.001')
        release=self.release(quantity=4,bom_version_id=bom['id'])
        self.assertEqual(release['material_cost_rub'],50)
        self.assertEqual(self.physical('Нить'),1.5)
        self.assertEqual(m.list_bom_versions(123)[1],old)
        self.assertEqual(self.release(quantity=4,bom_version_id=bom['id'])['posted'],0)
        self.assertEqual(self.physical('Нить'),1.5)

    def test_base_bounds_apply_after_conversion_and_incompatible_save_is_atomic(self):
        self.seed('Нить','г',20,2)
        bom=m.save_bom_version(123,[{'material_name':'Нить','quantity_per_unit':0.0005,'unit':'кг'}])
        self.assertEqual(bom['materials'][0]['quantity_per_unit'],0.5)
        for unit,qty in [('л',1),('кг',0.000000001),('кг',1000000000000)]:
            with self.assertRaises(ValueError):
                m.save_bom_version(123,[{'material_name':'Нить','quantity_per_unit':qty,'unit':unit}])
        self.assertEqual(len(m.list_bom_versions(123)),1)
        self.assertEqual(self.physical('Нить'),20)

    def seed(self, name="Ткань", unit="м", quantity=20, rate=10, source_suffix=""):
        with connect() as conn:
            self.set_material_physical_stock(conn,name.casefold(),name,partial_meters=quantity,roll_length=1)
            conn.execute("UPDATE material_inventory_color SET unit=?,tracking_mode='quantity' WHERE material_key=?",(unit,name.casefold()))
            return _create_material_layer(conn,name.casefold(),name,"opening","test:"+name+source_suffix,"2026-09-01",quantity,rate)

    def bom(self, **kwargs):
        return m.save_bom_version(123,[{"material_name":"Ткань","quantity_per_unit":2,"unit":"м"},{"material_name":"Нить","quantity_per_unit":0.1,"unit":"кг"}],**kwargs)

    def release(self, key="job-1", quantity=3, **kwargs):
        return m.post_manufacturing_release(123,quantity,"2026-09-23",key,**kwargs)

    def physical(self, name):
        return next(r["quantity"] for r in m.list_material_catalog() if r["material_name"] == name)

    def test_multi_material_actual_labor_and_equipment_snapshot(self):
        self.seed()
        self.seed("Нить","кг",10,100)
        eq = m.save_equipment("Машина",1000,100,90,"hours")
        bom = self.bom(operations=[{"name":"Пошив","rate_per_unit":5,"equipment_id":eq["id"],"equipment_usage_per_unit":0.5}],packaging_per_unit=0,other_per_unit=1)
        labor = m.post_piecework("job-1",123,"Пошив","Анна",3,7,"2026-09-23","labor-1")
        r = self.release()
        # Materials 6m*10 + .3kg*100 = 90; actual labor 21, wear 15, other 3.
        self.assertEqual(r["material_cost_rub"],90)
        self.assertEqual(r["labor_cost_rub"],21)
        self.assertEqual(r["depreciation_cost_rub"],15)
        self.assertEqual(r["packaging_cost_rub"],0)
        self.assertEqual(r["total_cost_rub"],129)
        self.assertEqual(r["unit_cost_rub"],43)
        self.assertEqual(r["labor"][0]["worker"],"Анна")
        self.assertEqual(m.list_equipment()[0]["consumed_resource"],1.5)
        self.assertEqual(m.list_piecework()[0]["release_movement_id"],r["movement_id"])
        next_bom = self.bom(operations=[{"name":"Пошив","rate_per_unit":100}])
        self.assertEqual(next_bom["version"],2)
        self.assertEqual(m.list_manufacturing_releases()[0]["bom_version_id"],bom["id"])
        self.assertEqual(m.list_manufacturing_releases()[0]["unit_cost_rub"],43)
        self.assertEqual(self.physical("Ткань"),14)
        self.assertAlmostEqual(self.physical("Нить"),9.7)
        self.assertEqual(self.release()["posted"],0)  # retry after BOM edit still same job
        self.assertEqual(m.post_piecework("job-1",123,"Пошив","Анна",3,7,"2026-09-23","labor-1")["id"],labor["id"])

    def test_bom_versions_are_scoped_to_variant_and_exact_variant_wins(self):
        self.seed()
        legacy = m.save_bom_version(123,[{"material_name":"Ткань","quantity_per_unit":1,"unit":"м"}])
        m.save_bom_version(123,[{"material_name":"Ткань","quantity_per_unit":2,"unit":"м"}],chrt_id=111)
        m.save_bom_version(123,[{"material_name":"Ткань","quantity_per_unit":3,"unit":"м"}],chrt_id=222)
        newest = m.save_bom_version(123,[{"material_name":"Ткань","quantity_per_unit":4,"unit":"м"}],chrt_id=111)
        self.assertEqual((legacy["version"], newest["version"]), (1, 2))
        release = self.release(key="variant-release", quantity=1, chrt_id=111)
        self.assertEqual(release["bom_version_id"], newest["id"])
        self.assertEqual(release["chrt_id"], 111)
        self.assertEqual(release["material_cost_rub"], 40)
        with connect() as conn:
            layer = conn.execute("SELECT chrt_id FROM finished_goods_cost_layers WHERE id=?", (release["finished_layer_id"],)).fetchone()
            movement = conn.execute("SELECT chrt_id FROM inventory_movements WHERE id=?", (release["movement_id"],)).fetchone()
            batch = conn.execute("SELECT chrt_id FROM production_cost_batches WHERE movement_id=?", (release["movement_id"],)).fetchone()
        self.assertEqual((layer["chrt_id"], movement["chrt_id"], batch["chrt_id"]), (111, 111, 111))

    def test_old_bom_schema_migrates_to_legacy_variant_zero(self):
        with connect() as conn:
            conn.execute("DROP TABLE manufacturing_boms")
            conn.execute("""CREATE TABLE manufacturing_boms (
                id INTEGER PRIMARY KEY,nm_id INTEGER NOT NULL,version INTEGER NOT NULL,
                materials_json TEXT NOT NULL,operations_json TEXT NOT NULL,
                packaging_per_unit REAL NOT NULL,other_per_unit REAL NOT NULL,
                note TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(nm_id,version))""")
            conn.execute("INSERT INTO manufacturing_boms VALUES (1,123,1,'[]','[]',0,0,'legacy','2026-09-01')")
            m.init_manufacturing_schema(conn)
            migrated = conn.execute("SELECT chrt_id,version,note FROM manufacturing_boms WHERE id=1").fetchone()
            self.assertEqual(tuple(migrated), (0, 1, "legacy"))
            conn.execute("INSERT INTO manufacturing_boms(nm_id,chrt_id,version,materials_json,operations_json,packaging_per_unit,other_per_unit,note,created_at) VALUES (123,501,1,'[]','[]',0,0,'S','2026-09-02')")

    def test_finished_goods_layers_migrate_producer_link_and_keep_opening_provenance_unknown(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("CREATE TABLE finished_goods_cost_layers(id INTEGER PRIMARY KEY, source_type TEXT, source_ref TEXT)")
            conn.execute("CREATE TABLE manufacturing_releases(id INTEGER PRIMARY KEY, finished_layer_id INTEGER, bom_version_id INTEGER, source_key TEXT)")
            conn.execute("INSERT INTO finished_goods_cost_layers VALUES (1,'production','movement:44')")
            conn.execute("INSERT INTO finished_goods_cost_layers VALUES (2,'opening','seed:opening')")
            conn.execute("INSERT INTO finished_goods_cost_layers VALUES (3,'production','movement:45')")
            conn.execute("INSERT INTO manufacturing_releases VALUES (9,1,17,'release-9')")
            conn.execute("INSERT INTO manufacturing_releases VALUES (10,3,18,'release-10')")
            conn.execute("INSERT INTO manufacturing_releases VALUES (11,3,19,'release-11')")
            m.init_manufacturing_schema(conn)
            m.init_manufacturing_schema(conn)  # repeat initialization is safe
            migrated = conn.execute("SELECT producer_release_id FROM finished_goods_cost_layers WHERE id=1").fetchone()
            legacy = conn.execute("SELECT producer_release_id FROM finished_goods_cost_layers WHERE id=2").fetchone()
            ambiguous = conn.execute("SELECT producer_release_id FROM finished_goods_cost_layers WHERE id=3").fetchone()
            producer = conn.execute("SELECT bom_version_id FROM manufacturing_releases WHERE id=?",(migrated["producer_release_id"],)).fetchone()
            self.assertEqual(migrated["producer_release_id"],9)
            self.assertEqual(producer["bom_version_id"],17)
            self.assertIsNone(legacy["producer_release_id"])
            self.assertIsNone(ambiguous["producer_release_id"])
            self.assertIsNotNone(conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_manufacturing_releases_finished_layer'").fetchone())
            hydrated = m._hydrate_semi_finished_provenance(conn, {"materials":[{
                "component_type":"semi_finished","component_bom_version_id":17,
                "allocations":[{"layer_id":2,"units":1,"amount_rub":7}],
            }]})
            legacy_allocation = hydrated["materials"][0]["allocations"][0]
            self.assertEqual(legacy_allocation["source_provenance"], "unknown_legacy_or_external")
            self.assertIsNone(legacy_allocation["source_bom_version_mismatch"])
        finally:
            conn.close()
    def test_variant_uses_legacy_bom_only_when_exact_bom_absent(self):
        self.seed()
        legacy = m.save_bom_version(123,[{"material_name":"Ткань","quantity_per_unit":1,"unit":"м"}])
        release = self.release(key="variant-fallback", quantity=1, chrt_id=333)
        self.assertEqual(release["bom_version_id"], legacy["id"])
        self.assertEqual(release["chrt_id"], 333)
        self.assertEqual(release["material_cost_rub"], 10)

    def test_legacy_release_cannot_select_variant_specific_bom(self):
        self.seed()
        variant_bom = m.save_bom_version(123,[{"material_name":"Ткань","quantity_per_unit":1,"unit":"м"}],chrt_id=111)
        with self.assertRaisesRegex(ValueError, "другому варианту"):
            self.release(key="legacy-mismatch", quantity=1, bom_version_id=variant_bom["id"], chrt_id=0)
    def test_same_task_key_cannot_post_release_for_another_variant(self):
        self.seed()
        m.save_bom_version(123,[{"material_name":"Ткань","quantity_per_unit":1,"unit":"м"}],chrt_id=111)
        m.save_bom_version(123,[{"material_name":"Ткань","quantity_per_unit":2,"unit":"м"}],chrt_id=222)
        self.release(key="shared-task", quantity=1, chrt_id=111)
        with self.assertRaisesRegex(ValueError, "другого варианта"):
            self.release(key="shared-task", quantity=1, chrt_id=222)
        self.assertEqual({row["chrt_id"] for row in m.list_manufacturing_releases(123)}, {111})

    def _catalog_base(self, nm_id, article):
        with connect() as conn:
            conn.execute(
                "INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES (?,?,?,?)",
                (nm_id, article, article, '{"sizes":[]}'),
            )

    def test_nested_semifinished_bom_consumes_exact_fifo_and_reverses_dependency_order(self):
        self.seed("Ткань", "м", 1, 10)
        self._catalog_base(222, "BLANK")
        original_bom = m.save_bom_version(222, [{"material_name":"Ткань","quantity_per_unit":1,"unit":"м"}])
        first = m.post_manufacturing_release(222, 1, "2026-09-23", "blank-1")
        # Force the next source layer to carry a distinct historical cost.
        self.seed("Ткань", "м", 1, 20, source_suffix=":second")
        revised_bom = m.save_bom_version(222, [{"material_name":"Ткань","quantity_per_unit":1,"unit":"м"}], note="Обновлённая спецификация")
        second = m.post_manufacturing_release(222, 1, "2026-09-24", "blank-2",bom_version_id=revised_bom["id"])
        self.assertEqual(first["unit_cost_rub"],10)
        self.assertEqual(second["unit_cost_rub"],20)
        self.assertNotEqual(original_bom["id"], revised_bom["id"])
        self.assertEqual(first["bom_version_id"], original_bom["id"])
        self.assertEqual(second["bom_version_id"], revised_bom["id"])
        self._catalog_base(123, "FINAL")
        self._catalog_base(333, "SUBASSEMBLY")
        m.save_bom_version(333, [{"component_type":"semi_finished","nm_id":222,"chrt_id":0,
                                  "quantity_per_unit":2,"unit":"шт"}])
        middle = m.post_manufacturing_release(333,1,"2026-09-25","middle-1")
        m.save_bom_version(123, [{"component_type":"semi_finished","nm_id":333,"chrt_id":0,
                                  "quantity_per_unit":1,"unit":"шт"}])

        finished = m.post_manufacturing_release(123,1,"2026-09-26","final-1")
        self.assertEqual(finished["material_cost_rub"], middle["unit_cost_rub"])
        self.assertEqual(finished["unit_cost_rub"],30)
        self.assertEqual(m.post_manufacturing_release(333,1,"2026-09-25","middle-1")["posted"],0)
        self.assertEqual(m.post_manufacturing_release(123,1,"2026-09-26","final-1")["posted"],0)
        with self.assertRaises(ValueError):
            m.post_manufacturing_release(123,2,"2026-09-26","final-1")
        with connect() as conn:
            allocations = conn.execute(
                "SELECT layer_id,units,amount_rub,from_location,to_location FROM finished_goods_fifo_allocations WHERE movement_id=? ORDER BY id",
                (finished["movement_id"],),
            ).fetchall()
            middle_allocations = conn.execute(
                "SELECT layer_id,units,amount_rub FROM finished_goods_fifo_allocations WHERE movement_id=? ORDER BY id",
                (middle["movement_id"],),
            ).fetchall()
            self.assertEqual([int(row["layer_id"]) for row in allocations], [middle["finished_layer_id"]])
            self.assertEqual([int(row["layer_id"]) for row in middle_allocations], [first["finished_layer_id"], second["finished_layer_id"]])
            self.assertEqual([int(row["units"]) for row in middle_allocations], [1, 1])
            self.assertEqual(middle["materials"][0]["component_bom_version_id"], revised_bom["id"])
            source_versions = [conn.execute("SELECT bom_version_id FROM manufacturing_releases WHERE finished_layer_id=?", (int(row["layer_id"]),)).fetchone()[0] for row in middle_allocations]
            self.assertEqual(source_versions, [original_bom["id"], revised_bom["id"]])
            provenance = middle["materials"][0]["allocations"]
            self.assertEqual([row["source_release_id"] for row in provenance], [first["id"],second["id"]])
            self.assertEqual([row["source_bom_version_id"] for row in provenance], [original_bom["id"],revised_bom["id"]])
            self.assertEqual([row["source_bom_version_mismatch"] for row in provenance], [True,False])
            self.assertTrue(all(row["source_provenance"] == "manufacturing" for row in provenance))
            self.assertAlmostEqual(sum(float(row["amount_rub"]) for row in middle_allocations),middle["material_cost_rub"],places=2)
            self.assertAlmostEqual(float(allocations[0]["amount_rub"]),middle["unit_cost_rub"],places=2)
            self.assertTrue(all((row["from_location"], row["to_location"]) == ("ready", "out") for row in allocations))

        blocked = undo_inventory_movement(first["movement_id"])
        self.assertFalse(blocked["ok"])
        self.assertIn("Сначала отмените", blocked["message"])
        undone_consumer = undo_inventory_movement(finished["movement_id"])
        self.assertTrue(undone_consumer["ok"], undone_consumer)
        undone_middle = undo_inventory_movement(middle["movement_id"])
        self.assertTrue(undone_middle["ok"], undone_middle)
        with connect() as conn:
            restored = conn.execute(
                "SELECT id,ready_units,status FROM finished_goods_cost_layers WHERE id IN (?,?) ORDER BY id",
                (first["finished_layer_id"],second["finished_layer_id"]),
            ).fetchall()
            self.assertEqual([int(row["ready_units"]) for row in restored],[1,1])
            self.assertTrue(all(row["status"] == "active" for row in restored))
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM finished_goods_fifo_allocations WHERE movement_id=? AND status='applied'",
                (middle["movement_id"],),
            ).fetchone()[0],0)
            self.assertEqual(conn.execute("SELECT producer_release_id FROM finished_goods_cost_layers WHERE id=?",(first["finished_layer_id"],)).fetchone()[0],first["id"])
        undone_second = undo_inventory_movement(second["movement_id"])
        undone_first = undo_inventory_movement(first["movement_id"])
        self.assertTrue(undone_second["ok"], undone_second)
        self.assertTrue(undone_first["ok"], undone_first)
        self.assertFalse(undo_inventory_movement(finished["movement_id"])["ok"])

    def test_nested_snapshot_is_persisted_and_consumer_variant_is_preserved(self):
        self.seed()
        with connect() as conn:
            conn.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES (222,'BLANK','Blank','{\"sizes\":[{\"chrtID\":501}]}')")
            conn.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES (123,'FINAL','Final','{\"sizes\":[{\"chrtID\":999}]}')")
        m.save_bom_version(222,[{"material_name":"Ткань","quantity_per_unit":1,"unit":"м"}],chrt_id=501)
        producer = m.post_manufacturing_release(222,2,"2026-09-23","producer",chrt_id=501)
        m.save_bom_version(123,[{"component_type":"semi_finished","nm_id":222,"chrt_id":501,"quantity_per_unit":1,"unit":"шт"}],chrt_id=999)
        result = m.post_manufacturing_release(123,1,"2026-09-24","consumer",chrt_id=999)
        import json
        with connect() as conn:
            movement = conn.execute("SELECT chrt_id FROM inventory_movements WHERE id=?", (result["movement_id"],)).fetchone()
            self.assertEqual(movement[0],999)
            snapshot = json.loads(conn.execute("SELECT materials_json FROM manufacturing_releases WHERE id=?",(result["id"],)).fetchone()[0])
            self.assertEqual(snapshot[0]["allocations"][0]["source_release_id"],producer["id"])
            conn.execute("UPDATE finished_goods_cost_layers SET producer_release_id=NULL WHERE id=?",(producer["finished_layer_id"],))
        replay = m.post_manufacturing_release(123,1,"2026-09-24","consumer",chrt_id=999)
        self.assertEqual(replay["posted"],0)
        self.assertEqual(replay["materials"][0]["allocations"][0]["source_release_id"],producer["id"])
        restored = next(r for r in m.list_manufacturing_releases() if r["id"] == result["id"])
        self.assertEqual(restored["materials"][0]["allocations"][0]["source_release_id"],producer["id"])

    def test_nested_release_rejects_deleted_catalog_atomically(self):
        self.seed()
        self._catalog_base(123,"FINAL")
        self._catalog_base(222,"BLANK")
        m.save_bom_version(222,[{"material_name":"Ткань","quantity_per_unit":1,"unit":"м"}])
        m.post_manufacturing_release(222,1,"2026-09-23","producer")
        m.save_bom_version(123,[{"component_type":"semi_finished","nm_id":222,"chrt_id":0,"quantity_per_unit":1,"unit":"шт"}])
        with connect() as conn:
            conn.execute("DELETE FROM products_catalog WHERE nm_id=222")
            before = [tuple(r) for r in conn.execute("SELECT * FROM finished_goods_cost_layers")]
        with self.assertRaises(ValueError):
            m.post_manufacturing_release(123,1,"2026-09-24","blocked")
        with connect() as conn:
            self.assertEqual(before,[tuple(r) for r in conn.execute("SELECT * FROM finished_goods_cost_layers")])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM manufacturing_releases WHERE source_key='blocked'").fetchone()[0],0)

    def test_bom_rejects_nested_recipe_cycle(self):
        self.seed()
        self._catalog_base(123, "FINAL")
        self._catalog_base(222, "BLANK")
        m.save_bom_version(222, [{"material_name":"Ткань","quantity_per_unit":1,"unit":"м"}])
        m.save_bom_version(123, [{"component_type":"semi_finished","nm_id":222,"chrt_id":0,
                                  "quantity_per_unit":1,"unit":"шт"}])
        with self.assertRaisesRegex(ValueError, "цикл"):
            m.save_bom_version(222, [{"component_type":"semi_finished","nm_id":123,"chrt_id":0,
                                      "quantity_per_unit":1,"unit":"шт"}])

    def test_nested_shortage_is_atomic_and_variant_layers_never_cross(self):
        self._catalog_base(123, "FINAL")
        layer = self.seed("Ткань", "м", 20, 10)
        with connect() as conn:
            conn.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES (222,'BLANK','Заготовка','{\"sizes\":[{\"chrtID\":501},{\"chrtID\":502}]}')")
        m.save_bom_version(222,[{"material_name":"Ткань","quantity_per_unit":1,"unit":"м"}],chrt_id=501)
        m.save_bom_version(222,[{"material_name":"Ткань","quantity_per_unit":1,"unit":"м"}],chrt_id=502)
        m.save_bom_version(123,[
            {"material_name":"Ткань","quantity_per_unit":1,"unit":"м"},
            {"component_type":"semi_finished","nm_id":222,"chrt_id":501,"quantity_per_unit":1,"unit":"шт"},
        ])
        wrong_variant = m.post_manufacturing_release(222,1,"2026-09-23","variant-502",chrt_id=502)
        self.assertEqual(wrong_variant["posted"],1)
        before_final = self.physical("Ткань")
        with self.assertRaisesRegex(ValueError,"Недостаточно FIFO-полуфабрикатов"):
            self.release(key="blocked-final",quantity=1)
        self.assertEqual(self.physical("Ткань"),before_final)
        with connect() as conn:
            remaining = conn.execute("SELECT remaining_meters FROM material_cost_layers WHERE id=?",(layer,)).fetchone()[0]
            self.assertEqual(remaining,before_final)  # raw FIFO remains at its pre-final state
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM material_fifo_consumptions").fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM inventory_movements WHERE movement_type='production_receipt' AND nm_id=123").fetchone()[0],0)

    def test_fractional_semi_finished_requirement_is_rejected_before_posting(self):
        self._catalog_base(123, "FINAL")
        self.seed()
        self._catalog_base(222,"BLANK")
        m.save_bom_version(222,[{"material_name":"Ткань","quantity_per_unit":1,"unit":"м"}])
        m.post_manufacturing_release(222,2,"2026-09-23","blank-2")
        m.save_bom_version(123,[{"component_type":"semi_finished","nm_id":222,"chrt_id":0,
                                 "quantity_per_unit":0.5,"unit":"шт"}])
        with self.assertRaisesRegex(ValueError,"Количество полуфабриката должно быть целым"):
            self.release(key="fractional-final",quantity=1)
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM inventory_movements WHERE source_task_key='fractional-final'").fetchone()[0],0)
    def test_shortage_second_component_rolls_back_first_fifo_and_labor(self):
        first = self.seed()
        self.seed("Нить","кг",10,100)
        self.bom()
        with connect() as conn:
            conn.execute("UPDATE material_inventory_color SET partial_meters=0 WHERE material_key='нить'")
        with self.assertRaisesRegex(ValueError,"Недостаточно сырья"):
            self.release()
        self.assertEqual(self.physical("Ткань"),20)
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT remaining_meters FROM material_cost_layers WHERE id=?",(first,)).fetchone()[0],20)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM material_fifo_consumptions").fetchone()[0],0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM inventory_movements").fetchone()[0],0)
        self.assertEqual(m.list_manufacturing_releases(),[])

    def test_insufficient_fifo_never_invents_opening_cost(self):
        self.seed(quantity=4)
        self.seed("Нить","кг",10,100)
        self.bom()
        with connect() as conn:
            conn.execute("UPDATE material_inventory_color SET partial_meters=100 WHERE material_key='ткань'")
        with self.assertRaisesRegex(ValueError,"FIFO"):
            self.release()
        self.assertEqual(self.physical("Ткань"),100)

    def test_reverse_restores_every_component_and_equipment(self):
        self.seed()
        self.seed("Нить","кг",10,100)
        eq = m.save_equipment("Пресс",90,0,9,"units")
        self.bom(operations=[{"name":"Пресс","rate_per_unit":2,"equipment_id":eq["id"],"equipment_usage_per_unit":1}])
        r = self.release()
        self.assertTrue(undo_inventory_movement(r["movement_id"])["ok"])
        self.assertEqual(self.physical("Ткань"),20)
        self.assertEqual(self.physical("Нить"),10)
        self.assertEqual(m.list_equipment()[0]["consumed_resource"],0)
        self.assertEqual(m.list_manufacturing_releases()[0]["status"],"reversed")
        self.assertFalse(undo_inventory_movement(r["movement_id"])["ok"])
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT ready_units FROM product_pipeline WHERE nm_id=123").fetchone()[0],0)
            self.assertEqual(conn.execute("SELECT status FROM production_cost_batches WHERE movement_id=?",(r["movement_id"],)).fetchone()[0],"reversed")
        with self.assertRaises(ValueError):
            self.release()

    def test_raw_defect_exact_layer_idempotency_and_undo(self):
        old = self.seed()
        with connect() as conn:
            newer = _create_material_layer(conn,"ткань","Ткань","opening","second","2026-09-02",5,30)
            conn.execute("UPDATE material_inventory_color SET partial_meters=25 WHERE material_key='ткань'")
        r = m.write_off_material_defect(newer,2,"scrap-1","Брак поставщика","2026-09-23")
        self.assertEqual(r["cost_rub"],60)
        self.assertEqual(m.write_off_material_defect(newer,2,"scrap-1","Брак поставщика","2026-09-23")["id"],r["id"])
        with self.assertRaises(ValueError):
            m.write_off_material_defect(newer,3,"scrap-1","Брак поставщика","2026-09-23")
        self.assertEqual(self.physical("Ткань"),23)
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT remaining_meters FROM material_cost_layers WHERE id=?",(old,)).fetchone()[0],20)
        self.assertTrue(undo_inventory_movement(r["movement_id"])["ok"])
        self.assertEqual(self.physical("Ткань"),25)
        self.assertEqual(m.list_material_defects()[0]["status"],"reversed")

    def test_validation_and_immutable_posted_labor(self):
        self.seed()
        self.seed("Нить","кг",10,100)
        self.bom()
        for value in [0,-1,1.1,float("nan"),float("inf"),True]:
            with self.assertRaises(ValueError):
                self.release(quantity=value)
        with self.assertRaises(ValueError):
            m.save_bom_version(123,[{"material_name":"Ткань","quantity_per_unit":1,"unit":"кг"}])
        r = self.release()
        with self.assertRaises(ValueError):
            m.post_piecework("job-1",123,"Работа","Борис",3,9,"2026-09-23","late")
        with self.assertRaises(ValueError):
            self.release(quantity=4)
        self.assertEqual(m.list_manufacturing_releases()[0]["id"],r["id"])

    def test_depreciation_capped_and_undo_requires_reverse_order(self):
        self.seed(quantity=100)
        self.seed("Нить","кг",100,100)
        eq = m.save_equipment("Пресс",100,10,4,"units")
        self.bom(operations=[{"name":"Пресс","rate_per_unit":0,"equipment_id":eq["id"],"equipment_usage_per_unit":1}])
        r1 = self.release()
        r2 = self.release(key="job-2")
        self.assertEqual(r1["depreciation_cost_rub"],67.5)
        self.assertEqual(r2["depreciation_cost_rub"],22.5)
        self.assertFalse(undo_inventory_movement(r1["movement_id"])["ok"])
        self.assertTrue(undo_inventory_movement(r2["movement_id"])["ok"])
        self.assertTrue(undo_inventory_movement(r1["movement_id"])["ok"])

    def test_shift_uses_bom_and_repeated_init_is_safe(self):
        self.seed()
        self.seed("Нить","кг",10,100)
        self.bom()
        init_db()
        with connect() as conn:
            conn.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES (123,'SKU','Товар','{\"sizes\":[]}')")
            conn.execute("INSERT INTO execution_tasks(task_key,task_type,task_date,nm_id,supplier_article,product_name,actual_units,status) VALUES ('shift-1','production','2026-09-23',123,'SKU','Товар',3,'Изготовлено')")
        r = close_production_shift("2026-09-23")
        self.assertEqual(r["errors"],[])
        self.assertEqual(r["posted"],1)
        self.assertEqual(r["cost_rub"],90)
        self.assertEqual(close_production_shift("2026-09-23")["posted"],0)

    def test_legacy_single_material_shift_unchanged(self):
        self.add_legacy_shift()
        self.confirm_packaging(12)
        r = close_production_shift("2026-09-23")
        self.assertEqual(r["errors"],[])
        self.assertEqual(r["posted"],1)
        self.assertEqual(r["cost_rub"],96)  # the same amount still applies when entered explicitly
        self.assertEqual(m.list_manufacturing_releases(),[])

    def add_legacy_shift(self, key="old-shift"):
        self.seed()
        with connect() as conn:
            conn.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES (321,'OLD','Товар','{\"sizes\":[]}')")
            conn.execute("INSERT INTO production_settings(nm_id,material_name,material_per_unit) VALUES (321,'Ткань',2)")
            conn.execute(
                "INSERT INTO execution_tasks(task_key,task_type,task_date,nm_id,supplier_article,product_name,actual_units,status) "
                "VALUES (?,'production','2026-09-23',321,'OLD','Товар',3,'Изготовлено')", (key,)
            )

    def confirm_packaging(self, amount):
        save_auto_cost_settings(pd.DataFrame([{
            "nm_id":321,"supplier_article":"OLD","packaging_cost_rub":amount,
            "packaging_cost_confirmed":True,"labor_cost_rub":0,"other_cost_rub":0,
        }]))

    def test_unknown_legacy_packaging_blocks_before_any_posting(self):
        self.add_legacy_shift()
        result = close_production_shift("2026-09-23")
        self.assertEqual(result["posted"],0)
        self.assertIn("Стоимость упаковки не подтверждена", result["errors"][0])
        self.assertIn("0 ₽ тоже нужно подтвердить", result["errors"][0])
        self.assertEqual(self.physical("Ткань"),20)
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM inventory_movements").fetchone()[0],0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM material_fifo_consumptions").fetchone()[0],0)

    def test_explicit_zero_packaging_is_confirmed_and_used(self):
        self.add_legacy_shift()
        self.confirm_packaging(0)
        result = close_production_shift("2026-09-23")
        self.assertEqual(result["errors"],[])
        self.assertEqual(result["posted"],1)
        self.assertEqual(result["cost_rub"],60)
        with connect() as conn:
            row = conn.execute("SELECT packaging_cost_rub,packaging_cost_status,packaging_cost_source,packaging_cost_confirmed_at FROM costs WHERE nm_id=321").fetchone()
            self.assertEqual(row["packaging_cost_rub"],0)
            self.assertEqual(row["packaging_cost_status"],"confirmed")
            self.assertEqual(row["packaging_cost_source"],"manual")
            self.assertTrue(row["packaging_cost_confirmed_at"])
            note = conn.execute("SELECT note FROM production_cost_batches").fetchone()[0]
            self.assertIn("упаковка 0.00 ₽/ед. (manual)",note)

    def test_positive_packaging_is_recorded_with_manual_provenance(self):
        self.add_legacy_shift()
        self.confirm_packaging(2.5)
        result = close_production_shift("2026-09-23")
        self.assertEqual(result["errors"],[])
        self.assertEqual(result["cost_rub"],67.5)
        with connect() as conn:
            row = conn.execute("SELECT packaging_cost_rub,packaging_cost_status,packaging_cost_source FROM costs WHERE nm_id=321").fetchone()
            self.assertEqual(tuple(row),(2.5,"confirmed","manual"))

    def test_preexisting_cost_row_migrates_to_unknown_regardless_of_amount(self):
        with connect() as conn:
            conn.execute("DROP TABLE costs")
            conn.execute("""CREATE TABLE costs (
                nm_id INTEGER PRIMARY KEY,supplier_article TEXT,cost_per_wb_unit REAL DEFAULT 0,
                auto_material_cost INTEGER DEFAULT 0,material_cost_rub REAL DEFAULT 0,
                packaging_cost_rub REAL DEFAULT 0,labor_cost_rub REAL DEFAULT 0,
                other_cost_rub REAL DEFAULT 0,note TEXT,updated_at TEXT)""")
            conn.execute("INSERT INTO costs(nm_id,packaging_cost_rub) VALUES (321,7.25)")
        init_db()
        with connect() as conn:
            row = conn.execute("SELECT packaging_cost_rub,packaging_cost_status,packaging_cost_source,packaging_cost_confirmed_at FROM costs WHERE nm_id=321").fetchone()
            self.assertEqual(row["packaging_cost_rub"],7.25)
            self.assertEqual(row["packaging_cost_status"],"unknown")
            self.assertEqual(row["packaging_cost_source"],"unknown")
            self.assertIsNone(row["packaging_cost_confirmed_at"])

    def test_bom_release_keeps_its_explicit_packaging_path(self):
        self.seed()
        self.seed("Нить","кг",10,100)
        self.bom(packaging_per_unit=3.25)
        result = self.release()
        self.assertEqual(result["errors"] if "errors" in result else [],[])
        self.assertEqual(result["packaging_cost_rub"],9.75)
        self.assertEqual(result["total_cost_rub"],99.75)

    def test_unknown_packaging_also_blocks_wip_completion_before_movement(self):
        with connect() as conn:
            conn.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES (321,'OLD','Товар','{\"sizes\":[]}')")
            conn.execute(
                "INSERT INTO production_settings(nm_id,supplier_article,enabled,material_name,blank_type,pack_size) "
                "VALUES (321,'OLD',1,'Ткань','Заготовка',2)"
            )
        result = post_manual_wip_packaging(321,1,packaging_date="2026-09-23",source_key="wip-unknown")
        self.assertEqual(result["posted"],0)
        self.assertIn("Стоимость упаковки не подтверждена",result["errors"][0])
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM inventory_movements").fetchone()[0],0)

    def test_unknown_packaging_blocks_applying_forecast_to_actual_cost(self):
        with connect() as conn:
            conn.execute(
                "INSERT INTO costs(nm_id,cost_per_wb_unit,forecast_total_cost_rub,forecast_material_cost_rub) "
                "VALUES (321,42,120,50)"
            )
        with self.assertRaisesRegex(ValueError, "подтвердите стоимость упаковки"):
            apply_forecast_costs(pd.DataFrame([{"nm_id":321,"apply_forecast":True}]))
        with connect() as conn:
            row = conn.execute(
                "SELECT cost_per_wb_unit,material_cost_rub,packaging_cost_status FROM costs WHERE nm_id=321"
            ).fetchone()
            self.assertEqual(row["cost_per_wb_unit"],42)
            self.assertEqual(row["material_cost_rub"],0)
            self.assertEqual(row["packaging_cost_status"],"unknown")

    def test_unknown_variant_cannot_fall_back_to_base_bom(self):
        with connect() as conn:
            self.set_material_physical_stock(conn,"canvas","Canvas",full_rolls=1,roll_length=25)
            _create_material_layer(conn,"canvas","Canvas","opening","unknown-chrt-layer","2026-09-01",25,10)
            conn.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES (100,'A','A','{\"sizes\":[{\"chrtID\":501}]}')")
            conn.execute("INSERT INTO execution_tasks(task_key,task_type,task_date,nm_id,chrt_id,supplier_article,product_name,actual_units,status) VALUES('unknown-chrt','production','2026-09-23',100,999,'A','A',1,'Изготовлено')")
        m.save_bom_version(100,[{"material_name":"Canvas","quantity_per_unit":1,"unit":"м"}])
        result = close_production_shift("2026-09-23")
        self.assertEqual(result["posted"],0)
        self.assertTrue(result["errors"])
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM inventory_movements").fetchone()[0],0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM production_cost_batches").fetchone()[0],0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM finished_goods_cost_layers").fetchone()[0],0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM material_fifo_consumptions").fetchone()[0],0)

    def test_manual_wip_duplicate_source_skips_after_packaging_confirmation_revoked(self):
        self.seed()
        with connect() as conn:
            conn.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES (321,'OLD','Товар','{\"sizes\":[]}')")
            conn.execute(
                "INSERT INTO production_settings(nm_id,supplier_article,enabled,material_name,blank_type,pack_size) "
                "VALUES (321,'OLD',1,'Ткань','Заготовка',2)"
            )
        self.confirm_packaging(12)
        issued = post_wip_blank_batch(
            "2026-09-23", "Ткань", "Заготовка", 2, 2, source_key="wip-retry-source"
        )
        self.assertEqual(issued["errors"], [])
        first = post_manual_wip_packaging(
            321, 1, packaging_date="2026-09-23", source_key="wip-retry-completion"
        )
        self.assertEqual(first["errors"], [])
        self.assertEqual(first["posted"], 1)

        save_auto_cost_settings(pd.DataFrame([{
            "nm_id":321,"supplier_article":"OLD","packaging_cost_rub":12,
            "packaging_cost_confirmed":False,"labor_cost_rub":0,"other_cost_rub":0,
        }]))
        retry = post_manual_wip_packaging(
            321, 1, packaging_date="2026-09-23", source_key="wip-retry-completion"
        )
        self.assertEqual(retry["errors"], [])
        self.assertEqual(retry["posted"], 0)
        self.assertEqual(retry["skipped"], 1)
        with connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM inventory_movements WHERE source_task_key='wip-retry-completion' AND reversed_at IS NULL"
            ).fetchone()[0], 1)

    def test_fifo_boundary_and_future_layers(self):
        self.seed(quantity=2)
        self.seed("Нить","кг",10,100)
        self.bom()
        with connect() as conn:
            _create_material_layer(conn,"ткань","Ткань","opening","new","2026-09-02",10,20)
            conn.execute("UPDATE material_inventory_color SET partial_meters=12 WHERE material_key='ткань'")
        r = self.release()
        self.assertEqual(r["material_cost_rub"],130)  # 2*10 + 4*20 + .3*100
        self.assertEqual(len(r["materials"][0]["allocations"]),2)
        with connect() as conn:
            conn.execute("UPDATE material_cost_layers SET source_date='2026-10-01' WHERE source_ref='new'")
        with self.assertRaisesRegex(ValueError,"FIFO"):
            self.release(key="future-job")

    def test_defect_insufficient_layer_and_nonfinite_validation(self):
        layer = self.seed(quantity=2)
        for quantity in [3,float("nan"),float("inf"),0,-1]:
            with self.assertRaises(ValueError):
                m.write_off_material_defect(layer,quantity,"bad","Брак","2026-09-23")
        self.assertEqual(self.physical("Ткань"),2)
        self.assertEqual(m.list_material_defects(),[])

    def test_used_finished_batch_cannot_reverse_material_consumption(self):
        from db.fifo_finished_goods import _move_finished_goods_fifo
        self.seed()
        self.seed("Нить","кг",10,100)
        self.bom()
        r = self.release()
        with connect() as conn:
            _move_finished_goods_fifo(conn,999,123,"","",1,"ready","inbound")
        self.assertFalse(undo_inventory_movement(r["movement_id"])["ok"])
        self.assertEqual(self.physical("Ткань"),14)
        self.assertEqual(m.list_manufacturing_releases()[0]["status"],"applied")

    def test_equipment_change_prohibited_after_use_and_fractional_rounding(self):
        self.seed(quantity=100)
        self.seed("Нить","кг",10,100)
        eq = m.save_equipment("Оборудование",0.02,0,3,"units")
        self.bom(operations=[{"name":"Обработка","rate_per_unit":0,"equipment_id":eq["id"],"equipment_usage_per_unit":1}])
        charges = [self.release(key=f"small-{i}",quantity=1)["depreciation_cost_rub"] for i in range(4)]
        self.assertAlmostEqual(sum(charges),0.02)
        with self.assertRaises(ValueError):
            m.save_equipment("Оборудование",100,0,3,"units",eq["id"])

    def test_concurrent_retry_posts_only_once(self):
        from concurrent.futures import ThreadPoolExecutor
        self.seed()
        self.seed("Нить","кг",10,100)
        self.bom()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.release(),range(2)))
        self.assertEqual(sum(r["posted"] for r in results),1)
        self.assertEqual(len(m.list_manufacturing_releases()),1)
        self.assertEqual(self.physical("Ткань"),14)

    def test_piecework_wrong_product_and_future_date_rejected_atomically(self):
        self.seed()
        self.seed("Нить","кг",10,100)
        self.bom(operations=[{"name":"Пошив","rate_per_unit":5}])
        m.post_piecework("job-1",999,"Пошив","Анна",3,7,"2026-09-23","wrong-product")
        with self.assertRaisesRegex(ValueError,"артикул"):
            self.release()
        m.post_piecework("job-future",123,"Пошив","Анна",3,7,"2026-10-01","future-work")
        with self.assertRaisesRegex(ValueError,"дату"):
            self.release(key="job-future")
        self.assertEqual(self.physical("Ткань"),20)
        self.assertEqual(m.list_manufacturing_releases(),[])
