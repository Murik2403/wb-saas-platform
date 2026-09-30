from concurrent.futures import ThreadPoolExecutor
import sqlite3
import pytest
from db import core,shared_warehouse as warehouse
from db.fifo_finished_goods import _create_finished_goods_layer,_move_finished_goods_fifo

@pytest.fixture(autouse=True)
def database(monkeypatch,tmp_path):
    monkeypatch.setattr(core,'DB_PATH',tmp_path/'warehouse.sqlite');core.init_db()
    with core.connect() as conn:
        conn.execute("INSERT INTO products_catalog(nm_id,product_name,raw_json) VALUES(100,'Canonical product',?)",('{"sizes":[{"chrtID":10},{"chrtID":20},{"chrtID":4000000000}]}',))
    warehouse.register_shop(0,'verified-primary',[('old-viewer',False),('old-manager',True)])
    warehouse.register_shop(1,'verified-secondary')

def layer(quantity=3,chrt_id=10,day='2026-09-01',cost=20,key='opening-1'):
    from db.production import _apply_pipeline_delta
    with core.connect() as conn:
        identifier=_create_finished_goods_layer(conn,100,'canon','Canonical product','opening',key,day,quantity,cost,'ready',chrt_id=chrt_id)
        _apply_pipeline_delta(conn,100,'canon',quantity,0)
        return identifier
def mapping(chrt_id=10,external_nm_id=777):
    item=warehouse.register_item(100,chrt_id,'Canonical product')
    for shop in (0,1):warehouse.map_sku(shop,external_nm_id,chrt_id,item['id'],'owner@test')
    return item
def reserve(shop=0,quantity=2,key='reserve-1',day='2026-09-10',chrt_id=10):
    preview=warehouse.preview(shop,777,chrt_id,quantity,day,'owner@test')
    return warehouse.reserve(shop,777,chrt_id,quantity,day,key,key,preview['fingerprint'],'owner@test')

def test_primary_sid_uniqueness_rotation_and_explicit_acl_migration():
    with pytest.raises(warehouse.WarehouseConflict):warehouse.register_shop(2,'verified-primary')
    with pytest.raises(warehouse.WarehouseConflict):warehouse.register_shop(0,'new-seller')
    assert warehouse.register_shop(0,'verified-primary')['shop_id']==0
    with core.connect() as conn:
        warehouse.access(conn,0,'old-viewer','viewer')
        with pytest.raises(warehouse.WarehouseScopeError):warehouse.access(conn,1,'old-manager','manager')
        with pytest.raises(warehouse.WarehouseScopeError):warehouse.access(conn,0,'old-viewer','viewer',True)
    warehouse.grant(1,'old-manager',True,'owner')
    with core.connect() as conn:
        warehouse.access(conn,1,'old-manager','manager',True)
        with pytest.raises(warehouse.WarehouseScopeError):warehouse.access(conn,1,'old-manager','integration_reader')

def test_same_external_nm_id_never_joins_without_explicit_exact_variant_mapping():
    layer();item=warehouse.register_item(100,10,'Canonical product');warehouse.map_sku(0,777,10,item['id'])
    with pytest.raises(warehouse.WarehouseScopeError):warehouse.preview(1,777,10,1,'2026-09-10')
    with pytest.raises(warehouse.WarehouseScopeError):warehouse.preview(0,777,0,1,'2026-09-10')
    other=warehouse.register_item(100,20,'Other size')
    with pytest.raises(warehouse.WarehouseConflict):warehouse.map_sku(0,777,10,other['id'])

def test_reservation_preview_readonly_fifo_dates_and_immutable_provenance():
    first=layer(1,cost=10);second=layer(2,cost=30,key='opening-2',day='2026-09-05');layer(9,cost=99,key='future',day='2026-10-01');mapping()
    preview=warehouse.preview(0,777,10,3,'2026-09-10')
    assert [x['layer_id'] for x in preview['layers']]==[first,second]
    assert preview['missing_units']==0 and sum(x['cost_rub'] for x in preview['layers'])==70
    with core.connect() as conn:assert conn.execute('SELECT COUNT(*) FROM warehouse_reservations').fetchone()[0]==0
    result=reserve(quantity=3)
    with core.connect() as conn:
        assert conn.execute('SELECT SUM(ready_units) FROM finished_goods_cost_layers').fetchone()[0]==12
        snapshots=conn.execute('SELECT provenance_json FROM warehouse_reservation_layers WHERE reservation_id=?',(result['id'],)).fetchall()
        assert all('source_date' in row[0] and 'chrt_id' in row[0] for row in snapshots)

def test_concurrent_two_shops_cannot_both_reserve_two_of_three():
    layer();mapping();previews={shop:warehouse.preview(shop,777,10,2,'2026-09-10') for shop in (0,1)}
    def attempt(shop):
        try:return warehouse.reserve(shop,777,10,2,'2026-09-10',f'order-{shop}',f'reserve-{shop}',previews[shop]['fingerprint'])
        except warehouse.WarehouseError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,[0,1]))
    assert sum(result is not None for result in results)==1
    with core.connect() as conn:assert conn.execute("SELECT SUM(units) FROM warehouse_reservation_layers WHERE status='reserved'").fetchone()[0]==2

def test_exact_replay_conflict_release_and_read_role():
    layer();mapping();preview=warehouse.preview(0,777,10,2,'2026-09-10')
    args=(0,777,10,2,'2026-09-10','order-1','reserve-key',preview['fingerprint'])
    reservation=warehouse.reserve(*args)
    assert warehouse.reserve(*args)['id']==reservation['id']
    with pytest.raises(warehouse.WarehouseConflict):warehouse.reserve(0,777,10,1,'2026-09-10','order-1','other-key',preview['fingerprint'])
    with pytest.raises(warehouse.WarehouseScopeError):warehouse.reserve(*args,actor='old-viewer',role='viewer')
    result=warehouse.transition(reservation['id'],'release','cancel-order','cancel-key','2026-09-11',1)
    assert warehouse.transition(reservation['id'],'release','cancel-order','cancel-key','2026-09-11',1)==result
    assert warehouse.preview(1,777,10,3,'2026-09-12')['missing_units']==0

def test_own_commit_keeps_foreign_reservation_and_receipt_costs():
    layer(4,cost=25);mapping();a=reserve(0,2,'order-a');b=reserve(1,2,'order-b')
    committed=warehouse.transition(a['id'],'commit','dispatch-a','commit-a','2026-09-11',1,'owner@test')
    assert committed['cost_rub']==50 and committed['status']=='inbound'
    with core.connect() as conn:
        physical=conn.execute('SELECT ready_units,inbound_units,wb_units FROM finished_goods_cost_layers').fetchone()
        assert tuple(physical)==(2,2,0)
        assert warehouse.reserved_units(conn,1)==2
    receipt=warehouse.transition(a['id'],'receipt','receipt-a','receipt-key','2026-09-12',2)
    assert receipt['status']=='wb' and receipt['cost_rub']==50
    with core.connect() as conn:
        assert tuple(conn.execute('SELECT ready_units,inbound_units,wb_units FROM finished_goods_cost_layers').fetchone())==(2,0,2)
        assert conn.execute('SELECT status FROM warehouse_reservations WHERE id=?',(b['id'],)).fetchone()[0]=='reserved'

def test_existing_fifo_cannot_bypass_reserves_or_synthesize_managed_stock():
    layer();mapping();reserve()
    with core.connect() as conn:
        with pytest.raises(warehouse.WarehouseScopeError):_move_finished_goods_fifo(conn,1,100,'','',1,'ready',None,10)
        with pytest.raises(warehouse.WarehouseScopeError):_create_finished_goods_layer(conn,100,'','','synthetic_sale','fake','2026-09-10',1,20,'wb',chrt_id=10)
        with pytest.raises(sqlite3.IntegrityError):conn.execute('UPDATE finished_goods_cost_layers SET ready_units=1')

def test_conflict_second_layer_rolls_back_own_release_and_physical_move():
    layer(1,key='one');second=layer(1,key='two');mapping();reservation=reserve(quantity=2)
    with core.connect() as conn:
        conn.execute(f"CREATE TRIGGER fail_second BEFORE UPDATE OF ready_units ON finished_goods_cost_layers WHEN OLD.id={second} BEGIN SELECT RAISE(ABORT,'SHARED_WAREHOUSE: injected second layer failure'); END")
    with pytest.raises(warehouse.WarehouseStockError):warehouse.transition(reservation['id'],'commit','dispatch','commit','2026-09-11',1)
    with core.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM warehouse_reservation_layers WHERE status='reserved'").fetchone()[0]==2
        assert conn.execute('SELECT SUM(ready_units),SUM(inbound_units) FROM finished_goods_cost_layers').fetchone()[0]==2
        assert conn.execute('SELECT COUNT(*) FROM warehouse_ownership').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM inventory_movements').fetchone()[0]==0

def test_history_update_delete_replace_guards():
    layer();mapping();reserve()
    with core.connect() as conn:
        for sql in ["DELETE FROM warehouse_documents","UPDATE warehouse_events SET actor='spoof'","INSERT OR REPLACE INTO warehouse_documents SELECT * FROM warehouse_documents","INSERT OR REPLACE INTO warehouse_events SELECT * FROM warehouse_events"]:
            with pytest.raises(sqlite3.IntegrityError):conn.execute(sql)

def test_reservation_provenance_and_ownership_sql_guards():
    layer();mapping();reservation=reserve()
    with core.connect() as conn:
        for sql in ["UPDATE finished_goods_cost_layers SET unit_cost_rub=99", "UPDATE finished_goods_cost_layers SET nm_id=999", "UPDATE warehouse_reservation_layers SET units=1", "UPDATE warehouse_reservation_layers SET provenance_json='{}'", "DELETE FROM warehouse_reservation_layers", "INSERT OR REPLACE INTO warehouse_reservation_layers SELECT * FROM warehouse_reservation_layers"]:
            with pytest.raises(sqlite3.IntegrityError):conn.execute(sql)
    warehouse.transition(reservation['id'],'commit','dispatch','commit','2026-09-11',1)
    with core.connect() as conn:
        for sql in ["UPDATE warehouse_ownership SET inbound_units=3", "UPDATE warehouse_ownership SET shop_id=1", "DELETE FROM warehouse_ownership", "INSERT OR REPLACE INTO warehouse_ownership SELECT * FROM warehouse_ownership"]:
            with pytest.raises(sqlite3.IntegrityError):conn.execute(sql)

def test_receipt_cannot_predate_commit_and_revision_is_strict():
    layer();mapping();reservation=reserve()
    with pytest.raises(warehouse.WarehouseError):warehouse.transition(reservation['id'],'commit','dispatch','commit','2026-09-11',True)
    warehouse.transition(reservation['id'],'commit','dispatch','commit','2026-09-12',1)
    with pytest.raises(warehouse.WarehouseConflict):warehouse.transition(reservation['id'],'receipt','receipt','receipt','2026-09-11',2)
    with core.connect() as conn:assert tuple(conn.execute('SELECT ready_units,inbound_units,wb_units FROM finished_goods_cost_layers').fetchone())==(1,2,0)

@pytest.mark.parametrize('is_return',[0,1])
@pytest.mark.parametrize('raw_variant',[10,0,999])
def test_legacy_sale_and_return_never_synthesize_or_restore_shared_stock(is_return,raw_variant):
    from db.fifo_sales import initialize_sales_fifo_tracking,process_sales_fifo_events
    initialize_sales_fifo_tracking();layer();mapping()
    with core.connect() as conn:
        import json
        conn.execute("INSERT INTO sales(id,sale_date,last_change,nm_id,supplier_article,is_return,raw_json) VALUES('event','2026-09-11','2026-09-11',100,'canon',?,?)",(is_return,json.dumps({'chrtID':raw_variant,'srid':'shared'})))
    result=process_sales_fifo_events()
    assert result['errors']==1 and result['processed']==0
    with core.connect() as conn:
        assert tuple(conn.execute('SELECT ready_units,inbound_units,wb_units FROM finished_goods_cost_layers').fetchone())==(3,0,0)
        assert conn.execute('SELECT COUNT(*) FROM finished_goods_cost_layers').fetchone()[0]==1
        assert conn.execute('SELECT COUNT(*) FROM inventory_movements').fetchone()[0]==0
        assert 'SHARED_SCOPE_REQUIRED' in conn.execute('SELECT note FROM sales_fifo_events').fetchone()[0]

def test_large_catalog_variant_and_unknown_variant_rejected():
    assert warehouse.register_item(100,4000000000,'Large variant')['chrt_id']==4000000000
    with pytest.raises(ValueError):warehouse.register_item(100,999,'Unknown variant')

def test_quarantine_cannot_double_claim_reserves_or_owned_physical_quantity():
    identifier=layer();mapping();reservation=reserve()
    with core.connect() as conn:
        for location,units in [('ready',2)]:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO incident_holds(case_id,layer_id,location,units,request_key,status,actor,created_at) VALUES(1,?,?,?,'hold-key','held','owner','2026-09-10')",(identifier,location,units))
    warehouse.transition(reservation['id'],'commit','dispatch','commit','2026-09-11',1)
    with core.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO incident_holds(case_id,layer_id,location,units,request_key,status,actor,created_at) VALUES(1,?,'inbound',1,'hold-key','held','owner','2026-09-10')",(identifier,))
        assert tuple(conn.execute('SELECT ready_units,inbound_units FROM product_pipeline WHERE nm_id=100').fetchone())==(1,2)
        assert tuple(conn.execute("SELECT goods_cost_rub,goods_unit_cost_rub FROM inventory_movements WHERE movement_type='shared_commit'").fetchone())==(40,20)

def test_parent_identity_replace_update_delete_guards():
    layer();mapping();reserve()
    with core.connect() as conn:
        for table,column in [('warehouse_shops','identity'),('warehouse_items','nm_id'),('warehouse_mappings','shop_identity'),('warehouse_reservations','shop_identity')]:
            for sql in [f'INSERT OR REPLACE INTO {table} SELECT * FROM {table}',f'DELETE FROM {table}',f"UPDATE {table} SET {column}='spoof'"]:
                with pytest.raises(sqlite3.IntegrityError):conn.execute(sql)

def test_public_incident_workspace_hold_release_and_stale_preview():
    from db.incident_workflow import hold,release,workspace
    from db.wb_incidents import create_wb_incident_case
    case=create_wb_incident_case('Review','2026-09-10',document_ref='report')
    identifier=layer();mapping();reserve()
    report=workspace(case)['layers'][0]
    assert report['allocated_ready_units']==2 and report['available_ready_units']==1
    preview=warehouse.preview(1,777,10,1,'2026-09-11')
    with pytest.raises(ValueError):hold(case,identifier,'ready',2,'hold-fail','owner')
    held=hold(case,identifier,'ready',1,'hold-free','owner')
    assert workspace(case)['layers'][0]['available_ready_units']==0
    with pytest.raises(warehouse.WarehouseConflict):warehouse.reserve(1,777,10,1,'2026-09-11','other','other',preview['fingerprint'])
    release(case,held,'Resolved','owner')
    assert workspace(case)['layers'][0]['available_ready_units']==1

def test_supply_snapshot_subtracts_shop_reserves_and_fingerprints_release():
    from db.supply_planning import load_supply_plan_snapshot,build_supply_preview
    layer();mapping()
    def snapshot():
        with core.connect() as conn:return load_supply_plan_snapshot(conn,'2026-09-01','2026-09-30')
    initial=snapshot();first=reserve(quantity=2)
    partial=snapshot()
    assert initial['intermediate_layers'][0]['ready_units']==3
    assert partial['intermediate_layers'][0]['ready_units']==1
    assert build_supply_preview(initial)['preview_fingerprint']!=build_supply_preview(partial)['preview_fingerprint']
    second=reserve(1,1,'second')
    assert snapshot()['intermediate_layers'][0]['ready_units']==0
    warehouse.transition(first['id'],'release','release','release','2026-09-11',1)
    released=snapshot()
    assert released['intermediate_layers'][0]['ready_units']==2
    assert partial['revisions']['shared_reservations']!=released['revisions']['shared_reservations']
