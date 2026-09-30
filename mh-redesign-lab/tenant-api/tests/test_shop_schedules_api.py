import importlib.util,sys,uuid
from pathlib import Path
from contextlib import closing
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest
API_DIR=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(API_DIR.parents[1]/'tenant-app'));sys.path.insert(0,str(API_DIR))
spec=importlib.util.spec_from_file_location('schedules_test_api',API_DIR/'api_server.py')
api=importlib.util.module_from_spec(spec);sys.modules[spec.name]=api;spec.loader.exec_module(api)
from db import core,shop_workspaces as shops,shared_warehouse as warehouse
import config

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'DB_PATH',tmp_path/'main.sqlite')
    monkeypatch.setattr(config,'DATA_DIR',tmp_path)
    monkeypatch.setattr(config,'TENANT_SLUG','fixture')
    monkeypatch.setattr(config,'DATA_ENCRYPTION_KEY',Fernet.generate_key().decode())
    monkeypatch.setattr(config,'DATA_ENCRYPTION_KEYRING','')
    monkeypatch.setattr(api,'IS_DEMO',False)
    core.init_db()
    shop=shops.create('Selected','secret','identity',register_warehouse=True)
    warehouse.grant(shop,'1',True,'owner')
    with closing(TestClient(api.app,base_url='http://127.0.0.1',headers={'x-tenant-role':'owner','x-tenant-account':'1','x-mh-shop-id':str(shop)})) as value:
        yield value,shop

def body(**changes):
    return dict(enabled=True,expected_generation=0,interval_minutes=30,period_days=30,metrics=[],**changes)

def test_selected_context_defaults_explicit_owner_config_generation_uuid_and_no_secret(client):
    c,shop=client;url=f'/api/v1/shop-schedules/{shop}'
    response=c.get(url);assert response.status_code==200,response.text
    value=response.json();assert value['can_configure'] and value['delivery']=='local_only'
    assert all(not s['enabled'] and not s['exists'] for s in value['schedules'])
    key=str(uuid.uuid4());headers={'x-mh-request-key':key}
    first=c.put(url+'/sync',json=body(),headers=headers)
    assert first.status_code==200,first.text
    assert first.headers['x-mh-resource-scope']=='tenant_shared'
    assert first.json()['enabled'] and first.json()['generation']==1
    assert c.put(url+'/sync',json=body(),headers=headers).json()==first.json()
    assert c.put(url+'/sync',json={**body(),'period_days':7},headers=headers).status_code==409
    assert c.put(url+'/sync',json=body(),headers={'x-mh-request-key':str(uuid.uuid4())}).status_code==409
    assert 'secret' not in c.get(url).text and 'credential' not in c.get(url).text

def test_roles_acl_revocation_unknown_shop_and_canonical_gateway_actor(client):
    c,shop=client;url=f'/api/v1/shop-schedules/{shop}'
    key={'x-mh-request-key':str(uuid.uuid4())}
    for role in ('manager','viewer','warehouse','technologist'):
        assert c.put(url+'/sync',json=body(),headers={**key,'x-tenant-role':role}).status_code==403
    assert c.get(url,headers={'x-tenant-account':'2','x-tenant-role':'manager'}).status_code==403
    assert c.get(url,headers={'x-tenant-account':'owner@example.test'}).status_code==403
    warehouse.grant(shop,'1',False,'owner')
    assert c.get(url).json()['can_configure'] and not c.get(url).json()['can_enable']
    assert c.put(url+'/sync',json=body(),headers=key).status_code==403
    assert c.put(url+'/sync',json={**body(),'enabled':False},headers=key).status_code==200
    assert c.get('/api/v1/shop-schedules/999').status_code in (403,409)

def test_input_strict_no_auto_delivery_or_user_actor(client):
    c,shop=client;url=f'/api/v1/shop-schedules/{shop}/sync'
    for changes in ({'enabled':1},{'expected_generation':True},{'actor':'2'},{'email_enabled':True},{'metrics':['unknown']},{'interval_minutes':1}):
        assert c.put(url,json={**body(),**changes},headers={'x-mh-request-key':str(uuid.uuid4())}).status_code in (409,422)

def test_actual_routes_classified_shared_and_current_session_actions(client):
    import shop_context
    c,shop=client
    paths=api.app.openapi()['paths']
    matching={path:methods for path,methods in paths.items() if path.startswith('/api/v1/shop-schedules')}
    assert len(matching)==2
    for path,methods in matching.items():
        for method in methods:
            assert shop_context.route_scope(method.upper(),path)['scope']=='tenant_shared'
    response=c.get('/api/v1/shop-context/session');assert response.status_code==200,response.text
    assert any(row['path']=='/api/v1/shop-schedules/{shop_id}/{kind}' for row in response.json()['route_scopes'])

def test_origin_demo_foreign_selected_context_and_viewer_replay_fail_closed(client,monkeypatch):
    c,shop=client;url=f'/api/v1/shop-schedules/{shop}/sync'
    key=str(uuid.uuid4());headers={'x-mh-request-key':key}
    assert c.put(url,json=body(),headers={**headers,'origin':'https://foreign.example.test'}).status_code==403
    assert c.put(url,json=body(),headers=headers).status_code==200
    assert c.put(url,json=body(),headers={**headers,'x-tenant-role':'viewer'}).status_code==403
    assert c.put(url,json=body(),headers={**headers,'x-mh-shop-id':'0'}).status_code==409
    second=shops.create('Other','other-secret','other-identity',register_warehouse=True)
    assert c.get(f'/api/v1/shop-schedules/{second}').status_code==409
    assert c.put(url,json=body(),headers={**headers,'x-mh-shop-id':str(second)}).status_code==409
    monkeypatch.setattr(api,'IS_DEMO',True)
    assert c.put(url,json=body(),headers=headers).status_code==403
