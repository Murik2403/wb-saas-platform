import sys
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]));sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'tenant-app'))
import ozon_prices_api as api
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api.prices.config,'IS_DEMO',False,raising=False)
    app=FastAPI();app.include_router(api.router)
    with TestClient(app) as c:yield c
@pytest.mark.parametrize('role',['viewer','production','warehouse',''])
@pytest.mark.parametrize('path',['refresh','preview','confirm'])
def test_read_roles_no_writes(client,monkeypatch,role,path):
    monkeypatch.setattr(api.prices,'client',lambda _:pytest.fail('must not upstream'))
    body={'items':[{'product_id':'1','price':'120.00'}],'source_fingerprint':'fp'}
    if path=='confirm':body.update(request_key='unique-request-key-1',confirmed=True)
    assert client.post('/api/v1/ozon-prices/'+path,json=body,headers={'x-tenant-role':role}).status_code==403

def test_demo_no_mutation(client,monkeypatch):
    monkeypatch.setattr(api.prices.config,'IS_DEMO',True)
    assert client.post('/api/v1/ozon-prices/refresh',headers={'x-tenant-role':'owner'}).status_code==403

def test_owner_confirm_and_manager_preview(client,monkeypatch):
    calls=[];monkeypatch.setattr(api.prices,'confirm',lambda *a:calls.append(a) or {'status':'completed'})
    body={'items':[{'product_id':'1','price':'120.00'}],'source_fingerprint':'fp','request_key':'unique-request-key-1','confirmed':True}
    assert client.post('/api/v1/ozon-prices/confirm',json=body,headers={'x-tenant-role':'owner'}).json()['status']=='completed';assert len(calls)==1
    assert client.post('/api/v1/ozon-prices/confirm',json={**body,'confirmed':'true'},headers={'x-tenant-role':'owner'}).status_code==422
