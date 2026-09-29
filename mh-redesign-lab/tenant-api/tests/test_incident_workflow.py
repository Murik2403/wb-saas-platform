import sys,base64
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
API=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(API));sys.path.insert(0,str(API.parents[1]/'tenant-app'))
import db.core as core
from db.wb_incidents import create_wb_incident_case
from db.fifo_finished_goods import _create_finished_goods_layer
from incident_workflow_api import router
@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'DB_PATH',tmp_path/'incidents.sqlite3');core.init_db()
    case=create_wb_incident_case('Test','2026-09-01')
    with core.connect() as conn:layer=_create_finished_goods_layer(conn,10,'a','p','opening','seed','2026-01-01',10,20,'wb')
    app=FastAPI();app.include_router(router);return TestClient(app),case,layer
@pytest.mark.parametrize('role',['viewer','warehouse','cutter','seamstress','technologist','integration_reader',''])
def test_writes_require_owner_or_manager(env,role):
    client,case,layer=env
    for path,body,method in [('/details',{'assignee':'a'},'put'),('/evidence',{'filename':'x.txt','media_type':'text/plain','content_base64':'eA=='},'post'),('/holds',{'layer_id':layer,'location':'wb','units':1,'request_key':'request-1','confirmed':True},'post')]:
        assert getattr(client,method)(f'/api/v1/incident-workflow/{case}'+path,json=body,headers={'x-tenant-role':role}).status_code==403

def test_attachment_is_scoped_download_only_and_owner_history(env):
    client,case,_=env
    response=client.post(f'/api/v1/incident-workflow/{case}/evidence',json={'filename':'акт.txt','media_type':'text/plain','content_base64':base64.b64encode(b'proof').decode()},headers={'x-tenant-role':'owner','x-tenant-account':'42'})
    assert response.status_code==200
    identifier=response.json()['id'];download=client.get(f'/api/v1/incident-workflow/{case}/evidence/{identifier}')
    assert download.content==b'proof';assert download.headers['content-type']=='application/octet-stream'
    assert download.headers['x-content-type-options']=='nosniff';assert 'attachment;' in download.headers['content-disposition']
    assert client.get(f'/api/v1/incident-workflow/{case+1}/evidence/{identifier}').status_code==422
    report=client.get(f'/api/v1/incident-workflow/{case}').json();assert report['history'][0]['actor']=='account:42 / owner'

def test_strict_units_confirmation_and_release(env):
    client,case,layer=env;headers={'x-tenant-role':'manager'}
    body={'layer_id':layer,'location':'wb','units':2,'request_key':'request-1'}
    assert client.post(f'/api/v1/incident-workflow/{case}/holds',json=body,headers=headers).status_code==422
    assert client.post(f'/api/v1/incident-workflow/{case}/holds',json={**body,'units':1.5,'confirmed':True},headers=headers).status_code==422
    response=client.post(f'/api/v1/incident-workflow/{case}/holds',json={**body,'confirmed':True},headers=headers)
    assert response.status_code==200;identifier=response.json()['id']
    assert client.post(f'/api/v1/incident-workflow/{case}/holds/{identifier}/release',json={'reason':'verified','confirmed':True},headers=headers).status_code==200
