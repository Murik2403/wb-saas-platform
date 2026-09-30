import base64
import json
import sqlite3
import uuid
from datetime import date
from unittest.mock import patch
import pytest
from cryptography.fernet import Fernet
import config
import db.core as core
from db.core import connect
from db import shared_warehouse as warehouse, shop_workspaces as shops
from db.ingest import upsert_financial
from db.fifo_finished_goods import _create_finished_goods_layer
from db.production import _apply_pipeline_delta
from db.financial_fifo_reconciliation import read_financial_fifo_reconciliation
import shared_warehouse_setup as setup
import shared_warehouse_finance as finance

def token(seller):return 'x.'+base64.urlsafe_b64encode(json.dumps({'sid':seller}).encode()).decode().rstrip('=')+'.x'
def key():return str(uuid.uuid4())
def financial(rrd=1,quantity=1,chrt=10):return dict(rrd_id=rrd,nm_id=100,chrt_id=chrt,quantity=quantity,rr_dt='2026-09-11',sale_dt='2026-09-11',doc_type_name='Продажа',retail_amount=100*quantity,ppvz_for_pay=100*quantity)

@pytest.fixture
def state(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'DB_PATH',tmp_path/'main.sqlite3')
    monkeypatch.setattr(config,'DATA_DIR',tmp_path)
    monkeypatch.setattr(config,'TENANT_SLUG','trusted-tenant')
    monkeypatch.setattr(config,'DATA_ENCRYPTION_KEY',Fernet.generate_key().decode())
    monkeypatch.setattr(config,'DATA_ENCRYPTION_KEYRING','')
    monkeypatch.setattr(config,'get_token',lambda:token('primary'))
    core.init_db();finance.initialize()
    warehouse.register_shop(0,shops.account_identity(token('primary')))
    with connect() as conn:
        conn.execute("INSERT INTO products_catalog(nm_id,product_name,raw_json) VALUES(100,'Canonical',?)",(json.dumps({'sizes':[{'chrtID':10}]}),))
        _create_finished_goods_layer(conn,100,'canonical','Canonical','production','trusted-production','2026-09-01',3,.335,'ready',chrt_id=10)
        _apply_pipeline_delta(conn,100,'canonical',3,0)
    item=setup.register_item(100,10,'Canonical','owner','owner',key())
    preview=setup.mapping_preview(0,100,10,item['id'],'owner','owner')
    setup.mapping_confirm(0,100,10,item['id'],preview['fingerprint'],'owner','owner',key())
    return dict(item=item,tmp_path=tmp_path)

def sale(shop,quantity,chrt):
    day='2026-09-10'
    preview=warehouse.preview(shop,100,chrt,quantity,day,'owner','owner')
    reserved=warehouse.reserve(shop,100,chrt,quantity,day,'order-'+str(shop),key(),preview['fingerprint'],'owner','owner')
    for revision,kind in ((1,'commit'),(2,'receipt')):
        warehouse.transition(reserved['id'],kind,kind+str(shop),key(),day,revision,'owner','owner')
    with connect() as conn:
        preview=warehouse._sale_preview(conn,warehouse._reservation(conn,reserved['id'],'owner','owner',False),quantity,'2026-09-11')
        preview['fingerprint']=warehouse.digest(preview)
    document=key()
    result=warehouse.sell(reserved['id'],quantity,'2026-09-11','sale'+str(shop),document,preview['fingerprint'],'owner','owner')
    return document,result

def bind(shop,rrd,document,manual=False,proof=None,request_key=None):
    preview=finance.preview(shop,rrd,document,manual,proof,'owner','owner')
    return finance.confirm(shop,rrd,document,manual,proof,preview['fingerprint'],'owner','owner',request_key or key()),preview

def test_primary_verified_binding_replaces_cogs_once_and_stale_facts_downgrade(state):
    document,result=sale(0,3,10)
    identity=shops.account_identity(token('primary'))
    upsert_financial([financial(quantity=3)],source_identity=identity)
    request_key=key();applied,preview=bind(0,1,document,request_key=request_key)
    assert applied['cost_rub']==1.01 and not applied['preliminary']
    rows=read_financial_fifo_reconciliation('2026-09-11','2026-09-11')
    assert rows.iloc[0]['eligible_fifo_cogs_rub']==1.01 and rows.iloc[0]['reason']=='shared_fifo_verified_source'
    from calculations import build_dashboard
    dashboard=build_dashboard(date(2026,9,11),date(2026,9,11))
    assert dashboard.financial['cost']==1.01 and dashboard.financial['profit']==298.99
    with connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM sales_fifo_events').fetchone()[0]==0
        for sql in ('DELETE FROM warehouse_financial_bindings','UPDATE warehouse_financial_bindings SET cost_rub=999','INSERT OR REPLACE INTO warehouse_financial_bindings SELECT * FROM warehouse_financial_bindings'):
            with pytest.raises(sqlite3.IntegrityError):conn.execute(sql)
    upsert_financial([{**financial(quantity=3),'penalty':1}],source_identity=identity)
    assert read_financial_fifo_reconciliation('2026-09-11','2026-09-11').iloc[0]['reason']=='shared_source_changed'
    assert finance.confirm(0,1,document,False,None,preview['fingerprint'],'owner','owner',request_key)==applied
    with pytest.raises(warehouse.WarehouseScopeError):finance.confirm(0,1,document,False,None,preview['fingerprint'],'owner','viewer',request_key)

def test_legacy_manual_confirmation_is_audited_preliminary_and_rotation_fails_closed(state,monkeypatch):
    document,_=sale(0,1,10)
    upsert_financial([financial()])
    with pytest.raises(warehouse.WarehouseConflict):finance.preview(0,1,document,False,None,'owner','owner')
    applied,_=bind(0,1,document,True,'Confirmed original seller document #1')
    assert applied['source_kind']=='manual_legacy' and applied['preliminary']
    from calculations import build_dashboard
    result=build_dashboard(date(2026,9,11),date(2026,9,11)).financial
    assert result['cost']==.34 and result['shared_fifo_manual_source_rows']==1 and not result['financial_period_complete']
    assert result['cogs_quality']=='estimated' and not result['cogs_complete'] and result['exact_unit_coverage_percent']==0
    assert result['manual_source_cogs_rub']==.34 and result['current_cost_estimate_rub']==0
    product=build_dashboard(date(2026,9,11),date(2026,9,11)).financial_products.iloc[0]
    assert product['Источник себестоимости']=='estimated' and not product['Покрытие себестоимости полное'] and product['FIFO по ручному источнику']==.34
    monkeypatch.setattr(config,'get_token',lambda:token('rotated-other-seller'))
    assert read_financial_fifo_reconciliation('2026-09-11','2026-09-11').iloc[0]['reason']=='shared_seller_changed'

def secondary(state,monkeypatch):
    identity=shops.account_identity(token('secondary'))
    shop=shops.create('Secondary',token('secondary'),identity,register_warehouse=True)
    with monkeypatch.context() as child:
        child.setattr(core,'DB_PATH',state['tmp_path']/'child.sqlite3')
        core.init_db()
        with connect() as conn:conn.execute("INSERT INTO products_catalog(nm_id,product_name,raw_json) VALUES(100,'External',?)",(json.dumps({'sizes':[{'chrtID':20}]}),))
        upsert_financial([financial(quantity=2,chrt=20)],source_identity=identity)
        with connect() as conn:snapshot=conn.serialize()
    shops.store_snapshot(shop,0,snapshot,synced=True)
    preview=setup.mapping_preview(shop,100,20,state['item']['id'],'owner','owner')
    setup.mapping_confirm(shop,100,20,state['item']['id'],preview['fingerprint'],'owner','owner',key())
    return shop

def test_two_shop_same_nm_projection_atomic_snapshot_and_no_duplicate_cost(state,monkeypatch):
    primary_doc,_=sale(0,1,10)
    upsert_financial([financial()],source_identity=shops.account_identity(token('primary')))
    bind(0,1,primary_doc)
    shop=secondary(state,monkeypatch)
    secondary_doc,_=sale(shop,2,20)
    original=shops.read(shop);preview=finance.preview(shop,1,secondary_doc,False,None,'owner','owner')
    with patch.object(shops,'store_snapshot_in_transaction',side_effect=RuntimeError('Injected commit failure')):
        with pytest.raises(RuntimeError):finance.confirm(shop,1,secondary_doc,False,None,preview['fingerprint'],'owner','owner',key())
    assert shops.read(shop)['database_snapshot']==original['database_snapshot']
    with connect() as conn:assert conn.execute('SELECT COUNT(*) FROM warehouse_financial_bindings WHERE shop_id=?',(shop,)).fetchone()[0]==0
    applied,_=bind(shop,1,secondary_doc)
    assert applied['cost_rub']==.67 and applied['applied_revision']==original['revision']+1
    from calculations import build_dashboard
    primary=build_dashboard(date(2026,9,11),date(2026,9,11)).financial
    file=state['tmp_path']/'projected-child.sqlite3';file.write_bytes(shops.read(shop)['database_snapshot'])
    with monkeypatch.context() as child:
        child.setattr(core,'DB_PATH',file)
        child.setattr(config,'get_token',lambda:token('secondary'))
        projected=build_dashboard(date(2026,9,11),date(2026,9,11)).financial
        assert projected['cost']==.67 and projected['profit']==199.33
        with connect() as conn:assert conn.execute('SELECT COUNT(*) FROM sales_fifo_events').fetchone()[0]==0
    assert primary['cost']+projected['cost']==1.01 and primary['profit']+projected['profit']==298.99
    from shop_workspace_service import prepare
    prepared=prepare(shop,'cost','2026-09-11','2026-09-11',actor='owner',role='owner',cost={'nm_id':100,'cost_per_wb_unit':999})
    assert prepared['response']['financial']['cost']==.67 and prepared['response']['financial']['cogs_quality']=='exact'
    assert prepared['response']['sources_complete']
    refreshed=state['tmp_path']/'refreshed-child.sqlite3';refreshed.write_bytes(prepared['snapshot'])
    with monkeypatch.context() as child:
        child.setattr(core,'DB_PATH',refreshed)
        child.setattr(config,'get_token',lambda:token('secondary'))
        upsert_financial([{**financial(quantity=2,chrt=20),'penalty':1}],source_identity=shops.account_identity(token('secondary')))
        assert read_financial_fifo_reconciliation('2026-09-11','2026-09-11').iloc[0]['reason']=='shared_source_changed'
    with connect() as conn:conn.execute('UPDATE shop_workspaces SET sync_quality_json=? WHERE id=?',(json.dumps(['ads_error','shared_fifo_pending']),shop))
    refreshed=prepare(shop,'report','2026-09-11','2026-09-11',actor='owner',role='owner')
    assert refreshed['response']['sync_errors']==['ads_error'] and not refreshed['response']['sources_complete']


def test_canonical_matched_return_signed_cost_and_no_second_physical_fifo(state):
    document,_=sale(0,3,10);identity=shops.account_identity(token('primary'))
    upsert_financial([financial(quantity=3)],source_identity=identity);bind(0,1,document)
    returned_key=key()
    warehouse.return_sale(document,1,'2026-09-12','return-doc',returned_key,4,'owner','owner')
    raw={**financial(2),'rr_dt':'2026-09-12','sale_dt':'2026-09-12','doc_type_name':'Возврат','retail_amount':-100,'ppvz_for_pay':-100}
    upsert_financial([raw],source_identity=identity);bind(0,2,returned_key)
    rows=read_financial_fifo_reconciliation('2026-09-11','2026-09-12')
    assert list(rows['eligible_fifo_cogs_rub'])==[1.01,-.34]
    from calculations import build_dashboard
    result=build_dashboard(date(2026,9,11),date(2026,9,12)).financial
    assert result['cost']==.67
    with connect() as conn:assert conn.execute('SELECT COUNT(*) FROM sales_fifo_events').fetchone()[0]==0


def test_verified_period_quality_removes_only_resolved_fifo_marker():
    financial=dict(financial_period_complete=True,cogs_complete=True,cogs_quality='exact',exact_unit_coverage_percent=100,exact_units=1)
    assert finance.verified_period_errors(['ads_error','shared_fifo_pending'],financial)==['ads_error']
    for changed in ({'financial_period_complete':False},{'cogs_complete':False},{'shared_fifo_manual_source_rows':1},{'exact_units':0},{'exact_unit_coverage_percent':50}):
        assert finance.verified_period_errors(['shared_fifo_pending'],{**financial,**changed})==['shared_fifo_pending']

def test_variant_quantity_date_and_foreign_seller_proof_reject(state):
    document,_=sale(0,1,10);identity=shops.account_identity(token('primary'))
    for row in (financial(quantity=2),{**financial(),'sale_dt':'2026-09-12'},financial(chrt=20)):
        upsert_financial([row],source_identity=identity)
        with pytest.raises((warehouse.WarehouseConflict,ValueError)):finance.preview(0,1,document,False,None,'owner','owner')
    with connect() as conn:conn.execute('UPDATE products_catalog SET raw_json=? WHERE nm_id=100',(json.dumps({'sizes':[{'chrtID':10},{'chrtID':20}]}),))
    row=financial();row.pop('chrt_id');upsert_financial([row],source_identity=identity)
    with pytest.raises(warehouse.WarehouseConflict):finance.preview(0,1,document,False,None,'owner','owner')
