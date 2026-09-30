import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from cryptography.fernet import Fernet
from db import core,shared_warehouse as warehouse,shop_workspaces as shops,shop_request_bridge as bridge
from db.fifo_finished_goods import _create_finished_goods_layer

@pytest.fixture
def setup(monkeypatch,tmp_path):
    monkeypatch.setattr(core,'DB_PATH',tmp_path/'main.sqlite')
    monkeypatch.setattr(shops.config,'DATA_ENCRYPTION_KEY',Fernet.generate_key().decode())
    monkeypatch.setattr(shops.config,'DATA_ENCRYPTION_KEY_ID','test')
    core.init_db()
    shop=shops.create('Secondary','secret','verified-secondary')
    warehouse.register_shop(shop,'verified-secondary')
    warehouse.register_shop(0,'verified-primary')
    with core.connect() as conn:
        conn.execute("INSERT INTO products_catalog(nm_id,product_name,raw_json) VALUES(100,'Canonical',?)",('{"sizes":[{"chrtID":10}]}',))
        layer=_create_finished_goods_layer(conn,100,'canonical','Canonical','opening','seed','2026-09-01',3,20,'ready',chrt_id=10)
    item=warehouse.register_item(100,10,'Canonical')
    warehouse.map_sku(shop,777,10,item['id'])
    warehouse.map_sku(0,777,10,item['id'])
    snapshot_path=tmp_path/'child.sqlite'
    with sqlite3.connect(snapshot_path) as conn:conn.execute('CREATE TABLE financial_fact(value INTEGER)');conn.execute('INSERT INTO financial_fact VALUES(17)')
    bridge.initialize()
    return {'shop':shop,'layer':layer,'snapshot':snapshot_path.read_bytes()}

def operation(state,key='reserve'):
    quote=warehouse.preview(state['shop'],777,10,2,'2026-09-10','owner@test')
    return {'name':'reserve','arguments':{'shop_id':state['shop'],'nm_id':777,'chrt_id':10,
      'quantity':2,'document_date':'2026-09-10','source_ref':key,'request_key':key,'expected':quote['fingerprint']}}

def commit(state,key=None,base=0,operations=(),body=None):
    return bridge.commit(state['shop'],'verified-secondary',base,key or str(uuid.uuid4()),
      body or {'method':'POST','path':'/local/order','body':{'quantity':2}},'owner@test','owner',
      snapshot=state['snapshot'],response={'saved':True},operations=operations)

def test_shared_snapshot_allocator_and_receipt_commit_once_and_recover_lost_response(setup):
    key=str(uuid.uuid4());request={'method':'POST','path':'/local/order','body':{'quantity':2}}
    first=commit(setup,key,operations=[operation(setup)],body=request)
    assert first['bridge']['applied_revision']==first['bridge']['current_revision']==1
    # A distinct later snapshot must not prevent the original receipt recovery.
    commit(setup,base=1,body={'method':'PATCH','path':'/local/cost','body':{'cost':5}})
    replay=bridge.replay(setup['shop'],'verified-secondary',0,key,request,'owner@test','owner')
    assert replay['result']==first['result']
    assert replay['bridge']['applied_revision']==1 and replay['bridge']['current_revision']==2
    with core.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM warehouse_reservations').fetchone()[0]==1
        assert conn.execute('SELECT COUNT(*) FROM shop_request_receipts').fetchone()[0]==2
    assert shops.read(setup['shop'])['database_snapshot']==setup['snapshot']

def test_injected_snapshot_failure_rolls_back_already_applied_allocation_and_receipt(setup,monkeypatch):
    def fail(*args,**kwargs):raise RuntimeError('injected after canonical allocation')
    monkeypatch.setattr(shops,'store_snapshot_in_transaction',fail)
    with pytest.raises(RuntimeError):commit(setup,operations=[operation(setup)])
    with core.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM warehouse_reservations').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM warehouse_documents').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM shop_request_receipts').fetchone()[0]==0
        assert conn.execute('SELECT ready_units FROM finished_goods_cost_layers WHERE id=?',(setup['layer'],)).fetchone()[0]==3
    assert shops.read(setup['shop'])['revision']==0

def test_replay_rechecks_acl_and_active_identity_and_rejects_changed_request(setup):
    key=str(uuid.uuid4());body={'method':'PATCH','path':'/local/cost','body':{'cost':2}}
    commit(setup,key,body=body)
    with pytest.raises(warehouse.WarehouseConflict):bridge.replay(setup['shop'],'verified-secondary',1,key,body,'owner@test','owner')
    with pytest.raises(warehouse.WarehouseConflict):bridge.replay(setup['shop'],'verified-secondary',0,key,dict(body,body={'cost':3}),'owner@test','owner')
    warehouse.grant(setup['shop'],'manager@test',True,'owner')
    assert bridge.replay(setup['shop'],'verified-secondary',0,key,body,'manager@test','manager')
    warehouse.grant(setup['shop'],'manager@test',False,'owner')
    with pytest.raises(warehouse.WarehouseScopeError):bridge.replay(setup['shop'],'verified-secondary',0,key,body,'manager@test','manager')
    shops.deactivate(setup['shop'])
    with pytest.raises(warehouse.WarehouseScopeError):bridge.replay(setup['shop'],'verified-secondary',0,key,body,'owner@test','owner')

def test_concurrent_same_base_only_one_snapshot_commits(setup):
    with ThreadPoolExecutor(2) as pool:
        futures=[pool.submit(commit,setup,body={'index':index}) for index in range(2)]
    successes=0;conflicts=0
    for future in futures:
        try:future.result();successes+=1
        except warehouse.WarehouseConflict:conflicts+=1
    assert (successes,conflicts)==(1,1)
    assert shops.read(setup['shop'])['revision']==1

def test_foreign_reservation_and_untrusted_context_cannot_enter_bridge(setup):
    op=operation(setup);op['arguments']['shop_id']=0
    with pytest.raises(warehouse.WarehouseScopeError):commit(setup,operations=[op])
    op=operation(setup);op['arguments']['role']='owner'
    with pytest.raises(warehouse.WarehouseScopeError):commit(setup,operations=[op])
    with core.connect() as conn:assert conn.execute('SELECT COUNT(*) FROM warehouse_reservations').fetchone()[0]==0

def test_supplied_transaction_never_commits_or_initializes_and_receipts_are_append_only(setup):
    op=operation(setup)
    with core.connect() as conn:
        with pytest.raises(warehouse.WarehouseError):warehouse.reserve(**op['arguments'],_conn=conn)
        conn.execute('BEGIN IMMEDIATE')
        warehouse.reserve(**op['arguments'],_conn=conn)
        assert conn.in_transaction
        conn.rollback()
    with core.connect() as conn:assert conn.execute('SELECT COUNT(*) FROM warehouse_reservations').fetchone()[0]==0
    key=str(uuid.uuid4());commit(setup,key)
    with core.connect() as conn:
        for sql in ("UPDATE shop_request_receipts SET actor='changed'",'DELETE FROM shop_request_receipts',
                    'INSERT OR REPLACE INTO shop_request_receipts SELECT * FROM shop_request_receipts'):
            with pytest.raises(sqlite3.IntegrityError):conn.execute(sql)

def test_replay_receipt_and_context_share_one_consistent_read_snapshot(setup,monkeypatch):
    key=str(uuid.uuid4());request={'method':'PATCH','path':'/local/cost','body':{'cost':8}}
    original=bridge._scope;injected=False
    def scope_then_concurrent_commit(conn,*args,**kwargs):
        nonlocal injected
        record=original(conn,*args,**kwargs)
        if not injected:
            injected=True
            commit(setup,key,body=request)
        return record
    monkeypatch.setattr(bridge,'_scope',scope_then_concurrent_commit)
    # Replay began before the writer: its snapshot cannot mix old revision0
    # with a newly committed receipt applied_revision1.
    assert bridge.replay(setup['shop'],'verified-secondary',0,key,request,'owner@test','owner') is None
    replay=bridge.replay(setup['shop'],'verified-secondary',0,key,request,'owner@test','owner')
    assert replay['bridge']['applied_revision']==replay['bridge']['current_revision']==1
