import pytest
from db import core
from db.migration_import import preview_import, confirm_import, import_history, FIELDS
@pytest.fixture(autouse=True)
def database(monkeypatch,tmp_path):
 monkeypatch.setattr(core,'DB_PATH',tmp_path/'import.sqlite');core.init_db()
def mapping(k):return {f:f for f in FIELDS[k]}

def test_opening_conversion_keeps_total_original_provenance_and_replay():
 import json
 save('materials',['m1;Нить;кг'])
 k='opening_materials';t=text(k,['o1;Нить;г;1250;0.1;2026-09-01']);p=preview_import(k,t,mapping(k))
 assert p['can_confirm'],p
 assert p['rows'][0]['data']['quantity']==1.25
 assert p['rows'][0]['data']['unit_cost_rub']==100
 from db.migration_import import parse_csv,_hash
 original_rows,errors=parse_csv(k,t,mapping(k));assert not errors
 original_hash=_hash({'kind':k,'rows':original_rows})
 assert original_hash!=_hash({'kind':k,'rows':p['rows']})
 assert not confirm_import(k,t,mapping(k),'converted',p['preview_fingerprint'])['replayed']
 assert confirm_import(k,t,mapping(k),'converted',p['preview_fingerprint'])['replayed']
 with core.connect() as c:
  layer=c.execute('SELECT * FROM material_cost_layers').fetchone()
  assert (layer['original_meters'],layer['unit_cost_rub_m'],layer['original_amount_rub'])==(1.25,100,125)
  snapshot=json.loads(c.execute("SELECT snapshot_json FROM migration_import_sources WHERE kind='opening_materials'").fetchone()[0])
  assert snapshot['unit_conversion']['entered_quantity']=='1250'
  assert snapshot['unit_conversion']['entered_unit']=='г'
  assert snapshot['unit_conversion']['base_unit']=='кг'
  assert c.execute("SELECT content_hash FROM migration_import_batches WHERE request_key='converted'").fetchone()[0]==original_hash

def test_converted_opening_stale_catalog_and_lossy_precision_fail_without_layer():
 save('materials',['m1;Нить;кг'])
 k='opening_materials';t=text(k,['o1;Нить;г;1250;0.1;2026-09-01']);p=preview_import(k,t,mapping(k))
 assert p['can_confirm']
 for row in ['o2;Нить;л;1;100;2026-09-01','o2;Нить;г;1.001;100;2026-09-01']:
  assert not preview_import(k,text(k,[row]),mapping(k))['can_confirm']
 with core.connect() as c:c.execute("UPDATE material_inventory_color SET unit='л' WHERE material_key='нить'")
 with pytest.raises(ValueError):confirm_import(k,t,mapping(k),'stale',p['preview_fingerprint'])
 with core.connect() as c:assert c.execute('SELECT COUNT(*) FROM material_cost_layers').fetchone()[0]==0
def text(k,rows):return ';'.join(FIELDS[k])+'\n'+'\n'.join(rows)
def save(k,rows,key='request-1'):
 t=text(k,rows);p=preview_import(k,t,mapping(k));assert p['can_confirm'],p
 return confirm_import(k,t,mapping(k),key,p['preview_fingerprint'])
def test_preview_readonly():
 assert preview_import('materials',text('materials',['m1;Ткань;м']),mapping('materials'))['can_confirm']
 assert not import_history()
 with core.connect() as c:
  assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='migration_import_batches'").fetchone()
  assert c.execute('SELECT COUNT(*) FROM material_inventory_color').fetchone()[0]==0

def test_fifo_reconciliation():
 save('materials',['m1;Ткань;м','m2;Краска;кг'])
 save('opening_materials',['o1;Ткань;м;3;125,50;2026-09-01'],'request-2')
 with core.connect() as c:
  m=c.execute("SELECT * FROM material_inventory_color WHERE material_key='ткань'").fetchone();l=c.execute("SELECT * FROM material_cost_layers WHERE material_key='ткань'").fetchone()
  assert m['balance_known']==1 and m['partial_meters']==3
  assert l['remaining_meters']==3 and l['unit_cost_rub_m']==125.5 and l['original_amount_rub']==376.5
  assert l['source_type']=='migration_opening' and l['source_ref']=='csv:o1'
 assert len(import_history())==2

def test_idempotence_conflict():
 k='products';t=text(k,['p1;123;ART;Товар']);p=preview_import(k,t,mapping(k))
 assert not confirm_import(k,t,mapping(k),'request-1',p['preview_fingerprint'])['replayed']
 assert confirm_import(k,t,mapping(k),'request-1',p['preview_fingerprint'])['replayed']
 assert confirm_import(k,t,mapping(k),'request-2','0'*64)['replayed']
 with pytest.raises(ValueError):confirm_import(k,text(k,['p2;124;ART;Другой']),mapping(k),'request-1',p['preview_fingerprint'])
 with core.connect() as c:assert c.execute('SELECT COUNT(*) FROM products_catalog').fetchone()[0]==1

def test_stale_and_no_overwrite():
 k='materials';t=text(k,['m1;Ткань;м']);p=preview_import(k,t,mapping(k));save(k,['m2;Ткань;м'])
 with pytest.raises(ValueError,match='изменились'):confirm_import(k,t,mapping(k),'new',p['preview_fingerprint'])
 assert not preview_import(k,t,mapping(k))['can_confirm']
 save('opening_materials',['o1;Ткань;м;3;10;2026-09-01'],'opening')
 assert not preview_import('opening_materials',text('opening_materials',['o2;Ткань;м;4;20;2026-09-01']),mapping('opening_materials'))['can_confirm']
@pytest.mark.parametrize('row',['o1;Нет;м;3;10;2026-09-01','o1;Ткань;шт;3;10;2026-09-01','o1;Ткань;м;NaN;10;2026-09-01','o1;Ткань;м;3;-1;2026-09-01','o1;Ткань;м;3;10;2999-01-01','o1;Ткань;м;0.0001;10;2026-09-01'])
def test_invalid_opening(row):
 save('materials',['m1;Ткань;м']);assert not preview_import('opening_materials',text('opening_materials',[row]),mapping('opening_materials'))['can_confirm']
def test_duplicate_and_mapping():
 for rows in [['m1;Ткань;м','m1;Краска;кг'],['m1;Ткань;м','m2;ТКАНЬ;м']]:assert not preview_import('materials',text('materials',rows),mapping('materials'))['can_confirm']
 with pytest.raises(ValueError):preview_import('materials',text('materials',['m1;Ткань;м']),{'source_id':'unit','unit':'unit','material_name':'material_name'})
def test_atomic_rollback(monkeypatch):
 from db import fifo_materials
 save('materials',['m1;Ткань;м','m2;Краска;кг']);k='opening_materials';t=text(k,['o1;Ткань;м;3;10;2026-09-01','o2;Краска;кг;4;20;2026-09-01']);p=preview_import(k,t,mapping(k))
 original=fifo_materials._create_material_layer;calls=[]
 def fail(*a,**kw):
  calls.append(1)
  if len(calls)==2:raise RuntimeError('interrupted')
  return original(*a,**kw)
 monkeypatch.setattr(fifo_materials,'_create_material_layer',fail)
 with pytest.raises(RuntimeError):confirm_import(k,t,mapping(k),'opening',p['preview_fingerprint'])
 with core.connect() as c:
  assert c.execute('SELECT COUNT(*) FROM material_cost_layers').fetchone()[0]==0
  assert c.execute('SELECT SUM(partial_meters) FROM material_inventory_color').fetchone()[0]==0
  assert c.execute("SELECT COUNT(*) FROM migration_import_sources WHERE kind='opening_materials'").fetchone()[0]==0
 assert len(import_history())==1

def test_concurrent_confirm_creates_one_batch():
 from concurrent.futures import ThreadPoolExecutor
 k='products';t=text(k,['p1;123;ART;Товар']);p=preview_import(k,t,mapping(k))
 with ThreadPoolExecutor(max_workers=2) as pool:
  values=list(pool.map(lambda key:confirm_import(k,t,mapping(k),key,p['preview_fingerprint']),['a','b']))
 assert sorted(v['replayed'] for v in values)==[False,True]
 with core.connect() as c:assert c.execute('SELECT COUNT(*) FROM products_catalog').fetchone()[0]==1

def test_source_history_is_append_only():
 import sqlite3
 save('materials',['m1;Ткань;м'])
 with core.connect() as c:
  with pytest.raises(sqlite3.IntegrityError):c.execute("DELETE FROM migration_import_sources")
  with pytest.raises(sqlite3.IntegrityError):c.execute("UPDATE migration_import_batches SET imported_count=10")
