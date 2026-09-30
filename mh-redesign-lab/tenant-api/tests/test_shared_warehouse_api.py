import json
import uuid
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import pytest
import importlib.util
from pathlib import Path
_fixture_spec=importlib.util.spec_from_file_location("shared_warehouse_api_fixtures",Path(__file__).with_name("test_shop_workspaces_api.py"))
_fixture_module=importlib.util.module_from_spec(_fixture_spec)
_fixture_spec.loader.exec_module(_fixture_module)
client,api,token=_fixture_module.client,_fixture_module.api,_fixture_module.token
import config
from db.core import connect
from db import shared_warehouse as warehouse,shop_workspaces as shops
from db.fifo_finished_goods import _create_finished_goods_layer
from db.production import _apply_pipeline_delta

@pytest.fixture
def warehouse_client(client,monkeypatch):
    monkeypatch.setattr(config,'TENANT_SLUG','trusted-tenant')
    import shared_warehouse_setup as setup
    setup.initialize()
    with connect() as conn:
        conn.execute("INSERT INTO products_catalog(nm_id,product_name,raw_json) VALUES(100,'Canonical',?)",(json.dumps({'sizes':[{'chrtID':10}]}),))
        _create_finished_goods_layer(conn,100,'canonical','Canonical','production','trusted-production','2026-09-01',3,.335,'ready',chrt_id=10)
        _apply_pipeline_delta(conn,100,'canonical',3,0)
    warehouse.register_shop(0,'primary-seller')
    return client

def post(client,path,body,shop=0,key=None,role='owner'):
    return client.post('/api/v1/shared-warehouse'+path,json=body,headers={
      'x-mh-shop-id':str(shop),'x-mh-request-key':key or str(uuid.uuid4()),'x-tenant-role':role})

def mapping(client,shop,nm,chrt,item):
    body={'nm_id':nm,'chrt_id':chrt,'item_id':item}
    preview=post(client,f'/{shop}/mappings/preview',body,shop)
    assert preview.status_code==200,preview.text
    confirmed=post(client,f'/{shop}/mappings/confirm',{**body,'fingerprint':preview.json()['fingerprint']},shop)
    assert confirmed.status_code==200,confirmed.text

def test_public_primary_lifecycle_preserves_money_replay_and_atomic_damaged_return(warehouse_client):
    client=warehouse_client
    created=post(client,'/items',{'nm_id':100,'chrt_id':10,'name':'Canonical'})
    assert created.status_code==200,created.text
    mapping(client,0,100,10,created.json()['id'])
    body={'nm_id':100,'chrt_id':10,'quantity':3,'document_date':'2026-09-10'}
    preview=post(client,'/0/reserve/preview',body)
    assert preview.status_code==200,preview.text
    reserved=post(client,'/0/reserve/confirm',{**body,'source_ref':'order-1','fingerprint':preview.json()['fingerprint']})
    assert reserved.status_code==200,reserved.text
    identifier=reserved.json()['id']
    for revision,kind in ((1,'commit'),(2,'receipt')):
        moved=post(client,f'/0/reservations/{identifier}/transition',{'kind':kind,'revision':revision,'source_ref':kind+'-1','document_date':'2026-09-10'})
        assert moved.status_code==200,moved.text
    sale_preview=post(client,f'/0/reservations/{identifier}/sale/preview',{'quantity':3,'document_date':'2026-09-11'})
    assert sale_preview.status_code==200,sale_preview.text
    sale_key=str(uuid.uuid4())
    sale_body={'quantity':3,'document_date':'2026-09-11','source_ref':'sale-1','fingerprint':sale_preview.json()['fingerprint']}
    sale=post(client,f'/0/reservations/{identifier}/sale/confirm',sale_body,key=sale_key)
    assert sale.status_code==200 and sale.json()['cost_rub']==1.01,sale.text
    assert post(client,f'/0/reservations/{identifier}/sale/confirm',sale_body,key=sale_key).json()==sale.json()
    return_body={'sale_request_key':sale_key,'quantity':1,'document_date':'2026-09-12','source_ref':'return-1','revision':4,'disposition':'quarantine','allocation_proof':'Inspected damaged returned unit'}
    with connect() as conn:before=conn.execute('SELECT COUNT(*) FROM warehouse_documents').fetchone()[0]
    returned_preview=post(client,'/0/returns/preview',return_body)
    assert returned_preview.status_code==200,returned_preview.text
    with connect() as conn:assert conn.execute('SELECT COUNT(*) FROM warehouse_documents').fetchone()[0]==before
    return_key=str(uuid.uuid4())
    returned=post(client,'/0/returns/confirm',return_body,key=return_key)
    assert returned.status_code==200 and returned.json()['cost_rub']==.34,returned.text
    assert post(client,'/0/returns/confirm',return_body,key=return_key).json()==returned.json()
    workspace=client.get('/api/v1/shared-warehouse/0/workspace')
    assert workspace.status_code==200,workspace.text
    assert workspace.json()['ownership'][0]['wb_units']==1 and workspace.json()['holds'][0]['units']==1
    assert post(client,f'/0/reservations/{identifier}/sale/preview',{'quantity':1,'document_date':'2026-09-13'}).json()['missing_units']==1

def test_public_shop_target_role_and_strict_dto_fail_closed(warehouse_client):
    client=warehouse_client
    assert post(client,'/items',{'nm_id':True,'chrt_id':10,'name':'Invalid'}).status_code==422
    assert post(client,'/items',{'nm_id':100,'chrt_id':10,'name':'Invalid','token':'untrusted'}).status_code==422
    assert post(client,'/items',{'nm_id':100,'chrt_id':10,'name':'Canonical'},role='manager').status_code==403
    assert client.get('/api/v1/shared-warehouse/1/workspace').status_code==409
    assert client.get('/api/v1/shared-warehouse/0/workspace',headers={'x-tenant-role':'invalid'}).status_code==403
    assert client.get('/api/v1/shared-warehouse/0/workspace',headers={'x-tenant-role':'viewer'}).status_code==200
    assert post(client,'/0/reserve/confirm',{'nm_id':100,'chrt_id':10,'quantity':1,'document_date':'2026-09-10','source_ref':'x','fingerprint':'0'*64},role='viewer').status_code==403

def test_primary_setup_changes_identity_only_after_verified_session(warehouse_client):
    # Separate account fixture already bound; changed seller must be rejected.
    with patch.object(config,'get_token',return_value=token('new-seller')),patch('wb_api.WBAPI.test_statistics',return_value=True):
        response=post(warehouse_client,'/primary-binding',{})
    assert response.status_code==409,response.text
    assert warehouse_client.get('/api/v1/shop-context/session').json()['shops'][0]['verified_identity']=='primary-seller'

def test_two_shop_public_reserve_cannot_overdraw_or_use_other_shop_document(warehouse_client):
    client=warehouse_client
    item=post(client,'/items',{'nm_id':100,'chrt_id':10,'name':'Canonical'}).json()
    mapping(client,0,100,10,item['id'])
    shop=shops.create('Secondary','unused','secondary-seller',register_warehouse=True)
    private=sqlite3.connect(':memory:')
    private.execute('CREATE TABLE products_catalog(nm_id INTEGER,product_name TEXT,supplier_article TEXT,raw_json TEXT)')
    private.execute('INSERT INTO products_catalog VALUES(200,?,?,?)',('External','external',json.dumps({'sizes':[{'chrtID':20}]})))
    private.commit();blob=private.serialize();private.close()
    shops.store_snapshot(shop,0,blob,synced=True)
    mapping(client,shop,200,20,item['id'])
    bodies={0:{'nm_id':100,'chrt_id':10,'quantity':2,'document_date':'2026-09-10'},shop:{'nm_id':200,'chrt_id':20,'quantity':2,'document_date':'2026-09-10'}}
    previews={sid:post(client,f'/{sid}/reserve/preview',body,sid).json() for sid,body in bodies.items()}
    with ThreadPoolExecutor(2) as pool:
        futures=[pool.submit(post,client,f'/{sid}/reserve/confirm',{**body,'source_ref':f'order-{sid}','fingerprint':previews[sid]['fingerprint']},sid) for sid,body in bodies.items()]
        responses=[future.result() for future in futures]
    assert sorted(response.status_code for response in responses)==[200,409]
    with connect() as conn:
        assert conn.execute("SELECT SUM(units) FROM warehouse_reservation_layers WHERE status='reserved'").fetchone()[0]==2
        successful=dict(conn.execute('SELECT * FROM warehouse_reservations').fetchone())
    wrong_shop=shop if successful['shop_id']==0 else 0
    denied=post(client,f'/{wrong_shop}/reservations/{successful["id"]}/transition',{'kind':'commit','revision':1,'source_ref':'wrong','document_date':'2026-09-10'},wrong_shop)
    assert denied.status_code==409,denied.text
    denied_manager=client.get(f'/api/v1/shared-warehouse/{shop}/workspace',headers={'x-mh-shop-id':str(shop),'x-tenant-role':'manager'})
    assert denied_manager.status_code==403,denied_manager.text
    with connect() as conn:assert conn.execute('SELECT COUNT(*) FROM warehouse_documents').fetchone()[0]==1

def test_setup_uuid_replay_precedes_changed_catalog_but_never_skips_acl(warehouse_client):
    client=warehouse_client
    item_key=str(uuid.uuid4())
    item_body={'nm_id':100,'chrt_id':10,'name':'Canonical'}
    item=post(client,'/items',item_body,key=item_key)
    assert item.status_code==200,item.text
    assert post(client,'/items',item_body,key=item_key).json()==item.json()
    assert post(client,'/items',{**item_body,'name':'changed'},key=item_key).status_code==409
    body={'nm_id':100,'chrt_id':10,'item_id':item.json()['id']}
    preview=post(client,'/0/mappings/preview',body).json()
    body.update(fingerprint=preview['fingerprint']);mapping_key=str(uuid.uuid4())
    first=post(client,'/0/mappings/confirm',body,key=mapping_key)
    assert first.status_code==200,first.text
    with connect() as conn:
        conn.execute("UPDATE products_catalog SET raw_json=? WHERE nm_id=100",(json.dumps({'sizes':[{'chrtID':20}]}),))
    assert post(client,'/0/mappings/confirm',body,key=mapping_key).json()==first.json()
    assert post(client,'/0/mappings/confirm',{**body,'chrt_id':20},key=mapping_key).status_code==409
    assert post(client,'/0/mappings/confirm',body,key=mapping_key,role='viewer').status_code==403
    with connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM warehouse_setup_receipts').fetchone()[0]==2
        for sql in ('DELETE FROM warehouse_setup_receipts','UPDATE warehouse_setup_receipts SET actor="other"','INSERT OR REPLACE INTO warehouse_setup_receipts SELECT * FROM warehouse_setup_receipts'):
            with pytest.raises(sqlite3.IntegrityError):conn.execute(sql)

def test_unbound_primary_is_visible_setup_state_and_verified_binding_refreshes_session(client,monkeypatch):
    monkeypatch.setattr(config,'TENANT_SLUG','trusted-tenant')
    initial=client.get('/api/v1/shared-warehouse/0/workspace')
    assert initial.status_code==200,initial.text
    assert initial.json()['needs_primary_binding'] and initial.json()['can_configure']
    assert 'x-mh-shop-identity' not in initial.headers
    with patch.object(config,'get_token',return_value=token('primary')),patch('wb_api.WBAPI.test_statistics',return_value=True):
        bound=post(client,'/primary-binding',{})
    assert bound.status_code==200 and bound.json()['refresh_session'],bound.text
    session=client.get('/api/v1/shop-context/session').json()
    assert session['shops'][0]['verified_identity']==bound.json()['verified_identity']
    assert bound.headers['x-mh-shop-identity']==bound.json()['verified_identity']
    assert client.get('/api/v1/shared-warehouse/0/workspace').json()['needs_primary_binding'] is False
