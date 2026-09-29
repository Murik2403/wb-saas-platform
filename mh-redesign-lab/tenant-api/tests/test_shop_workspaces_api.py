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
    with closing(TestClient(api.app,base_url='http://127.0.0.1')) as c:yield c

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
