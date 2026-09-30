from __future__ import annotations
from contextlib import closing
import importlib.util,sys,base64,json
from pathlib import Path
from unittest.mock import patch
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
API_DIR=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(API_DIR.parents[1]/'tenant-app'));sys.path.insert(0,str(API_DIR))
spec=importlib.util.spec_from_file_location('shops_test_api',API_DIR/'api_server.py')
api=importlib.util.module_from_spec(spec);sys.modules[spec.name]=api;spec.loader.exec_module(api)
import shop_workspaces_api as routes
from db import shop_workspaces as shops

@pytest.fixture
def client(monkeypatch,tmp_path):
    import config,db.core as core
    monkeypatch.setattr(core,'DB_PATH',tmp_path/'main.sqlite3')
    monkeypatch.setattr(api,'DB_PATH',tmp_path/'main.sqlite3')
    monkeypatch.setattr(config,'DATA_DIR',tmp_path)
    monkeypatch.setattr(config,'DATA_ENCRYPTION_KEY',Fernet.generate_key().decode())
    monkeypatch.setattr(config,'DATA_ENCRYPTION_KEYRING','')
    monkeypatch.setattr(api,'IS_DEMO',False)
    core.init_db()
    with closing(TestClient(api.app,base_url='http://127.0.0.1',headers={'x-tenant-role':'owner','x-tenant-account':'owner@test'})) as c:yield c

def token(sid):return 'header.'+base64.urlsafe_b64encode(json.dumps({'sid':sid}).encode()).decode().rstrip('=')+'.signature'

def test_owner_registration_no_secret_and_roles(client):
    with patch.object(routes.WBAPI,'test_statistics',return_value=True),patch.object(routes,'get_token',return_value=None):
        response=client.post('/api/v1/shop-workspaces',json={'name':'shop','token':token('one')})
        assert response.status_code==200
        for role in ['manager','viewer','technologist']:
            assert client.post('/api/v1/shop-workspaces',json={'name':'shop','token':token('two')},headers={'x-tenant-role':role}).status_code==403
        assert client.post('/api/v1/shop-workspaces',json={'name':'duplicate','token':token('one')}).status_code==400
    listing=client.get('/api/v1/shop-workspaces').json()
    assert 'credential' not in str(listing) and token('one') not in str(listing)
    assert len(listing['items'])==1
    assert client.get('/api/v1/shop-workspaces',headers={'x-tenant-role':'viewer'}).json()['can_write'] is False

def test_consolidated_route_not_shadowed_and_partial_sources(client):
    shop=shops.create('second','token','identity')
    with patch.object(routes,'execute',return_value={'financial':{'sales':5,'profit':3,'financial_period_complete':True,'cogs_complete':True},'sources_complete':False}):
        with patch('calculations.build_dashboard') as main:
            main.return_value.financial={'sales':10,'profit':6,'financial_period_complete':True,'cogs_complete':True}
            r=client.get('/api/v1/shop-workspaces/consolidated/report?start=2026-09-01&end=2026-09-02')
            assert r.status_code==200
            assert r.json()['total']['sales']==10  # Unsynced secondary excluded.
            assert r.json()['complete'] is False
    for path in [f'/{shop}/sync',f'/{shop}/deactivate',f'/{shop}/connection']:
        assert client.post('/api/v1/shop-workspaces'+path,json={'days':30,'token':'token'},headers={'x-tenant-role':'viewer'}).status_code==403
    assert client.get('/api/v1/shop-workspaces/consolidated/report?start=2026-09-03&end=2026-09-02').status_code==400

def test_reconnect_retains_snapshot_rejects_other_identity(client):
    shop=shops.create('second','old',shops.account_identity(token('one')))
    with patch.object(routes.WBAPI,'test_statistics',return_value=True):
        assert client.post(f'/api/v1/shop-workspaces/{shop}/deactivate').status_code==200
        assert client.post(f'/api/v1/shop-workspaces/{shop}/connection',json={'token':token('two')}).status_code==400
        assert client.post(f'/api/v1/shop-workspaces/{shop}/connection',json={'token':token('one')}).status_code==200
    assert shops.worker_input(shop)['token']==token('one')

def test_primary_partial_sync_keeps_consolidated_preliminary(client):
    with patch('calculations.build_dashboard') as main,patch('db.core.last_sync') as sync:
        main.return_value.financial={'sales':10,'financial_period_complete':True,'cogs_complete':True}
        for status in [None,{'status':'partial'},{'status':'error'}]:
            sync.return_value=status
            result=client.get('/api/v1/shop-workspaces/consolidated/report?start=2026-09-01&end=2026-09-02').json()
            assert result['complete'] is False
            assert result['shops'][0]['sources_complete'] is False
        sync.return_value={'status':'success'}
        assert client.get('/api/v1/shop-workspaces/consolidated/report?start=2026-09-01&end=2026-09-02').json()['complete'] is True

def test_shop_session_acl_unknown_selector_and_no_primary_fallback(client):
    from db import shared_warehouse as warehouse
    shop=shops.create('second','unused','verified')
    owner=client.get('/api/v1/shop-context/session').json()
    assert [r['id'] for r in owner['shops']]==[0,shop]
    assert owner['shops'][0]['snapshot_revision'] is None
    assert owner['shops'][1]['snapshot_revision']==0
    assert owner['shops'][1]['snapshot_available'] is False
    assert 'credential' not in str(owner) and 'unused' not in str(owner)
    headers={'x-tenant-role':'manager','x-tenant-account':'member@test','x-mh-shop-id':str(shop)}
    assert client.get('/api/v1/settings/product-costs',headers=headers).status_code==403
    assert client.get('/api/v1/shop-workspaces',headers={'x-tenant-role':'manager','x-tenant-account':'member@test'}).json()['items']==[]
    warehouse.grant(shop,'member@test',False,'owner')
    granted=client.get('/api/v1/shop-context/session',headers=headers)
    assert granted.status_code==200 and granted.json()['shops'][1]['can_write'] is False
    assert client.get('/api/v1/unknown-route',headers=headers).status_code==409
    assert client.get('/api/v1/settings/product-costs',headers={'x-mh-shop-id':'garbage'}).status_code==400
    assert client.get('/api/v1/settings/product-costs',headers={'x-mh-shop-id':'999'}).status_code==409
    assert client.post('/api/v1/products/prices/upload',json={},headers=headers).status_code==409
    assert client.patch('/api/v1/settings/product-costs',json={'items':[]},headers=headers).status_code==403

def test_two_real_selected_shop_routes_costs_atomic_replay_and_history_isolation(client,monkeypatch,tmp_path):
    import sqlite3,uuid,db.core as core
    main=core.DB_PATH;ids=[]
    for number,cost in enumerate((10,30)):
        identifier=shops.create(str(number),'unused',str(number));ids.append(identifier)
        path=tmp_path/f'child-{number}.sqlite'
        monkeypatch.setattr(core,'DB_PATH',path);core.init_db()
        with core.connect() as conn:
            conn.execute("INSERT INTO products_catalog(nm_id,product_name,raw_json) VALUES(123,'Same article','{}')")
            conn.execute('INSERT INTO costs(nm_id,cost_per_wb_unit) VALUES(123,?)',(cost,))
        monkeypatch.setattr(core,'DB_PATH',main)
        shops.store_snapshot(identifier,0,path.read_bytes())
    selected={'x-mh-shop-id':str(ids[0])}
    first=client.get('/api/v1/settings/product-costs',headers=selected)
    assert first.status_code==200 and first.json()['items'][0]['cost_per_wb_unit']==10
    assert first.headers['x-mh-shop-id']==str(ids[0])
    assert first.headers['x-mh-shop-revision']=='1'
    assert client.get('/api/v1/settings/product-costs',headers={'x-mh-shop-id':str(ids[1])}).json()['items'][0]['cost_per_wb_unit']==30
    headers=dict(selected,**{'x-mh-shop-revision':'1','x-mh-request-key':str(uuid.uuid4())})
    payload={'items':[{'nm_id':123,'supplier_article':'same','cost_per_wb_unit':44,'note':'selected'}],'reason':'manual cost confirmation'}
    response=client.patch('/api/v1/settings/product-costs',headers=headers,json=payload)
    assert response.status_code==200,response.text
    assert response.headers['x-mh-shop-revision']=='2'
    assert client.patch('/api/v1/settings/product-costs',headers=headers,json=payload).status_code==200
    assert shops.read(ids[0])['revision']==2
    assert client.get('/api/v1/settings/product-costs',headers=selected).json()['items'][0]['cost_per_wb_unit']==44
    assert client.get('/api/v1/settings/product-costs',headers={'x-mh-shop-id':str(ids[1])}).json()['items'][0]['cost_per_wb_unit']==30
    with core.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM costs WHERE nm_id=123').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM shop_request_receipts').fetchone()[0]==1
        conn.execute("INSERT INTO settings_change_history(kind,nm_id,before_json,after_json,changed_at,source,actor,reason) VALUES('cost',123,'{}','{}','2026-09-30','manual','owner@test','PRIMARY COST')")
        conn.execute("INSERT INTO settings_change_history(kind,nm_id,before_json,after_json,changed_at,source,actor,reason) VALUES('production_norm',123,'{}','{}','2026-09-30','manual','owner@test','GLOBAL NORM')")
    history=client.get('/api/v1/settings/change-history?kind=cost&nm_id=123',headers=selected)
    assert history.status_code==200 and history.headers['x-mh-resource-scope']=='shop_local'
    assert 'manual cost confirmation' in history.text and 'PRIMARY COST' not in history.text
    norms=client.get('/api/v1/settings/change-history?kind=production_norm&nm_id=123',headers=selected)
    assert norms.status_code==200 and norms.headers['x-mh-resource-scope']=='tenant_shared'
    assert 'GLOBAL NORM' in norms.text and 'manual cost confirmation' not in norms.text
    for query in ('','?kind=cost&kind=production_norm','?kind=other'):
        assert client.get('/api/v1/settings/change-history'+query,headers=selected).status_code==409
    report_headers=dict(selected,**{'x-mh-shop-revision':'2','x-mh-request-key':str(uuid.uuid4())})
    payload={'name':'Selected shop report','days':2,'metrics':['stocks']}
    created=client.post('/api/v1/operations/reports/quick',headers=report_headers,json=payload)
    assert created.status_code==200,created.text
    assert client.post('/api/v1/operations/reports/quick',headers=report_headers,json=payload).json()==created.json()
    run=created.json()['run_id']
    download=client.get(f'/api/v1/operations/reports/runs/{run}/download',headers=selected)
    assert download.status_code==200 and download.content.startswith(b'%PDF-'),download.text[:500]
    assert download.headers['x-mh-shop-revision']=='3'
    assert 'report-' in download.headers['content-disposition']
    assert client.get(f'/api/v1/operations/reports/runs/{run}/download',headers={'x-mh-shop-id':str(ids[1])}).status_code==404

def test_selected_request_stream_rejects_before_buffering_remaining_chunks():
    import asyncio
    from shop_dispatch import bounded_body
    import shop_context
    class Input:
        exhausted=False
        async def stream(self):
            yield b'a'*4
            yield b'b'*5
            self.exhausted=True
            yield b'never'
    value=Input()
    with pytest.raises(shop_context.ShopContextError) as exc:asyncio.run(bounded_body(value,8))
    assert exc.value.code=='SHOP_REQUEST_TOO_LARGE' and not value.exhausted

def test_registry_physical_resources_central_fbs_selected_and_external_ozon_denied(client):
    import shop_context
    for path in ('/api/v1/production-workspace/wip/issue','/api/v1/production-workspace/workcenters/confirm'):
        assert shop_context.route_scope('POST',path)['scope']=='tenant_shared'
    assert shop_context.route_scope('GET','/api/v1/operations/supply/fbs/abc/barcode')['scope']=='shop_local'
    assert shop_context.route_scope('GET','/api/v1/ozon-accounting/pnl')['scope']=='forbidden'

def test_report_and_consolidation_recheck_target_acl_after_private_worker(client):
    from db import shared_warehouse as warehouse,core
    shop=shops.create('second','unused','verified');warehouse.register_shop(shop,'verified')
    warehouse.grant(shop,'member@test',False,'owner')
    headers={'x-tenant-role':'manager','x-tenant-account':'member@test'}
    def revoke_then_report(*args,**kwargs):
        with core.connect() as conn:conn.execute('DELETE FROM warehouse_shop_acl WHERE shop_id=? AND account=?',(shop,'member@test'))
        return {'financial':{'sales':999},'sources_complete':True}
    with patch.object(routes,'execute',side_effect=revoke_then_report):
        result=client.get(f'/api/v1/shop-workspaces/{shop}/report?start=2026-09-01&end=2026-09-02',headers=headers)
        assert result.status_code==403 and '999' not in result.text
    warehouse.grant(shop,'member@test',False,'owner')
    with patch.object(routes,'execute',side_effect=revoke_then_report),patch('calculations.build_dashboard') as main:
        main.return_value.financial={'sales':10}
        result=client.get('/api/v1/shop-workspaces/consolidated/report?start=2026-09-01&end=2026-09-02',headers=headers)
        assert result.status_code==200
        assert [row['id'] for row in result.json()['shops']]==[0]
        assert '999' not in result.text
