import importlib.util,sys,time
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT.parents[1]/'tenant-app'))
import gateway_request_auth as proof
spec=importlib.util.spec_from_file_location('gateway_proof_test_api',ROOT/'api_server.py');api=importlib.util.module_from_spec(spec);sys.modules[spec.name]=api;spec.loader.exec_module(api)
@pytest.fixture
def client(monkeypatch):
 monkeypatch.setattr(api,'IS_DEMO',False);monkeypatch.setattr(api,'TENANT_SLUG','test-seller');monkeypatch.setattr(api,'INTERNAL_API_SECRET','tenant-scoped-secret')
 return TestClient(api.app,base_url='http://127.0.0.1')
def auth(method,path,role='owner',account='1',issued=None):return {'x-tenant-role':role,'x-tenant-account':account,'x-mh-gateway-auth':proof.sign('tenant-scoped-secret',method,path,'test-seller',role,account,issued)}
def test_unsigned_forged_owner_denied_even_reads(client):
 assert client.get('/api/v1/session',headers={'x-tenant-role':'owner','x-tenant-account':'1'}).status_code==403
 assert client.post('/api/v1/settings/ozon',json={'client_id':'1','api_key':'secret-test-key'}).status_code==403
 assert client.get('/healthz').status_code==200

def test_signed_identity_expiry_method_path_account_tampering(client):
 path='/api/v1/session';headers=auth('GET',path)
 assert client.get(path,headers=headers).json()['user']['role']=='owner'
 assert client.get(path+'?other=1',headers=headers).status_code==403
 assert client.get(path,headers={**headers,'x-tenant-account':'2'}).status_code==403
 assert client.get(path,headers={**headers,'x-tenant-role':'manager'}).status_code==403
 assert client.get(path,headers=auth('GET',path,issued=time.time()-61)).status_code==403
 assert client.get(path,headers=auth('POST',path)).status_code==403

def test_signed_owner_can_write_and_manager_cannot_telegram(client,monkeypatch):
 path='/api/v1/settings/ozon';calls=[];monkeypatch.setattr(api,'save_ozon_credentials',lambda *v:calls.append(v))
 assert client.post(path,json={'client_id':'1','api_key':'secret-test-key'},headers=auth('POST',path)).status_code==200
 assert len(calls)==1
 link='/api/v1/operations/reports/telegram/link-code';monkeypatch.setattr(api.delivery,'request_telegram_link_code',lambda actor:pytest.fail('manager must not issue owner code'))
 assert client.post(link,headers=auth('POST',link,'manager')).status_code==403

def test_demo_overrides_owner_header_and_anonymous_reads(client,monkeypatch):
 monkeypatch.setattr(api,'IS_DEMO',True)
 result=client.get('/api/v1/session',headers={'x-tenant-role':'owner','x-tenant-account':'1'}).json()
 assert result['user']['role']=='viewer' and result['user']['id']=='demo'
 assert client.get('/api/v1/session').status_code==200
 assert client.post('/api/v1/settings/ozon',json={'client_id':'1','api_key':'secret-test-key'},headers={'x-tenant-role':'owner'}).status_code==403

def test_local_missing_role_not_owner(client,monkeypatch):
 monkeypatch.setattr(api,'TENANT_SLUG','')
 assert client.get('/api/v1/session').json()['user']['role']=='viewer'
 assert client.post('/api/v1/settings/ozon',json={'client_id':'1','api_key':'secret-test-key'}).status_code==403

def test_purpose_context_bound_cross_tenant_and_future_signature():
 token=proof.sign('secret','GET','/api/v1/session','a','owner','1')
 assert not proof.verify('secret',token,'GET','/api/v1/session','b','owner','1')
 assert not proof.verify('secret',proof.sign('secret','GET','/','a','owner','1',time.time()+1),'GET','/','a','owner','1')


@pytest.mark.parametrize('demo,role',[(False,'viewer'),(True,'owner')])
def test_readonly_price_advisor_works_for_viewer_and_demo(client,monkeypatch,demo,role):
 monkeypatch.setattr(api,'TENANT_SLUG','');monkeypatch.setattr(api,'IS_DEMO',demo)
 monkeypatch.setattr(api,'_price_advice',lambda *args:{'items':[],'read_only':True})
 r=client.post('/api/v1/dashboard/products/price-advice',json={'prices':[{'nm_id':1,'price':1000,'discount':0}]},headers={'x-tenant-role':role,'x-tenant-account':'1'})
 assert r.status_code==200 and r.json()['read_only'] is True

def test_auth_ingress_before_waiting_and_rejects_encoded_path(client):
 from gateway_request_auth import GatewayIdentityGuard
 from dashboard_work_guard import DashboardWorkGuard
 assert api.app.user_middleware[0].cls is GatewayIdentityGuard
 assert api.app.user_middleware[1].cls is DashboardWorkGuard
 assert client.get('/api/v1/session%3Fextra',headers=auth('GET','/api/v1/session%3Fextra')).status_code==403

def test_manager_connections_backup_delete_denied_products_and_production_retained():
 from production_permissions import write_allowed
 for method,path in [('POST','/api/v1/settings/wb-token'),('POST','/api/v1/settings/ozon'),('POST','/api/v1/operations/reports/telegram/link-code'),('GET','/api/v1/operations/backups'),('DELETE','/api/v1/settings/manual-expenses/1'),('POST','/api/v1/self-buyouts/delete')]:
  assert not write_allowed('manager',method,path) and write_allowed('owner',method,path)
 for method,path in [('POST','/api/v1/products/prices/upload'),('POST','/api/v1/manufacturing-costs/releases')]:assert write_allowed('manager',method,path)

def test_valid_proof_does_not_expire_while_waiting_behind_inner_guard(client,monkeypatch):
 import asyncio
 from starlette.requests import Request
 from starlette.responses import JSONResponse
 issued=int(time.time());path='/api/v1/session';headers=auth('GET',path,issued=issued)
 clock=[issued];monkeypatch.setattr(proof.time,'time',lambda:clock[0])
 result=[]
 async def downstream(scope,receive,send):
  assert scope[proof.VERIFIED_SCOPE_KEY] is proof.VERIFIED_TOKEN
  clock[0]=issued+120 # represents time spent queued after valid ingress
  async def route(request):return JSONResponse({'authorized':True})
  response=await api.invalidate_after_write(Request(scope),route)
  await response(scope,receive,send)
 async def receive():return {'type':'http.request','body':b'','more_body':False}
 async def send(message):result.append(message)
 scope={'type':'http','method':'GET','path':path,'raw_path':path.encode(),'query_string':b'','scheme':'http','server':('localhost',80),'headers':[(k.encode(),v.encode()) for k,v in headers.items()]}
 guard=proof.GatewayIdentityGuard(downstream,lambda:(False,'test-seller','tenant-scoped-secret'))
 asyncio.run(guard(scope,receive,send))
 assert result[0]['status']==200
 assert not proof.verify('tenant-scoped-secret',headers['x-mh-gateway-auth'],'GET',path,'test-seller','owner','1')

@pytest.mark.parametrize('demo,role',[(False,'viewer'),(True,'owner')])
def test_readonly_tax_input_passes_role_gate_for_viewer_and_demo(client,monkeypatch,demo,role):
 # The Ozon cabinet-grant layer (marketplace_context_api) is separate; here only the write-role gate is under test.
 monkeypatch.setattr(api,'TENANT_SLUG','');monkeypatch.setattr(api,'IS_DEMO',demo)
 r=client.post('/api/v1/ozon-insights/tax-input',json={'start_date':'2026-01-01','end_date':'2026-01-31'},headers={'x-tenant-role':role,'x-tenant-account':'1'})
 blocked=('наблюдателя','Демо доступно','производственной роли')
 assert not any(text in r.text for text in blocked)

def test_tax_rates_put_owner_only_manager_denied(client):
 from production_permissions import write_allowed
 assert write_allowed('owner','PUT','/api/v1/settings/tax') and not write_allowed('manager','PUT','/api/v1/settings/tax')
 assert write_allowed('manager','PUT','/api/v1/settings/tax-reconciliation')
 path='/api/v1/settings/tax'
 assert client.put(path,json={'year':2026,'regime':'usn_income','rates':[]},headers=auth('PUT',path,'manager')).status_code==403

def test_readonly_mutating_posts_stay_blocked_for_viewer(client,monkeypatch):
 monkeypatch.setattr(api,'TENANT_SLUG','')
 assert client.post('/api/v1/ozon-prices/confirm',json={},headers={'x-tenant-role':'viewer','x-tenant-account':'1'}).status_code==403
