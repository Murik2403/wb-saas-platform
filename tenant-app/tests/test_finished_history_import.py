import json,pytest
from db import core
from db.migration_import import preview_import,confirm_import,FIELDS
from db.financial_fifo_reconciliation import read_financial_fifo_reconciliation
from db.historical_cogs import project_historical_cogs

@pytest.fixture(autouse=True)
def database(monkeypatch,tmp_path):
 monkeypatch.setattr(core,'DB_PATH',tmp_path/'history.sqlite');core.init_db()
 with core.connect() as c:c.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES(100,'A','Item',?)",(json.dumps({'sizes':[{'chrtID':10,'techSize':'S'}]}),))

def csv(kind,rows):return ';'.join(FIELDS[kind])+'\n'+'\n'.join(';'.join(str(r[f]) for f in FIELDS[kind]) for r in rows)
def preview(kind,rows):return preview_import(kind,csv(kind,rows),{f:f for f in FIELDS[kind]})
def save(kind,rows,key):
 p=preview(kind,rows);assert p['can_confirm'],p
 return confirm_import(kind,csv(kind,rows),{f:f for f in FIELDS[kind]},key,p['preview_fingerprint'],'1')
def lot(original=10,remaining=5,rate='10',amount='50'):
 return dict(source_id='L1',nm_id=100,chrt_id=10,source_date='2026-08-01',cutover_date='2026-09-01',location='ready',original_units=original,remaining_units=remaining,unit_cost_rub=rate,remaining_cost_rub=amount,document_proof='ledger original batch')
def doc(key,rrd,day,event,qty,original='-',**kw):
 r=dict(source_id='allocation-'+key,document_id=key,rrd_id=rrd,nm_id=100,chrt_id=10,operation_date=day,sequence=rrd,event_type=event,quantity=qty,lot_source_id='L1',allocation_units=qty,original_sale_document=original,document_proof='source documents checked',**{m:'0' for m in ('retail_amount','ppvz_for_pay','commission','delivery_rub','storage_fee','deduction','penalty','acceptance')});r.update(kw);return r

def test_preview_remaining_only():
 assert preview('finished_lots',[lot()])['can_confirm']
 with core.connect() as c:assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='migration_finished_lots'").fetchone()
 save('finished_lots',[lot()],'lots')
 with core.connect() as c:
  l=c.execute('SELECT * FROM finished_goods_cost_layers').fetchone();assert l['original_units']==l['ready_units']==5 and l['original_amount_rub']==50
  assert c.execute('SELECT ready_units FROM product_pipeline').fetchone()[0]==5
  assert c.execute('SELECT COUNT(*) FROM inventory_movements').fetchone()[0]==0

def test_sale10_return5_manual_cogs_and_signs():
 save('finished_lots',[lot()],'lots')
 docs=[doc('sale',11,'2026-08-02','sale',10,retail_amount='200',ppvz_for_pay='180',commission='20'),doc('return',12,'2026-08-03','return',5,'sale',retail_amount='-100',ppvz_for_pay='-90',commission='-10',delivery_rub='5')]
 save('historical_documents',docs,'docs')
 rec=read_financial_fifo_reconciliation('2026-08-01','2026-08-31');assert rec['status'].tolist()==['manual_source','manual_source'];assert rec['eligible_fifo_cogs_rub'].tolist()==[100,-50]
 _,_,total=project_historical_cogs(rec);assert total['cogs_rub']==50 and total['manual_source_cogs_rub']==50 and not total['cogs_complete'] and total['exact_unit_coverage_percent']==0
 with core.connect() as c:
  assert c.execute('SELECT SUM(ready_units) FROM finished_goods_cost_layers').fetchone()[0]==5
  assert c.execute('SELECT COUNT(*) FROM sales_fifo_events').fetchone()[0]==0
  assert c.execute('SELECT SUM(retail_amount),SUM(ppvz_for_pay),SUM(delivery_rub) FROM financial_report').fetchone()[:]==(100,90,5)
  assert c.execute('SELECT identity,source_kind FROM financial_source_ingestions LIMIT 1').fetchone()[:]==(None,'legacy_unverified')

def test_fragmented_cents_incomplete_history():
 save('finished_lots',[lot(3,0,'0.335','0')],'lots')
 docs=[doc('sale'+str(i),10+i,'2026-08-0'+str(i+1),'sale',1) for i in range(3)]
 assert not preview('historical_documents',docs[:2])['can_confirm']
 save('historical_documents',docs,'docs')
 with core.connect() as c:assert [r[0] for r in c.execute('SELECT cogs_cents FROM migration_historical_documents ORDER BY rrd_id')]==[34,34,33]

def test_replay_and_immutable_replace():
 rows=[lot()];p=preview('finished_lots',rows);text=csv('finished_lots',rows);mapping={f:f for f in FIELDS['finished_lots']}
 confirm_import('finished_lots',text,mapping,'same',p['preview_fingerprint'],'1')
 assert confirm_import('finished_lots',text,mapping,'same',p['preview_fingerprint'],'1')['replayed']
 with pytest.raises(ValueError):confirm_import('finished_lots',csv('finished_lots',[dict(lot(),document_proof='other proof')]),mapping,'same',p['preview_fingerprint'],'1')
 with core.connect() as c:
  for sql in ["UPDATE migration_finished_lots SET remaining_units=4",'DELETE FROM migration_finished_lots',"INSERT OR REPLACE INTO migration_finished_lots SELECT * FROM migration_finished_lots"]:
   with pytest.raises(Exception):c.execute(sql)

def test_cutover_direct_moves_manufacturing_boundary():
 save('finished_lots',[lot()],'lots')
 with core.connect() as c:
  for typ in ('dispatch','production_receipt','wb_sale_fifo','shared_commit'):
   with pytest.raises(Exception,match='cutover'):c.execute("INSERT INTO inventory_movements(event_key,movement_type,nm_id,movement_date,status,created_at) VALUES(?,?,100,'2026-08-31','applied','now')",(typ,typ))
  c.execute("INSERT INTO inventory_movements(event_key,movement_type,nm_id,movement_date,status,created_at) VALUES('boundary','dispatch',100,'2026-09-01','applied','now')")
  with pytest.raises(Exception,match='cutover'):c.execute("UPDATE inventory_movements SET movement_date='2026-08-31' WHERE event_key='boundary'")

def test_changed_financial_source():
 save('finished_lots',[lot(3,0,'0.335','0')],'lots');save('historical_documents',[doc('sale',11,'2026-08-02','sale',3)],'docs')
 with core.connect() as c:c.execute('UPDATE financial_report SET penalty=1 WHERE rrd_id=11')
 rec=read_financial_fifo_reconciliation('2026-08-01','2026-08-31');assert rec.iloc[0]['reason']=='migration_historical_source_changed' and rec.iloc[0]['status']=='insufficient'

def test_post_cutover_physical_cents_return_resale_reverse():
 from db.fifo_finished_goods import _move_finished_goods_fifo,_reverse_finished_goods_allocations
 from db.fifo_sales import _restore_return_to_original_layers
 save('finished_lots',[dict(lot(3,3,'0.335','1.01'),location='wb')],'lots')
 with core.connect() as c:
  def movement(key):return c.execute("INSERT INTO inventory_movements(event_key,movement_type,nm_id,movement_date,status,created_at) VALUES(?,'wb_sale_fifo',100,'2026-09-02','applied','now')",(key,)).lastrowid
  amounts=[];ids=[]
  for i in range(3):
   mid=movement(str(i));ids.append(mid);amounts.append(_move_finished_goods_fifo(c,mid,100,'A','Item',1,'wb',None,10)[0])
  assert amounts==[.34,.34,.33] and round(sum(amounts),2)==1.01
  returned=movement('return');assert _restore_return_to_original_layers(c,returned,{'movement_id':ids[0]},1)==(.34,1)
  resale=movement('resale');assert _move_finished_goods_fifo(c,resale,100,'A','Item',1,'wb',None,10)[0]==.34
  _reverse_finished_goods_allocations(c,resale)
  assert _move_finished_goods_fifo(c,movement('resale-again'),100,'A','Item',1,'wb',None,10)[0]==.34

def test_other_sku_snapshot_cannot_hide_existing_wb_stock():
 with core.connect() as c:
  c.execute("INSERT INTO stocks(snapshot_at,nm_id,chrt_id,quantity,warehouse_name,raw_json) VALUES('2026-08-01',100,10,2,'W','{}')")
  c.execute("INSERT INTO stocks(snapshot_at,nm_id,chrt_id,quantity,warehouse_name,raw_json) VALUES('2026-08-02',999,20,1,'W','{}')")
 assert not preview('finished_lots',[dict(lot(3,3,'1','3'),location='wb')])['can_confirm']

def test_dispatch_specific_money_reverse_receipt_order():
 from db.fifo_finished_goods import _move_finished_goods_fifo,_move_dispatch_allocation_to_wb,_reverse_finished_goods_allocations
 save('finished_lots',[lot(3,3,'0.335','1.01')],'lots')
 with core.connect() as c:
  def move(key,typ,ref=None):return c.execute("INSERT INTO inventory_movements(event_key,movement_type,nm_id,movement_date,status,created_at,reference_movement_id) VALUES(?,?,100,'2026-09-02','applied','now',?)",(key,typ,ref)).lastrowid
  dispatches=[]
  for i in range(3):
   mid=move('dispatch'+str(i),'dispatch');dispatches.append(mid)
   assert _move_finished_goods_fifo(c,mid,100,'A','Item',1,'ready','inbound',10)[0]==[.34,.34,.33][i]
  for i in (2,0,1):
   receipt=move('receipt'+str(i),'receipt',dispatches[i])
   assert _move_dispatch_allocation_to_wb(c,receipt,dispatches[i],1)[0]==[.34,.34,.33][i]
  layer=c.execute('SELECT * FROM finished_goods_cost_layers').fetchone()
  assert layer['wb_units']==3 and layer['inbound_units']==layer['ready_units']==0
  with pytest.raises(Exception):c.execute('DELETE FROM finished_goods_cost_layers')

def test_historical_alternate_key_replace_forbidden():
 save('finished_lots',[lot(3,0,'0.335','0')],'lots');save('historical_documents',[doc('sale',11,'2026-08-02','sale',3)],'docs')
 with core.connect() as c:
  with pytest.raises(Exception):c.execute("INSERT OR REPLACE INTO migration_historical_documents SELECT 'different',rrd_id,shop_id,nm_id,chrt_id,operation_date,event_type,quantity,cogs_cents,financial_fingerprint,snapshot_json FROM migration_historical_documents")
  assert c.execute('SELECT document_id FROM migration_historical_documents').fetchone()[0]=='sale'

def test_same_day_sale_return_resale_explicit_sequence():
 save('finished_lots',[lot(3,0,'0.335','0')],'lots')
 docs=[doc('sale',11,'2026-08-02','sale',3),doc('return',12,'2026-08-02','return',1,'sale'),doc('resale',13,'2026-08-02','sale',1)]
 save('historical_documents',docs,'docs')
 with core.connect() as c:assert [r[0] for r in c.execute('SELECT cogs_cents FROM migration_historical_documents ORDER BY rrd_id')]==[101,34,34]

def test_money_overflow_rejected_by_preview():
 assert not preview('finished_lots',[lot(1000000000,0,'1000000000','0')])['can_confirm']

def test_concurrent_openings_one_atomic_winner():
 from concurrent.futures import ThreadPoolExecutor
 from threading import Barrier
 barrier=Barrier(2);rows=[[dict(lot(),source_id='L'+str(i))] for i in range(2)]
 before=[preview('finished_lots',r) for r in rows];assert all(p['can_confirm'] for p in before)
 def write(i):
  barrier.wait()
  try:return confirm_import('finished_lots',csv('finished_lots',rows[i]),{f:f for f in FIELDS['finished_lots']},'request'+str(i),before[i]['preview_fingerprint'],'1')['imported_count']
  except ValueError:return 'stale'
 with ThreadPoolExecutor(2) as pool:result=list(pool.map(write,range(2)))
 assert sorted(map(str,result))==['1','stale']
 with core.connect() as c:
  assert c.execute('SELECT COUNT(*) FROM migration_finished_lots').fetchone()[0]==1
  assert c.execute('SELECT SUM(ready_units) FROM finished_goods_cost_layers').fetchone()[0]==5
  assert c.execute('SELECT ready_units FROM product_pipeline').fetchone()[0]==5

def test_injected_second_document_failure_rolls_whole_history_back(monkeypatch):
 from db import financial_source
 save('finished_lots',[lot()],'lots')
 docs=[doc('sale',11,'2026-08-02','sale',10),doc('return',12,'2026-08-03','return',5,'sale')]
 p=preview('historical_documents',docs);assert p['can_confirm']
 original=financial_source.record
 def fail(c,rows,identity):
  original(c,rows,identity)
  if rows[0]['rrd_id']==12:raise ValueError('Injected source recording failure')
 monkeypatch.setattr(financial_source,'record',fail)
 with pytest.raises(ValueError,match='Injected'):confirm_import('historical_documents',csv('historical_documents',docs),{f:f for f in FIELDS['historical_documents']},'docs',p['preview_fingerprint'],'1')
 with core.connect() as c:
  assert c.execute('SELECT COUNT(*) FROM financial_report').fetchone()[0]==0
  assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='financial_source_ingestions'").fetchone()
  assert c.execute('SELECT COUNT(*) FROM migration_historical_documents').fetchone()[0]==0
  assert c.execute('SELECT SUM(ready_units) FROM finished_goods_cost_layers').fetchone()[0]==5

def test_input_component_cutover_not_just_output_sku():
 from db.fifo_finished_goods import _move_finished_goods_fifo
 save('finished_lots',[lot()],'lots')
 with core.connect() as c:
  output=c.execute("INSERT INTO inventory_movements(event_key,movement_type,nm_id,movement_date,status,created_at) VALUES('old-other-output','production_receipt',200,'2026-08-31','applied','now')").lastrowid
  with pytest.raises(ValueError,match='cutover'):_move_finished_goods_fifo(c,output,100,'A','Item',1,'ready',None,10)
  assert c.execute('SELECT SUM(ready_units) FROM finished_goods_cost_layers').fetchone()[0]==5

@pytest.mark.parametrize('change',[{'chrt_id':0},{'remaining_units':11},{'remaining_cost_rub':'51.001'},{'original_units':'NaN'},{'source_date':'2026-09-02'},{'cutover_date':'2999-01-01'}])
def test_invalid_opening(change):assert not preview('finished_lots',[dict(lot(),**change)])['can_confirm']


def test_inventory_readers_exact_residual_after_fragmented_consumption():
 from db.fifo_finished_goods import _move_finished_goods_fifo,_finished_layer_totals,read_finished_goods_fifo_summary,read_inventory_valuation
 from db.wb_fifo_reconciliation import _fifo_reconciliation_preview_with_conn
 save('finished_lots',[lot(3,3,'0.335','1.01')],'valuation')
 with core.connect() as c:
  for i in range(2):
   mid=c.execute("INSERT INTO inventory_movements(event_key,movement_type,nm_id,movement_date,status,created_at) VALUES(?,'consumption',100,'2026-09-02','applied','now')",(str(i),)).lastrowid
   assert _move_finished_goods_fifo(c,mid,100,'A','Item',1,'ready',None,10)[0]==.34
  c.execute('UPDATE product_pipeline SET ready_units=1 WHERE nm_id=100')
  assert _finished_layer_totals(c,100,'ready',10)==(1,.33)
  c.execute('UPDATE product_pipeline SET ready_units=2 WHERE nm_id=100')
  reconciliation=_fifo_reconciliation_preview_with_conn(c).iloc[0]
  assert reconciliation['unit_cost_rub']==pytest.approx(.33)
  c.execute('UPDATE product_pipeline SET ready_units=1 WHERE nm_id=100')
 summary=read_finished_goods_fifo_summary().iloc[0];valuation=read_inventory_valuation().iloc[0]
 assert summary['ready_rate_rub']==pytest.approx(.33)
 assert valuation['ready_layer_amount']==pytest.approx(.33)
 assert valuation['ready_value_rub']==pytest.approx(.33)
 assert .68+valuation['ready_value_rub']==pytest.approx(1.01)


def test_valuation_snapshot_does_not_mix_concurrent_commit(monkeypatch):
 from db import finished_history_import as history
 from db.fifo_finished_goods import read_finished_goods_fifo_summary,_move_finished_goods_fifo
 save('finished_lots',[lot(3,3,'0.335','1.01')],'concurrent-values')
 original=history.register_valuation
 fired=[]
 def register(c):
  original(c)
  if not fired:
   fired.append(True)
   with core.connect() as writer:
    mid=writer.execute("INSERT INTO inventory_movements(event_key,movement_type,nm_id,movement_date,status,created_at) VALUES('concurrent','consumption',100,'2026-09-02','applied','now')").lastrowid
    _move_finished_goods_fifo(writer,mid,100,'A','Item',1,'ready',None,10)
    writer.execute('UPDATE product_pipeline SET ready_units=2 WHERE nm_id=100')
 monkeypatch.setattr(history,'register_valuation',register)
 old=read_finished_goods_fifo_summary().iloc[0]
 assert old['ready_layer_units']==3 and old['ready_rate_rub']*3==pytest.approx(1.01)
 fresh=read_finished_goods_fifo_summary().iloc[0]
 assert fresh['ready_layer_units']==2 and fresh['ready_rate_rub']*2==pytest.approx(.67)
