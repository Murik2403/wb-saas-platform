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

def received_reservation(shop=0,quantity=2,key='order'):
    reservation=reserve(shop,quantity,key)
    warehouse.transition(reservation['id'],'commit',key+'-dispatch',key+'-commit','2026-09-11',1)
    warehouse.transition(reservation['id'],'receipt',key+'-receipt',key+'-received','2026-09-12',2)
    return reservation

def sale(reservation,quantity=1,key='sale',day='2026-09-13'):
    preview=warehouse.preview_sale(reservation['id'],quantity,day)
    return warehouse.sell(reservation['id'],quantity,day,key,key,preview['fingerprint'])

def test_sale_and_matched_return_preserve_owner_original_fifo_cost_and_replay():
    layer(1,cost=10,key='first');layer(2,cost=30,key='second');mapping()
    reservation=received_reservation(quantity=3)
    preview=warehouse.preview_sale(reservation['id'],2,'2026-09-13')
    args=(reservation['id'],2,'2026-09-13','sale','sale',preview['fingerprint'])
    result=warehouse.sell(*args)
    assert result['cost_rub']==40 and warehouse.sell(*args)==result
    with pytest.raises(warehouse.WarehouseConflict):warehouse.return_sale('sale',1,'2026-09-14','return','return',4)
    plan=[{'layer_id':result['allocations'][1]['layer_id'],'units':1}]
    returned=warehouse.return_sale('sale',1,'2026-09-14','return','return',4,allocation_plan=plan,allocation_proof='Customer return document: confirmed second lot')
    assert returned['cost_rub']==30 and warehouse.return_sale('sale',1,'2026-09-14','return','return',4,allocation_plan=plan,allocation_proof='Customer return document: confirmed second lot')==returned
    with core.connect() as conn:
        assert [tuple(row) for row in conn.execute('SELECT wb_units FROM finished_goods_cost_layers ORDER BY id')]==[(0,),(2,)]
        assert conn.execute('SELECT SUM(wb_units) FROM warehouse_ownership').fetchone()[0]==2
        assert conn.execute('SELECT SUM(returned_units) FROM warehouse_sale_layers').fetchone()[0]==1

def test_no_cross_shop_double_sale_no_unmatched_or_over_return():
    layer(4);mapping();a=received_reservation(0,2,'a');b=received_reservation(1,2,'b')
    assert sale(a,2,'sale-a')['cost_rub']==40
    with pytest.raises(warehouse.WarehouseStockError):sale(a,1,'extra-a')
    assert warehouse.preview_sale(b['id'],2,'2026-09-13')['missing_units']==0
    with pytest.raises(warehouse.WarehouseScopeError):warehouse.return_sale('unknown',1,'2026-09-14','r','r',4)
    with pytest.raises(warehouse.WarehouseStockError):warehouse.return_sale('sale-a',3,'2026-09-14','r','r',4)
    warehouse.return_sale('sale-a',2,'2026-09-14','return-a','return-a',4)
    with pytest.raises(warehouse.WarehouseStockError):warehouse.return_sale('sale-a',1,'2026-09-15','extra','extra',5)

def test_sale_return_acl_stale_and_header_provenance_guards():
    layer();mapping();reservation=received_reservation()
    preview=warehouse.preview_sale(reservation['id'],1,'2026-09-13')
    sale(reservation,1,'first-sale')
    with pytest.raises(warehouse.WarehouseConflict):warehouse.sell(reservation['id'],1,'2026-09-13','stale','stale',preview['fingerprint'])
    with pytest.raises(warehouse.WarehouseScopeError):warehouse.return_sale('first-sale',1,'2026-09-14','r','r',4,'old-viewer','viewer')
    with core.connect() as conn:
        for sql in ['DELETE FROM warehouse_sale_layers','UPDATE warehouse_sale_layers SET units=99','INSERT OR REPLACE INTO warehouse_sale_layers SELECT * FROM warehouse_sale_layers','UPDATE warehouse_sale_layers SET returned_units=99']:
            with pytest.raises(sqlite3.IntegrityError):conn.execute(sql)

def test_three_partial_returns_conserve_original_sale_monetary_residual():
    layer(3,cost=.335);mapping();reservation=received_reservation(quantity=3)
    sold=sale(reservation,3)
    returned=[]
    for index in range(3):
        result=warehouse.return_sale('sale',1,'2026-09-14',f'return-{index}',f'return-{index}',4+index)
        returned.append(result['cost_rub'])
    assert sold['cost_rub']==1.01 and returned==[.34,.33,.34]
    assert round(sum(returned),2)==sold['cost_rub']
    with core.connect() as conn:
        assert tuple(conn.execute('SELECT wb_units,wb_cost_rub FROM warehouse_ownership').fetchone())==(3,1.01)
        assert tuple(conn.execute('SELECT amount_rub,returned_amount_rub FROM warehouse_sale_layers').fetchone())==(1.01,1.01)
    assert sale(reservation,3,'resale','2026-09-15')['cost_rub']==1.01

def test_fragmented_sales_and_returns_preserve_original_owned_monetary_basis():
    layer(3,cost=.335);mapping();reservation=received_reservation(quantity=3)
    amounts=[sale(reservation,1,f'sale-{index}')['cost_rub'] for index in range(3)]
    assert amounts==[.34,.34,.33] and round(sum(amounts),2)==1.01
    with core.connect() as conn:assert tuple(conn.execute('SELECT wb_units,wb_cost_rub FROM warehouse_ownership').fetchone())==(0,0)
    returned=[warehouse.return_sale(f'sale-{index}',1,'2026-09-14',f'return-{index}',f'return-{index}',6+index)['cost_rub'] for index in range(3)]
    assert returned==amounts
    assert sale(reservation,3,'resale','2026-09-15')['cost_rub']==1.01

def test_explicit_return_allocation_validation_never_guesses_unknown_layers():
    layer(1,cost=10,key='one');layer(1,cost=20,key='two');mapping();reservation=received_reservation(quantity=2);sale(reservation,2)
    for plan in [[{'layer_id':999,'units':1}],[{'layer_id':1,'units':2}],[{'layer_id':1,'units':True}],[{'layer_id':1,'units':1},{'layer_id':1,'units':1}]]:
        with pytest.raises(warehouse.WarehouseError):warehouse.return_sale('sale',1,'2026-09-14','return','return',4,allocation_plan=plan)
    with core.connect() as conn:assert conn.execute('SELECT SUM(returned_units) FROM warehouse_sale_layers').fetchone()[0]==0

def test_cross_shop_fractional_reserve_release_and_last_residual_conserve_layer_basis():
    layer(6,cost=.335);mapping()
    first=reserve(0,3,'first');second=reserve(1,3,'second')
    with core.connect() as conn:
        assert [row[0] for row in conn.execute('SELECT cost_rub FROM warehouse_reservation_layers ORDER BY rowid')]==[1.01,1.0]
        assert conn.execute('SELECT SUM(cost_rub) FROM warehouse_reservation_layers').fetchone()[0]==2.01
    warehouse.transition(first['id'],'release','release','release','2026-09-11',1)
    replaced=reserve(0,3,'replaced')
    with core.connect() as conn:
        assert conn.execute('SELECT cost_rub FROM warehouse_reservation_layers WHERE reservation_id=?',(replaced['id'],)).fetchone()[0]==1.01
        assert conn.execute("SELECT SUM(cost_rub) FROM warehouse_reservation_layers WHERE status='reserved'").fetchone()[0]==2.01

def test_quarantine_money_stays_with_held_units_not_entire_free_reserve():
    from db.incident_workflow import hold,release
    from db.wb_incidents import create_wb_incident_case
    identifier=layer(3,cost=.335);mapping();case=create_wb_incident_case('Review','2026-09-10',document_ref='proof')
    held=hold(case,identifier,'ready',2,'hold-key','owner')
    first=reserve(0,1,'free')
    with core.connect() as conn:assert conn.execute('SELECT cost_rub FROM warehouse_reservation_layers WHERE reservation_id=?',(first['id'],)).fetchone()[0]==.34
    release(case,held,'Resolved','owner');second=reserve(1,2,'remaining')
    with core.connect() as conn:assert conn.execute('SELECT cost_rub FROM warehouse_reservation_layers WHERE reservation_id=?',(second['id'],)).fetchone()[0]==.67

def test_owned_quarantine_is_subset_and_blocks_sale_until_documented_release():
    identifier=layer(4);mapping();a=received_reservation(0,2,'a');b=received_reservation(1,2,'b')
    held=warehouse.quarantine_owned(a['id'],identifier,'wb',1,'2026-09-13','damaged','hold-a',3,'Inspected damaged unit')
    assert warehouse.preview_sale(a['id'],2,'2026-09-13')['missing_units']==1
    assert warehouse.preview_sale(b['id'],2,'2026-09-13')['missing_units']==0
    with core.connect() as conn:
        assert conn.execute('SELECT SUM(wb_units) FROM warehouse_ownership').fetchone()[0]==4
        assert conn.execute('SELECT wb_units FROM finished_goods_cost_layers').fetchone()[0]==4
        for sql in ["UPDATE warehouse_ownership SET wb_units=0,wb_cost_rub=0 WHERE reservation_id='"+a['id']+"'",'DELETE FROM warehouse_owned_holds','INSERT OR REPLACE INTO warehouse_owned_holds SELECT * FROM warehouse_owned_holds','UPDATE warehouse_owned_holds SET units=99']:
            with pytest.raises(sqlite3.IntegrityError):conn.execute(sql)
    assert sale(a,1,'free-unit')['cost_rub']==20
    with pytest.raises(warehouse.WarehouseStockError):sale(a,1,'held-unit')
    released=warehouse.release_owned_hold(held['hold_id'],'2026-09-14','resolved','release-a',5,'Inspection resolved')
    assert warehouse.release_owned_hold(held['hold_id'],'2026-09-14','resolved','release-a',5,'Inspection resolved')==released
    assert sale(a,1,'released-unit','2026-09-15')['cost_rub']==20

def test_owned_inbound_quarantine_prevents_receipt_without_mutating_physics():
    identifier=layer();mapping();reservation=reserve()
    warehouse.transition(reservation['id'],'commit','dispatch','commit','2026-09-11',1)
    warehouse.quarantine_owned(reservation['id'],identifier,'inbound',1,'2026-09-12','damaged','hold',2,'Inspection')
    with pytest.raises(warehouse.WarehouseStockError):warehouse.transition(reservation['id'],'receipt','receipt','receipt','2026-09-13',3)
    with core.connect() as conn:assert tuple(conn.execute('SELECT ready_units,inbound_units,wb_units FROM finished_goods_cost_layers').fetchone())==(1,2,0)

def test_return_quarantine_is_atomic_and_replay_cannot_change_disposition():
    layer(1);mapping();reservation=received_reservation(quantity=1);sale(reservation)
    result=warehouse.return_sale('sale',1,'2026-09-14','return','return',4,allocation_proof='Damaged customer return',disposition='quarantine')
    assert warehouse.return_sale('sale',1,'2026-09-14','return','return',4,allocation_proof='Damaged customer return',disposition='quarantine')==result
    with pytest.raises(warehouse.WarehouseConflict):warehouse.return_sale('sale',1,'2026-09-14','return','return',4,disposition='available')
    assert warehouse.preview_sale(reservation['id'],1,'2026-09-15')['missing_units']==1
    with core.connect() as conn:
        assert tuple(conn.execute('SELECT wb_units,wb_cost_rub FROM warehouse_ownership').fetchone())==(1,20)
        assert conn.execute("SELECT SUM(units) FROM warehouse_owned_holds WHERE status='held'").fetchone()[0]==1

def test_stage_one_nonempty_ownership_money_and_state_trigger_upgrade():
    layer(3,cost=.335);mapping();reservation=received_reservation(quantity=3)
    with core.connect() as conn:
        conn.execute('DROP TRIGGER warehouse_owned_money_insert');conn.execute('DROP TRIGGER warehouse_owned_money_update')
        conn.execute('ALTER TABLE warehouse_ownership DROP COLUMN inbound_cost_rub');conn.execute('ALTER TABLE warehouse_ownership DROP COLUMN wb_cost_rub')
        conn.execute('DROP TRIGGER warehouse_reservation_state_guard')
        conn.execute("CREATE TRIGGER warehouse_reservation_state_guard BEFORE UPDATE OF status,revision ON warehouse_reservations WHEN NOT (OLD.status='inbound' AND NEW.status='wb') BEGIN SELECT RAISE(ABORT,'old stage-one transition'); END")
    sold=sale(reservation,3)
    assert sold['cost_rub']==1.01
    returned=warehouse.return_sale('sale',1,'2026-09-14','return','return',4)
    assert returned['cost_rub']==.34

def test_atomic_quarantine_return_failure_rolls_back_physical_money_and_return_count():
    layer(1);mapping();reservation=received_reservation(quantity=1);sale(reservation)
    with core.connect() as conn:conn.execute("CREATE TRIGGER fail_return_hold BEFORE INSERT ON warehouse_owned_holds BEGIN SELECT RAISE(ABORT,'SHARED_WAREHOUSE: injected hold failure'); END")
    with pytest.raises(warehouse.WarehouseStockError):warehouse.return_sale('sale',1,'2026-09-14','return','return',4,allocation_proof='Damaged',disposition='quarantine')
    with core.connect() as conn:
        assert tuple(conn.execute('SELECT wb_units,wb_cost_rub FROM warehouse_ownership').fetchone())==(0,0)
        assert conn.execute('SELECT wb_units FROM finished_goods_cost_layers').fetchone()[0]==0
        assert conn.execute('SELECT returned_units FROM warehouse_sale_layers').fetchone()[0]==0
        assert conn.execute("SELECT COUNT(*) FROM warehouse_documents WHERE kind='return'").fetchone()[0]==0

def test_concurrent_sales_and_returns_cannot_double_apply_quantity():
    layer(1);mapping();reservation=received_reservation(quantity=1)
    preview=warehouse.preview_sale(reservation['id'],1,'2026-09-13')
    def sell_once(key):
        try:return warehouse.sell(reservation['id'],1,'2026-09-13',key,key,preview['fingerprint'])
        except warehouse.WarehouseError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(sell_once,['sale-a','sale-b']))
    assert sum(result is not None for result in results)==1
    source='sale-a' if results[0] else 'sale-b'
    def return_once(key):
        try:return warehouse.return_sale(source,1,'2026-09-14',key,key,4)
        except warehouse.WarehouseError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(return_once,['return-a','return-b']))
    assert sum(result is not None for result in results)==1
    with core.connect() as conn:
        assert conn.execute('SELECT wb_units FROM finished_goods_cost_layers').fetchone()[0]==1
        assert conn.execute('SELECT returned_units FROM warehouse_sale_layers').fetchone()[0]==1

def test_schema_initialization_never_commits_callers_business_transaction():
    with core.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute("INSERT INTO app_meta(key,value,updated_at) VALUES('uncommitted','true','2026-09-30')")
        with pytest.raises(warehouse.WarehouseError):warehouse.initialize(conn)
        assert conn.in_transaction
        conn.rollback()
    with core.connect() as conn:assert not conn.execute("SELECT 1 FROM app_meta WHERE key='uncommitted'").fetchone()
