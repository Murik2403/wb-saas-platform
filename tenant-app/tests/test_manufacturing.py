from __future__ import annotations

from db.core import connect, init_db
from db.fifo_materials import _create_material_layer
from db.inventory_movements import undo_inventory_movement
from db import manufacturing as m
from db.production import close_production_shift
from .base import DbTestCase


class ManufacturingTests(DbTestCase):
    def seed(self, name="Ткань", unit="м", quantity=20, rate=10):
        with connect() as conn:
            self.set_material_physical_stock(conn,name.casefold(),name,partial_meters=quantity,roll_length=1)
            conn.execute("UPDATE material_inventory_color SET unit=?,tracking_mode='quantity' WHERE material_key=?",(unit,name.casefold()))
            return _create_material_layer(conn,name.casefold(),name,"opening","test:"+name,"2026-09-01",quantity,rate)

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
            conn.execute("INSERT INTO execution_tasks(task_key,task_type,task_date,nm_id,supplier_article,product_name,actual_units,status) VALUES ('shift-1','production','2026-09-23',123,'SKU','Товар',3,'Изготовлено')")
        r = close_production_shift("2026-09-23")
        self.assertEqual(r["errors"],[])
        self.assertEqual(r["posted"],1)
        self.assertEqual(r["cost_rub"],90)
        self.assertEqual(close_production_shift("2026-09-23")["posted"],0)

    def test_legacy_single_material_shift_unchanged(self):
        self.seed()
        with connect() as conn:
            conn.execute("INSERT INTO production_settings(nm_id,material_name,material_per_unit) VALUES (321,'Ткань',2)")
            conn.execute("INSERT INTO execution_tasks(task_key,task_type,task_date,nm_id,supplier_article,product_name,actual_units,status) VALUES ('old-shift','production','2026-09-23',321,'OLD','Товар',3,'Изготовлено')")
        r = close_production_shift("2026-09-23")
        self.assertEqual(r["errors"],[])
        self.assertEqual(r["posted"],1)
        self.assertEqual(r["cost_rub"],96)  # legacy packaging default remains compatible
        self.assertEqual(m.list_manufacturing_releases(),[])

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
