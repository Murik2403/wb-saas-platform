import json,sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
import config
from db import core,shared_warehouse as wh,shop_workspaces as shops
from db.migration_import import preview_import,confirm_import,FIELDS
from db.fifo_finished_goods import read_inventory_valuation,_move_finished_goods_fifo
import base64
from cryptography.fernet import Fernet

def token(seller):return 'header.'+base64.urlsafe_b64encode(json.dumps({'sid':seller}).encode()).decode().rstrip('=')+'.fixture'

@pytest.fixture
def trusted(tmp_path,monkeypatch):
 monkeypatch.setattr(core,'DB_PATH',tmp_path/'main.sqlite3');monkeypatch.setattr(config,'DATA_DIR',tmp_path)
 monkeypatch.setattr(config,'DATA_ENCRYPTION_KEY',Fernet.generate_key().decode());monkeypatch.setattr(config,'DATA_ENCRYPTION_KEYRING','')
 monkeypatch.setattr(config,'TENANT_SLUG','verified-tenant');core.init_db();core.ensure_financial_schema();setup.initialize()
 return tmp_path
import shared_warehouse_setup as setup
import imported_shared_basis as transfer

@pytest.fixture
def ready(trusted,monkeypatch):
 monkeypatch.setattr(config,'get_token',lambda:token('primary'))
 wh.register_shop(0,shops.account_identity(token('primary')))
 with core.connect() as c:
  c.execute('BEGIN IMMEDIATE');setup.access(c,0,'1','owner')
  c.execute("INSERT INTO products_catalog(nm_id,product_name,raw_json) VALUES(100,'Imported SKU',?)",(json.dumps({'sizes':[{'chrtID':10},{'chrtID':11}]}),))
 rows=[['lot'+str(chrt),100,chrt,'2026-08-01','2026-09-01','ready',3,3,'0.335','1.01','Original manual source checked'] for chrt in (10,11)]
 text=';'.join(FIELDS['finished_lots'])+'\n'+'\n'.join(';'.join(map(str,row)) for row in rows);mapping={f:f for f in FIELDS['finished_lots']}
 p=preview_import('finished_lots',text,mapping);assert p['can_confirm'],p
 confirm_import('finished_lots',text,mapping,'opening',p['preview_fingerprint'],'1')
 return trusted

def accept():
 p=transfer.preview(100,'Confirmed authority change','1','owner')
 return transfer.confirm(100,p['reason'],p['fingerprint'],'1','owner',str(uuid4()))

def test_whole_sku_switch_immutable_units_money_no_events_and_replay(ready):
 p=transfer.preview(100,'Confirmed authority change','1','owner');key=str(uuid4())
 with core.connect() as c:before=[tuple(r) for r in c.execute('SELECT * FROM finished_goods_cost_layers ORDER BY id')]
 r=transfer.confirm(100,p['reason'],p['fingerprint'],'1','owner',key)
 assert r['total_units']==6 and r['total_cents']==202 and len(r['items'])==2
 assert transfer.confirm(100,p['reason'],p['fingerprint'],'1','owner',key)==r
 with pytest.raises(wh.WarehouseConflict):transfer.confirm(100,'Different reason',p['fingerprint'],'1','owner',key)
 with core.connect() as c:
  assert [tuple(row) for row in c.execute('SELECT * FROM finished_goods_cost_layers ORDER BY id')]==before
  for table in ('financial_report','inventory_movements','sales_fifo_events'):assert c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==0
  for table in ('warehouse_imported_transfers','warehouse_imported_authority','warehouse_ready_basis'):
   for sql in ('DELETE FROM '+table,'UPDATE '+table+' SET '+('nm_id=101' if table!='warehouse_ready_basis' else 'initial_ready_units=2'),'INSERT OR REPLACE INTO '+table+' SELECT * FROM '+table):
    with pytest.raises(sqlite3.IntegrityError):c.execute(sql)
 assert read_inventory_valuation().iloc[0]['ready_value_rub']==pytest.approx(2.02)

def test_central_fragmented_reserve_release_dispatch_sale_return_conserves(ready):
 r=accept();item=r['items'][0]
 wh.map_sku(0,100,item['chrt_id'],item['id'],'1')
 amounts=[];reservations=[]
 for i in range(3):
  p=wh.preview(0,100,item['chrt_id'],1,'2026-09-02','1','owner')
  reserve=wh.reserve(0,100,item['chrt_id'],1,'2026-09-02','reserve'+str(i),str(uuid4()),p['fingerprint'],'1','owner');reservations.append(reserve)
  amounts.append(p['layers'][0]['cost_rub'])
 assert amounts==[.34,.34,.33]
 wh.transition(reservations[-1]['id'],'release','release',str(uuid4()),'2026-09-02',1,'1','owner')
 p=wh.preview(0,100,item['chrt_id'],1,'2026-09-02','1','owner');assert p['layers'][0]['cost_rub']==.33
 reserve=reservations[0]['id'];wh.transition(reserve,'commit','dispatch',str(uuid4()),'2026-09-02',1,'1','owner');wh.transition(reserve,'receipt','arrival',str(uuid4()),'2026-09-02',2,'1','owner')
 p=wh.preview_sale(reserve,1,'2026-09-02','1','owner');sale_key=str(uuid4());sale=wh.sell(reserve,1,'2026-09-02','sale',sale_key,p['fingerprint'],'1','owner')
 assert sale['cost_rub']==.34
 returned=wh.return_sale(sale_key,1,'2026-09-03','returned',str(uuid4()),4,'1','owner');assert returned['cost_rub']==.34
 from db.finished_history_import import register_valuation
 with core.connect() as c:
  register_valuation(c);lid=p['layers'][0]['layer_id']
  assert c.execute("SELECT finished_location_amount(id,'ready',ready_units,unit_cost_rub)+finished_location_amount(id,'wb',wb_units,unit_cost_rub) FROM finished_goods_cost_layers WHERE id=?",(lid,)).fetchone()[0]==pytest.approx(1.01)

@pytest.mark.parametrize('role',['viewer','manager','warehouse'])
def test_owner_only_replay(ready,role):
 p=transfer.preview(100,'Authority change','1','owner');key=str(uuid4());transfer.confirm(100,p['reason'],p['fingerprint'],'1','owner',key)
 with pytest.raises(wh.WarehouseScopeError):transfer.confirm(100,p['reason'],p['fingerprint'],'1',role,key)

def test_seller_rotation_stale_catalog_and_rollback(ready,monkeypatch):
 p=transfer.preview(100,'Authority change','1','owner')
 monkeypatch.setattr(config,'get_token',lambda:token('foreign'))
 with pytest.raises(wh.WarehouseScopeError):transfer.confirm(100,p['reason'],p['fingerprint'],'1','owner',str(uuid4()))
 monkeypatch.setattr(config,'get_token',lambda:token('primary'))
 with core.connect() as c:c.execute("UPDATE products_catalog SET product_name='Changed' WHERE nm_id=100")
 with pytest.raises(wh.WarehouseConflict):transfer.confirm(100,p['reason'],p['fingerprint'],'1','owner',str(uuid4()))
 p=transfer.preview(100,'Authority change','1','owner');original=wh.register_item
 def failing(*a,**kw):original(*a,**kw);raise RuntimeError('Injected transaction failure')
 monkeypatch.setattr(wh,'register_item',failing)
 with pytest.raises(RuntimeError):transfer.confirm(100,p['reason'],p['fingerprint'],'1','owner',str(uuid4()))
 with core.connect() as c:
  for table in ('warehouse_imported_transfers','warehouse_imported_authority','warehouse_items','warehouse_ready_basis'):assert c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==0

def test_active_local_dependencies_block_and_local_writer_denied_after_transfer(ready):
 with core.connect() as c:
  mid=c.execute("INSERT INTO inventory_movements(event_key,movement_type,nm_id,movement_date,status,created_at) VALUES('local','consumption',100,'2026-09-02','applied','now')").lastrowid
  _move_finished_goods_fifo(c,mid,100,'A','I',1,'ready',None,10);c.execute('UPDATE product_pipeline SET ready_units=5 WHERE nm_id=100')
 with pytest.raises(wh.WarehouseConflict,match='действующие локальные'):transfer.preview(100,'Authority change','1','owner')
 from db.fifo_finished_goods import _reverse_finished_goods_allocations
 with core.connect() as c:_reverse_finished_goods_allocations(c,mid);c.execute('UPDATE product_pipeline SET ready_units=6 WHERE nm_id=100')
 accept()
 with core.connect() as c:
  with pytest.raises(wh.WarehouseScopeError):_move_finished_goods_fifo(c,mid,100,'A','I',1,'ready',None,10)

def test_concurrent_confirm_one_authority(ready):
 p=transfer.preview(100,'Authority change','1','owner')
 def run(_):
  try:return transfer.confirm(100,p['reason'],p['fingerprint'],'1','owner',str(uuid4()))
  except wh.WarehouseConflict:return None
 with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,range(2)))
 assert sum(value is not None for value in results)==1
 with core.connect() as c:assert c.execute('SELECT COUNT(*) FROM warehouse_imported_transfers').fetchone()[0]==1


@pytest.mark.parametrize('location',['inbound','wb'])
def test_nonready_and_holds_deny(ready,location):
 with core.connect() as c:c.execute(f'UPDATE finished_goods_cost_layers SET ready_units=2,{location}_units=1 WHERE chrt_id=10')
 with pytest.raises(wh.WarehouseConflict,match='в пути и на WB'):transfer.preview(100,'Authority change','1','owner')

def test_active_incident_hold_deny(ready):
 from db import incident_workflow as incident
 from db.wb_incidents import create_wb_incident_case
 case=create_wb_incident_case('Проверка','2026-09-01',document_ref='акт')
 with core.connect() as c:lid=c.execute('SELECT id FROM finished_goods_cost_layers WHERE chrt_id=10').fetchone()[0]
 incident.hold(case,lid,'ready',1,'hold-request','1')
 with pytest.raises(wh.WarehouseConflict,match='карантин'):transfer.preview(100,'Authority change','1','owner')

@pytest.mark.parametrize('sizes',[[{'chrtID':10},{'chrtID':10}],[{'chrtID':10}],None,[{'chrtID':True}]])
def test_variant_catalog_fail_closed(ready,sizes):
 with core.connect() as c:c.execute('UPDATE products_catalog SET raw_json=? WHERE nm_id=100',(json.dumps({'sizes':sizes}),))
 with pytest.raises(wh.WarehouseConflict):transfer.preview(100,'Authority change','1','owner')

def test_manufacturing_consumption_and_typed_reversal_single_basis(ready):
 accept()
 from db.shared_manufacturing import consume,journal_reversal
 from db.fifo_finished_goods import _reverse_finished_goods_allocations
 from db.finished_history_import import physical_amount
 with core.connect() as c:
  c.execute('BEGIN IMMEDIATE')
  mid=c.execute("INSERT INTO inventory_movements(event_key,movement_type,nm_id,movement_date,source_task_key,status,created_at) VALUES('manufacture','production_receipt',200,'2026-09-02','task','applied','now')").lastrowid
  total,allocations=consume(c,mid,100,10,2,'2026-09-02','task');assert total==.67
  lid=allocations[0]['layer_id'];assert physical_amount(c,lid,'ready',1)==.34
  with pytest.raises(ValueError,match='общему складу'):_reverse_finished_goods_allocations(c,mid)
  journal_reversal(c,mid);_reverse_finished_goods_allocations(c,mid)
  assert physical_amount(c,lid,'ready',3)==1.01

def test_primary_seller_rotates_before_commit_rolls_back(ready,monkeypatch):
 p=transfer.preview(100,'Authority change','1','owner');original=wh.register_item
 def rotated(*a,**kw):
  result=original(*a,**kw);monkeypatch.setattr(config,'get_token',lambda:token('changed'));return result
 monkeypatch.setattr(wh,'register_item',rotated)
 with pytest.raises(wh.WarehouseScopeError):transfer.confirm(100,p['reason'],p['fingerprint'],'1','owner',str(uuid4()))
 with core.connect() as c:assert not c.execute('SELECT 1 FROM warehouse_imported_transfers').fetchone()


def test_complete_history_required_and_manual_finance_survives_transfer(trusted,monkeypatch):
 monkeypatch.setattr(config,'get_token',lambda:token('primary'));wh.register_shop(0,shops.account_identity(token('primary')))
 with core.connect() as c:
  c.execute('BEGIN IMMEDIATE');setup.access(c,0,'1','owner');c.execute("INSERT INTO products_catalog(nm_id,product_name,raw_json) VALUES(300,'History item',?)",(json.dumps({'sizes':[{'chrtID':30}]}),))
 def save(kind,rows,key):
  text=';'.join(FIELDS[kind])+'\n'+'\n'.join(';'.join(map(str,r)) for r in rows);mapping={f:f for f in FIELDS[kind]};p=preview_import(kind,text,mapping);assert p['can_confirm'],p;confirm_import(kind,text,mapping,key,p['preview_fingerprint'],'1')
 save('finished_lots',[['historic-lot',300,30,'2026-08-01','2026-09-01','ready',3,1,'10','10','Original batch checked']],'historic-opening')
 with pytest.raises(wh.WarehouseConflict,match='полную историю'):transfer.preview(300,'Authority change','1','owner')
 save('historical_documents',[['alloc','archive-sale',10001,300,30,'2026-08-02',1,'sale',2,'historic-lot',2,'-',100,90,10,0,0,0,0,0,'Original sale checked']],'historic-sales')
 from db.financial_fifo_reconciliation import read_financial_fifo_reconciliation
 from db.historical_cogs import project_historical_cogs
 before=read_financial_fifo_reconciliation('2026-08-01','2026-08-31');assert before['status'].tolist()==['manual_source']
 p=transfer.preview(300,'Authority change','1','owner');r=transfer.confirm(300,p['reason'],p['fingerprint'],'1','owner',str(uuid4()));assert r['total_cents']==1000
 after=read_financial_fifo_reconciliation('2026-08-01','2026-08-31');assert after.equals(before)
 _,_,total=project_historical_cogs(after);assert total['manual_source_cogs_rub']==20 and not total['cogs_complete']
 with core.connect() as c:assert c.execute('SELECT COUNT(*) FROM financial_report').fetchone()[0]==1

def test_two_shops_share_one_pool_and_foreign_acl_never_inherits(ready):
 r=accept();item=r['items'][0];wh.map_sku(0,100,item['chrt_id'],item['id'],'1')
 second=shops.create('Synthetic secondary',token('secondary'),shops.account_identity(token('secondary')),register_warehouse=True)
 with pytest.raises(wh.WarehouseScopeError):wh.preview(second,200,20,1,'2026-09-02','1','manager')
 wh.grant(second,'1',True,'owner');wh.map_sku(second,200,20,item['id'],'1')
 first=wh.preview(0,100,item['chrt_id'],2,'2026-09-02','1','owner');wh.reserve(0,100,item['chrt_id'],2,'2026-09-02','primary',str(uuid4()),first['fingerprint'],'1','owner')
 second_p=wh.preview(second,200,20,2,'2026-09-02','1','owner');assert second_p['missing_units']==1
 with pytest.raises(wh.WarehouseStockError):wh.reserve(second,200,20,2,'2026-09-02','secondary',str(uuid4()),second_p['fingerprint'],'1','owner')
 p=wh.preview(second,200,20,1,'2026-09-02','1','owner');assert p['layers'][0]['cost_rub']==.34
 wh.reserve(second,200,20,1,'2026-09-02','secondary-one',str(uuid4()),p['fingerprint'],'1','owner')
 assert wh.preview(0,100,item['chrt_id'],1,'2026-09-02','1','owner')['missing_units']==1
 with core.connect() as c:assert c.execute('SELECT SUM(cost_rub) FROM warehouse_reservation_layers').fetchone()[0]==pytest.approx(1.01)
