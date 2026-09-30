import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_supply_planning import snapshot
from db.supply_planning import build_supply_preview

def task(k="final",nm=10,q=2,d="2026-09-15"):
 return dict(task_key=k,task_date=d,nm_id=nm,chrt_id=nm*10,planned_units=q,status="Не начато")
def semi(n,b,q=1):
 return dict(component_type="semi_finished",nm_id=n,chrt_id=n*10,component_bom_version_id=b,quantity_per_unit=q,unit="шт")
def bom(i,n,m,v=1):
 return dict(id=i,nm_id=n,chrt_id=n*10,version=v,materials=m)
def raw(q=3):
 return dict(material_key="raw",material_name="Сырьё",quantity_per_unit=q,unit="м")
def fixture():
 return snapshot(tasks=[task()],catalog_identities=[dict(nm_id=n,valid=True,chrt_ids=[n*10]) for n in [10,20,30]],boms=[bom(1,10,[semi(20,2)]),bom(2,20,[semi(30,3,2)]),bom(3,30,[raw()])],inventory=[dict(material_key="raw",material_name="Сырьё",unit="м",balance_known=1,partial_meters=0,tracking_mode="quantity")],fifo_inventory=[],intermediate_layers=[])
def layer(n,q,d="2026-09-01",i=1):
 return dict(id=i,nm_id=n,chrt_id=n*10,ready_units=q,status="active",source_type="opening",source_date=d)
def test_pinned_chain():
 s=fixture();s["boms"].append(bom(4,30,[raw(100)],2));r=build_supply_preview(s)
 assert r["blockers"]==[] and r["lines"][0]["required_base_qty"]==12
 assert [x["bom_version_id"] for x in r["lines"][0]["source_refs"][0]["route"]]==[1,2,3]
def test_partial_stock():
 s=fixture();s["intermediate_layers"]=[layer(30,2)];r=build_supply_preview(s)
 assert r["lines"][0]["required_base_qty"]==6 and r["intermediate_allocations"][1]["covered_units"]==2
 assert s["intermediate_layers"][0]["ready_units"]==2
def test_shared_stock_chronology():
 s=fixture();s["tasks"]=[task("late",d="2026-09-20"),task("early",d="2026-09-10")];s["intermediate_layers"]=[layer(20,3)];r=build_supply_preview(s)
 assert r["lines"][0]["required_base_qty"]==6
 assert [(x["task_key"],x["covered_units"]) for x in r["intermediate_allocations"] if x["nm_id"]==20]==[("early",2),("late",1)]
def test_future_wrong_variant():
 s=fixture();l=layer(20,20);l["chrt_id"]=201;s["intermediate_layers"]=[l,layer(20,20,"2026-09-16",2)]
 assert build_supply_preview(s)["lines"][0]["required_base_qty"]==12
def test_all_covered_checks_recipe_fingerprint():
 s=fixture();s["intermediate_layers"]=[layer(20,2)];r=build_supply_preview(s);assert r["lines"]==[]
 s["intermediate_layers"][0]["ready_units"]=1;assert build_supply_preview(s)["preview_fingerprint"]!=r["preview_fingerprint"]
 s["boms"]=s["boms"][:1];assert build_supply_preview(s)["blockers"][0]["code"]=="missing_nested_bom"
def test_same_day_planned_output_not_doublecounted():
 s=fixture();s["tasks"]=[task(),task("child",20,2)];r=build_supply_preview(s)
 assert r["blockers"]==[] and r["lines"][0]["required_base_qty"]==12
 x=next(x for x in r["intermediate_allocations"] if x["task_key"]=="final");assert x["covered_units"]==2 and x["allocations"][0]["planned_task_key"]=="child"
def test_later_production_not_coverage():
 s=fixture();s["tasks"]=[task(),task("child",20,2,"2026-09-20")];assert build_supply_preview(s)["lines"][0]["required_base_qty"]==24
def test_cycles_identity_provenance():
 s=fixture();s["boms"][2]["materials"]=[semi(10,1)];assert build_supply_preview(s)["blockers"][0]["code"]=="nested_cycle"
 s=fixture();s["catalog_identities"][1]["valid"]=False;assert build_supply_preview(s)["blockers"][0]["code"]=="invalid_nested_variant"
 s=fixture();l=layer(20,2);l["source_type"]="reconstruction_cost_pending";s["intermediate_layers"]=[l];assert build_supply_preview(s)["blockers"][0]["code"]=="invalid_intermediate_provenance"
def test_partial_coverage_fraction_not_truncated():
 s=fixture();s["boms"][1]["materials"]=[semi(30,3,.5)];s["intermediate_layers"]=[layer(20,1)];r=build_supply_preview(s)
 assert r["lines"]==[] and r["intermediate_allocations"]==[] and r["blockers"][0]["code"]=="fractional_nested_units"


from base import DbTestCase
import json
class NestedSupplyDBTests(DbTestCase):
 def test_real_snapshot_pinned_recipe_stock_and_confirmation_note(self):
  from db.core import connect
  from db.supply_planning import load_supply_plan_snapshot,confirm_supply_plan
  with connect() as conn:
   source=fixture()
   for card in source["catalog_identities"]:
    conn.execute("INSERT INTO products_catalog(nm_id,raw_json) VALUES (?,?)",(card["nm_id"],json.dumps({"sizes":[{"chrtID":card["chrt_ids"][0]}]})))
   for spec in source["boms"]:
    conn.execute("INSERT INTO manufacturing_boms(id,nm_id,chrt_id,version,materials_json,operations_json,packaging_per_unit,other_per_unit,note,created_at) VALUES (?,?,?,?,?,'[]',0,0,'','2026-09-01')",(spec["id"],spec["nm_id"],spec["chrt_id"],spec["version"],json.dumps(spec["materials"])))
   conn.execute("INSERT INTO execution_tasks(task_key,task_type,task_date,nm_id,chrt_id,planned_units,actual_units,status,updated_at) VALUES ('final','production','2026-09-15',10,100,2,0,'Не начато','2026-09-01')")
   conn.execute("INSERT INTO material_inventory_color(material_key,material_name,balance_known,full_rolls,partial_meters,roll_length,unit,tracking_mode,updated_at) VALUES ('raw','Сырьё',1,0,0,1,'м','quantity','2026-09-01')")
   conn.execute("INSERT INTO suppliers(name,default_currency,lead_time_days,active,created_at,updated_at) VALUES ('Supplier','RUB',1,1,'2026-09-01','2026-09-01')")
   conn.execute("INSERT INTO finished_goods_cost_layers(nm_id,chrt_id,source_type,source_ref,source_date,original_units,ready_units,created_at,updated_at) VALUES (30,300,'opening','opening:30','2026-09-01',2,2,'2026-09-01','2026-09-01')")
   before=conn.total_changes
   r=build_supply_preview(load_supply_plan_snapshot(conn,'2026-09-01','2026-09-30'))
   assert conn.total_changes==before and r["blockers"]==[] and r["lines"][0]["required_base_qty"]==6
   confirm_supply_plan(conn,'nested-confirm','hash',r["preview_fingerprint"],r,[dict(material_key='raw',unit='м',quantity=6,supplier_id=1,expected_date='2026-09-10')])
   note=conn.execute("SELECT note FROM procurement_items").fetchone()[0]
   assert '"route"' in note and '"bom_version_id":3' in note and '"layer_id":1' in note
   assert conn.execute("SELECT ready_units FROM finished_goods_cost_layers").fetchone()[0]==2

def test_sibling_components_cannot_reuse_intermediate_units():
 s=fixture();s["boms"][0]["materials"]=[semi(20,2),semi(20,2)];s["intermediate_layers"]=[layer(20,3)];r=build_supply_preview(s)
 assert r["lines"][0]["required_base_qty"]==6
 assert sum(x["covered_units"] for x in r["intermediate_allocations"] if x["nm_id"]==20)==3

def test_depth_limit_blocks_overlong_route():
 s=fixture();s["boms"]=[];s["catalog_identities"]=[]
 for i in range(11):
  nm=10+i;s["catalog_identities"].append(dict(nm_id=nm,valid=True,chrt_ids=[nm*10]))
  s["boms"].append(bom(i+1,nm,[semi(nm+1,i+2)] if i<10 else [raw()]))
 assert build_supply_preview(s)["blockers"][0]["code"]=="nested_depth"


class QuarantineNestedSupplyTests(DbTestCase):
 def seed(self):
  from db.core import connect
  from db.wb_incidents import create_wb_incident_case
  from db.fifo_finished_goods import _create_finished_goods_layer
  with connect() as conn:
   source=fixture()
   for card in source["catalog_identities"]:
    conn.execute("INSERT INTO products_catalog(nm_id,raw_json) VALUES (?,?)",(card["nm_id"],json.dumps({"sizes":[{"chrtID":card["chrt_ids"][0]}]})))
   for spec in source["boms"]:
    conn.execute("INSERT INTO manufacturing_boms(id,nm_id,chrt_id,version,materials_json,operations_json,packaging_per_unit,other_per_unit,note,created_at) VALUES (?,?,?,?,?,'[]',0,0,'','2026-09-01')",(spec["id"],spec["nm_id"],spec["chrt_id"],spec["version"],json.dumps(spec["materials"])))
   conn.execute("INSERT INTO execution_tasks(task_key,task_type,task_date,nm_id,chrt_id,planned_units,actual_units,status,updated_at) VALUES ('final','production','2026-09-15',10,100,2,0,'Не начато','2026-09-01')")
   conn.execute("INSERT INTO material_inventory_color(material_key,material_name,balance_known,full_rolls,partial_meters,roll_length,unit,tracking_mode,updated_at) VALUES ('raw','Сырьё',1,0,0,1,'м','quantity','2026-09-01')")
   conn.execute("INSERT INTO suppliers(name,default_currency,lead_time_days,active,created_at,updated_at) VALUES ('Supplier','RUB',1,1,'2026-09-01','2026-09-01')")
   layer=_create_finished_goods_layer(conn,20,'semi','semi','opening','source','2026-09-01',2,20,'ready',chrt_id=200)
  return create_wb_incident_case('Инцидент','2026-09-01'),layer
 def preview(self):
  from db.core import connect
  from db.supply_planning import load_supply_plan_snapshot
  with connect() as conn:
   before=conn.total_changes
   source=load_supply_plan_snapshot(conn,'2026-09-01','2026-09-30')
   assert conn.total_changes==before
   return source,build_supply_preview(source)
 def test_full_hold_removes_coverage_release_restores_and_fingerprint_keeps_revision(self):
  from db.incident_workflow import hold,release
  case,layer=self.seed();initial_source,initial=self.preview();assert initial['lines']==[]
  identifier=hold(case,layer,'ready',2,'ready-hold','owner')
  source,blocked=self.preview();assert blocked['lines'][0]['required_base_qty']==12
  assert source['intermediate_layers'][0]['physical_ready_units']==2 and source['intermediate_layers'][0]['ready_units']==0
  assert blocked['preview_fingerprint']!=initial['preview_fingerprint']
  release(case,identifier,'Сверено','owner');_,restored=self.preview()
  assert restored['lines']==[]
  assert restored['preview_fingerprint'] not in {initial['preview_fingerprint'],blocked['preview_fingerprint']}
 def test_partial_hold_covers_only_unheld_quantity(self):
  from db.incident_workflow import hold
  case,layer=self.seed();hold(case,layer,'ready',1,'ready-hold','owner')
  _,preview=self.preview()
  assert preview['lines'][0]['required_base_qty']==6
  assert sum(item['covered_units'] for item in preview['intermediate_allocations'] if item['nm_id']==20)==1
 def test_hold_change_invalidates_confirmation_without_creating_order(self):
  import pytest
  from db.incident_workflow import hold,release
  from db.core import connect
  from db.supply_planning import confirm_supply_plan
  case,layer=self.seed();identifier=hold(case,layer,'ready',1,'ready-hold','owner');_,old=self.preview()
  release(case,identifier,'Сверено','owner');_,current=self.preview()
  with connect() as conn:
   with pytest.raises(ValueError,match='stale_preview'):
    confirm_supply_plan(conn,'old-request','hash',old['preview_fingerprint'],current,[dict(material_key='raw',unit='м',quantity=6,supplier_id=1,expected_date='2026-09-10')])
   assert conn.execute('SELECT COUNT(*) FROM procurement_orders').fetchone()[0]==0
 def test_other_location_hold_does_not_reduce_ready_coverage(self):
  from db.incident_workflow import hold
  from db.core import connect
  case,layer=self.seed()
  with connect() as conn:conn.execute('UPDATE finished_goods_cost_layers SET wb_units=2 WHERE id=?',(layer,))
  hold(case,layer,'wb',2,'other-hold','owner')
  _,preview=self.preview();assert preview['lines']==[]
