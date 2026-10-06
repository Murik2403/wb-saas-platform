import sys
from pathlib import Path
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]));sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'tenant-app'))
import config
import db.core as core
import ozon_ads_api as api

PERIOD={'start_date':'2026-09-01','end_date':'2026-09-02','campaign_ids':['101']}
@pytest.fixture
def env(monkeypatch,tmp_path):
    monkeypatch.setattr(config,'DATA_DIR',tmp_path);monkeypatch.setattr(config,'DATA_ENCRYPTION_KEY',Fernet.generate_key().decode());monkeypatch.setattr(core,'DB_PATH',tmp_path/'db.sqlite')
    app=FastAPI();app.include_router(api.router)
    with TestClient(app) as client:yield client
def test_reads_are_local_unknown_and_never_remote(env,monkeypatch):
    monkeypatch.setattr(api.ads,'PerformanceClient',lambda *args:pytest.fail('local GET must not call upstream'))
    state=env.get('/api/v1/ozon-accounting/advertising/credentials').json()
    assert state=={'configured':False,'credential_type':'ozon-performance','seller_credentials_reused':False}
    result=env.get('/api/v1/ozon-accounting/advertising?start_date=2026-09-01&end_date=2026-09-02')
    assert result.status_code==200 and result.json()['totals']['spend'] is None
    assert env.get('/api/v1/ozon-accounting/advertising/export.csv?start_date=2026-09-01&end_date=2026-09-02').status_code==200
@pytest.mark.parametrize('role',['manager','viewer','warehouse','production',''])
def test_credentials_owner_only_and_never_echo_secrets(env,role):
    path='/api/v1/ozon-accounting/advertising/credentials';body={'client_id':'performance-account','client_secret':'private-value'}
    assert env.put(path,json=body,headers={'x-tenant-role':role}).status_code==403
    assert env.delete(path,headers={'x-tenant-role':role}).status_code==403
    assert not api.ads.credential_path().exists()
def test_owner_store_disconnect_no_exposed_credentials(env):
    path='/api/v1/ozon-accounting/advertising/credentials';body={'client_id':'performance-account','client_secret':'private-value'}
    response=env.put(path,json=body,headers={'x-tenant-role':'owner'});assert response.status_code==200
    assert 'private-value' not in response.text and 'performance-account' not in env.get(path).text
    assert env.get(path).json()['configured'] is True
    assert env.delete(path,headers={'x-tenant-role':'owner'}).json()=={'configured':False}
@pytest.mark.parametrize('role',['viewer','warehouse','production',''])
def test_remote_report_generation_and_poll_require_write_role(env,role,monkeypatch):
    monkeypatch.setattr(api.ads,'PerformanceClient',lambda *args:pytest.fail('denial must precede upstream'))
    base='/api/v1/ozon-accounting/advertising/reports'
    assert env.post(base,json=PERIOD,headers={'x-tenant-role':role}).status_code==403
    assert env.post(base+'/missing/poll',headers={'x-tenant-role':role}).status_code==403
def test_not_configured_invalid_scope_and_dates_never_remote(env,monkeypatch):
    monkeypatch.setattr(api.ads,'PerformanceClient',lambda *args:pytest.fail('no remote before validation'))
    base='/api/v1/ozon-accounting/advertising/reports';headers={'x-tenant-role':'owner'}
    assert env.post(base,json=PERIOD,headers=headers).status_code==409
    for changes in ({'campaign_ids':[]},{'campaign_ids':['invalid']},{'start_date':'2026-01-01'},{'unknown_field':'value'}):
        assert env.post(base,json={**PERIOD,**changes},headers=headers).status_code==422
def test_async_job_status_safe_and_endpoint_limited(env,monkeypatch):
    api.ads.save_credentials('id','secret')
    calls=[]
    class Client:
        def __init__(self,*args):pass
        def submit(self,*args):calls.append('submit');return '12345678-1234-4234-9234-123456789012'
        def status(self,*args):calls.append('status');return {'state':'IN_PROGRESS'}
        def report(self,*args):pytest.fail('must not download pending report')
    monkeypatch.setattr(api.ads,'PerformanceClient',Client)
    base='/api/v1/ozon-accounting/advertising/reports';headers={'x-tenant-role':'manager'}
    response=env.post(base,json=PERIOD,headers=headers);assert response.status_code==200
    identifier=response.json()['id'];assert response.json()['status']=='pending'
    status=env.get(base+'/'+identifier);assert status.status_code==200 and 'remote_uuid' not in status.json() and 'credential_fingerprint' not in status.json()
    assert env.post(base+'/'+identifier+'/poll',headers=headers).json()['status']=='pending'
    assert calls==['submit','status']
    assert env.get(base+'/other-tenant-id').status_code==404
def test_lock_rejects_overlapping_remote_work(env,monkeypatch):
    api.ads.save_credentials('id','secret');api.operation_lock.acquire()
    try:
        monkeypatch.setattr(api.ads,'PerformanceClient',lambda *args:pytest.fail('busy denial must precede upstream'))
        assert env.post('/api/v1/ozon-accounting/advertising/reports',json=PERIOD,headers={'x-tenant-role':'owner'}).status_code==409
    finally:api.operation_lock.release()
