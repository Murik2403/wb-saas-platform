import sys
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
API_DIR=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(API_DIR));sys.path.insert(0,str(API_DIR.parents[1]/'tenant-app'))
from db import core
from db.ozon_accounting import seller_fingerprint, import_snapshot
import ozon_insights_api as api

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'DB_PATH',tmp_path/'isolated.sqlite');core.init_db()
    import config
    monkeypatch.setattr(config,'get_ozon_credentials',lambda:('fake','synthetic-key'))
    app=FastAPI();app.include_router(api.router)
    with TestClient(app) as client:yield client


def seed():
    import_snapshot({'seller_fingerprint':seller_fingerprint('fake'),'start_date':'2026-09-01','end_date':'2026-09-30','products':[{'sku':123,'offer_id':'Synthetic'}],'stocks':[],'fbo':[],'fbs':[],'returns':[],'types':[],'accruals':[]})


def test_reads_unknown_without_upstream_or_mutating_wb(client):
    args={'start_date':'2026-09-01','end_date':'2026-09-30'}
    for path in ['products','heatmap','rnp','monthly']:
        response=client.get('/api/v1/ozon-insights/'+path,params=args)
        assert response.status_code==200 and response.json()['coverage']['marketplace_complete'] is False
    response=client.get('/api/v1/ozon-insights/monthly.csv',params=args)
    assert response.status_code==200 and response.headers['X-Content-Type-Options']=='nosniff'
    assert 'None' not in response.text and 'month' in response.text
    with core.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM sales').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM orders').fetchone()[0]==0


@pytest.mark.parametrize('role',['owner','manager','viewer','warehouse','integration_reader',''])
def test_plan_favorite_write_roles_and_local_crud(client,role):
    seed();headers={'x-tenant-role':role}
    body={'sku':'123','day':'2026-09-05','units':7}
    response=client.put('/api/v1/ozon-insights/rnp/plan',json=body,headers=headers)
    allowed=role in ['owner','manager']
    assert response.status_code==(200 if allowed else 403)
    assert client.put('/api/v1/ozon-insights/rnp/favorite',json={'sku':'123','favorite':True},headers=headers).status_code==(200 if allowed else 403)
    if allowed:
        r=client.get('/api/v1/ozon-insights/rnp?start_date=2026-09-01&end_date=2026-09-30').json()['items'][0]
        assert r['plan_units']==7 and r['favorite'] and r['fact_units'] is None
        assert client.put('/api/v1/ozon-insights/rnp/plan',json={**body,'units':None},headers=headers).status_code==200
        assert client.put('/api/v1/ozon-insights/rnp/favorite',json={'sku':'123','favorite':False},headers=headers).status_code==200
        r=client.get('/api/v1/ozon-insights/rnp?start_date=2026-09-01&end_date=2026-09-30').json()['items'][0]
        assert r['plan_units'] is None and not r['favorite']


def test_validation_and_tax_inputs_never_default_missing_to_zero(client):
    base='/api/v1/ozon-insights'
    assert client.get(base+'/products?start_date=2026-09-30&end_date=2026-09-01').status_code==422
    assert client.get(base+'/heatmap?start_date=2026-09-01&end_date=2026-09-30&metric=profit').status_code==422
    assert client.put(base+'/rnp/plan',json={'sku':'123','day':'2026-09-05','units':True},headers={'x-tenant-role':'owner'}).status_code==422
    payload={'start_date':'2026-09-01','end_date':'2026-09-30'}
    unknown=client.post(base+'/tax-input',json=payload).json()
    assert unknown['income_input_rub'] is None and unknown['estimate'] is None
    incomplete=client.post(base+'/tax-input',json={**payload,'options':{'month':'2026-09','regime':'ausn_income','received':'100.00'}}).json()
    assert incomplete['estimate']['status']=='incomplete' and incomplete['income_input_rub'] is None
    complete=client.post(base+'/tax-input',json={**payload,'options':{'month':'2026-09','regime':'ausn_income','rate':'8','received':'100.00','offset':'0','compensation':'0','other_income':'0','refunds':'0'}})
    assert complete.status_code==200 and complete.json()['estimate']['estimate']=='8.00'
    assert client.post(base+'/tax-input',json={**payload,'options':{'month':'2026-09','received':'NaN'}}).status_code==422
    assert client.post(base+'/tax-input',json={**payload,'options':{'month':'2026-09','received':True}}).status_code==422

@pytest.mark.parametrize('regime,expected',[('ausn_income','8.00'),('ausn_income_expense','3.00'),('usn_income','6.00'),('usn_income_expense','1.50'),('osno_ip','1.30'),('osno_company','2.50')])
def test_all_tax_regimes_use_explicit_inputs_only(client,regime,expected):
    options={'month':'2026-09','regime':regime,'received':'100','offset':'0','compensation':'0','other_income':'0','refunds':'0','expenses':'90','loss':'0','insurance':'0','other_ndfl_base':'0'}
    response=client.post('/api/v1/ozon-insights/tax-input',json={'start_date':'2026-09-01','end_date':'2026-09-30','options':options})
    assert response.status_code==200 and response.json()['estimate']['estimate']==expected
    assert response.json()['source']=='explicit_manual_tax_inputs' and response.json()['document_revenue_suggestion_rub'] is None

@pytest.mark.parametrize('mode',['general0','general10'])
def test_supported_vat_modes_are_not_rejected_by_bridge(client,mode):
    response=client.post('/api/v1/ozon-insights/tax-input',json={'start_date':'2026-09-01','end_date':'2026-09-30','options':{'month':'2026-09','regime':'osno_company','vat_mode':mode}})
    assert response.status_code==200 and response.json()['estimate']['status']=='incomplete'
