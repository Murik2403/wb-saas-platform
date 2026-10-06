import json
import pytest
from db import core
from db.ozon_accounting import seller_fingerprint, import_snapshot
import ozon_cabinet as cabinet

START,END='2026-09-01','2026-09-30'

@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'DB_PATH',tmp_path/'cabinet.sqlite')
    core.init_db()
    import config
    monkeypatch.setattr(config,'get_ozon_credentials',lambda:('fake','synthetic-key'))

def saved(**kwargs):
    snapshot={'seller_fingerprint': seller_fingerprint('fake'),'start_date':START,'end_date':END,'products':[],'stocks':[],'fbo':[],'fbs':[],'returns':[],'types':[],'accruals':[]}
    snapshot.update(kwargs);import_snapshot(snapshot)

def posting(number='FBS1',**kwargs):
    return {'posting_number':number,'status':'awaiting_packaging','in_process_at':'2026-09-03T00:00:00Z',
            'products':[{'sku':123,'offer_id':'Synthetic','name':'Synthetic','quantity':2,'price':{'amount':'250.25','currency':'RUB'}}],**kwargs}

def test_missing_and_unverified_empty_are_unknown_not_zero():
    assert cabinet.orders(START,END)['metrics']['order_units'] is None
    saved()
    value=cabinet.orders(START,END)
    assert value['loaded'] and value['metrics']['order_units'] is None
    assert not value['coverage']['marketplace_complete']

def test_current_fbs_money_is_reference_not_revenue_and_status_unchanged():
    saved(fbs=[posting()])
    value=cabinet.orders(START,END)
    assert value['metrics']=={'posting_count':1,'order_units':2}
    row=value['items'][0]
    assert row['status']=='awaiting_packaging' and row['date_basis']=='in_process_at'
    assert row['price_rub'] is None and row['products'][0]['price_rub']=='250.25'

def test_duplicates_collapse_but_conflicts_and_cross_channel_are_unknown():
    saved(fbs=[posting(),posting()])
    assert cabinet.orders(START,END)['metrics']['order_units']==2
    saved(fbs=[posting(),posting(status='cancelled')])
    value=cabinet.orders(START,END)
    assert value['items']==[] and value['metrics']['order_units'] is None
    saved(fbs=[posting()],fbo=[posting(created_at='2026-09-03T00:00:00Z')])
    assert cabinet.orders(START,END)['metrics']['order_units'] is None

def test_quantity_bool_and_product_id_do_not_become_units_or_sku():
    saved(fbs=[posting(products=[{'product_id':123,'quantity':True}])])
    value=cabinet.orders(START,END)
    assert value['metrics']['order_units'] is None
    assert value['items'][0]['products'][0]['sku'] is None

def test_marked_current_fbs_missing_source_date_cannot_use_legacy_created_at():
    row=posting(created_at='2026-09-03T00:00:00Z');row.pop('in_process_at')
    saved(fbs=[row],source_endpoints={'fbs':'/v4/posting/fbs/list'})
    value=cabinet.orders(START,END)
    assert value['metrics']['order_units'] is None and not value['coverage']['record_parse_complete']

def test_current_stock_scope_literal_sku_no_warehouse_and_conflict():
    product={'product_id':987,'offer_id':'Synthetic','stocks':[{'sku':123,'type':'fbo','present':5,'reserved':2,'shipment_type':'general'}]}
    saved(stocks=[product])
    value=cabinet.stock('2026-08-01','2026-08-31')
    assert value['coverage']['scope']=='current_snapshot' and value['source_period']['start_date']==START
    assert value['items'][0]['sku']=='123' and value['items'][0]['warehouse_id'] is None
    assert value['metrics']['present_units']==5
    saved(stocks=[product,{**product,'stocks':[{**product['stocks'][0],'present':6}]}])
    assert cabinet.stock(START,END)['metrics']['present_units'] is None

def test_returns_registry_status_and_date_not_filtered_by_selected_period():
    saved(returns=[{'id':'1000015552','posting_number':'FBS1','product':{'sku':'123','quantity':1},
                    'visual':{'status':{'display_name':'Возвращён','sys_name':'returned'},'change_moment':'2026-10-01T00:00:00Z'},
                    'logistic':{'return_date':'2026-10-01T00:00:00Z'}}])
    value=cabinet.returns(START,END)
    assert len(value['items'])==1 and value['items'][0]['status']=='Возвращён'
    assert value['coverage']['scope']=='current_registry'

def test_newer_overlapping_capture_stales_orders_but_stock_uses_latest():
    saved(fbs=[posting()])
    with core.connect() as conn:
        row=conn.execute('SELECT payload_json FROM ozon_accounting_snapshots').fetchone()
        conn.execute('INSERT INTO ozon_accounting_snapshots VALUES(?,?,?,?,?)',('2026-09-02:2026-09-03','2026-09-02','2026-09-03','2999-01-01T00:00:00Z',row[0]))
    value=cabinet.orders(START,END)
    assert not value['loaded'] and value['coverage']['source_snapshot_stale']
    assert cabinet.stock(START,END)['synced_at']=='2999-01-01T00:00:00Z'

def test_bounded_snapshot_and_rows_fail_before_large_decode(monkeypatch):
    saved()
    monkeypatch.setattr(cabinet,'MAX_BYTES',1)
    with pytest.raises(ValueError):cabinet.orders(START,END)

def test_overview_does_not_turn_empty_accruals_into_profit_or_revenue():
    saved(fbs=[posting()])
    value=cabinet.overview(START,END)
    assert value['metrics']['order_units']==2
    assert value['metrics']['document_profit_rub'] is None and value['metrics']['document_revenue_rub'] is None

def test_seller_change_hides_runtime_sources_but_key_rotation_does_not(monkeypatch):
    import config
    from db.ozon_accounting import workspace
    saved(fbs=[posting()])
    assert cabinet.orders(START,END)['metrics']['order_units']==2
    monkeypatch.setattr(config,'get_ozon_credentials',lambda:('fake','rotated-key'))
    assert cabinet.orders(START,END)['metrics']['order_units']==2
    monkeypatch.setattr(config,'get_ozon_credentials',lambda:('other-seller','rotated-key'))
    for fn in (cabinet.orders,cabinet.stock,cabinet.returns,cabinet.control):
        value=fn(START,END)
        assert not value['loaded'] and not value['coverage']['source_identity_confirmed']
    assert not workspace(START,END)['loaded']

def test_legacy_unbound_snapshot_is_not_assigned_to_current_seller():
    saved(seller_fingerprint=None,fbs=[posting()])
    assert not cabinet.orders(START,END)['loaded']

def test_route_contract_and_invalid_period_or_kind():
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'mh-redesign-lab'/'tenant-api'))
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from ozon_cabinet_api import router
    app=FastAPI();app.include_router(router)
    client=TestClient(app)
    for path in ('overview','orders','returns','stock','control'):
        assert client.get('/api/v1/ozon-cabinet/'+path,params={'start_date':START,'end_date':END}).status_code==200
    assert client.get('/api/v1/ozon-cabinet/orders',params={'start_date':END,'end_date':START}).status_code==422
    assert client.get('/api/v1/ozon-cabinet/orders',params={'start_date':START,'end_date':END,'kind':'guess'}).status_code==422
