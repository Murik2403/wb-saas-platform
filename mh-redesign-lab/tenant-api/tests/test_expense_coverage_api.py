from datetime import date
import uuid
from types import SimpleNamespace
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from test_management_economics import api


@pytest.fixture
def client(monkeypatch, tmp_path):
    import db.core as core
    monkeypatch.setattr(core, 'DB_PATH', tmp_path/'isolated.sqlite3')
    core.init_db()
    core.ensure_financial_schema()
    monkeypatch.setattr(api, 'IS_DEMO', False)
    monkeypatch.setattr(api, '_build_dashboard_cached', lambda *_: SimpleNamespace(financial_products=pd.DataFrame([{'Артикул WB':123, 'Выручка':100, 'Продано нетто':2, 'Прибыль':20}])))
    return TestClient(api.app, base_url="http://127.0.0.1",headers={"x-tenant-role":"owner","x-tenant-account":"1"})


URL='/api/v1/settings/expense-coverage'
QUERY='?nm_id=123&start=2026-09-01&end=2026-09-30'


def payload(client):
    item = next(row for row in client.get(URL+QUERY).json()['items'] if row['article']=='taxes')
    return dict(nm_id=123, start='2026-09-01', end='2026-09-30', article='taxes', state='confirmed_zero', source_ref='Ledger #4', reason='Checked zero', request_key=str(uuid.uuid4()), expected_fingerprint=item['fingerprint'])


def test_roles_origin_and_demo_guard(client, monkeypatch):
    data=payload(client)
    for role in ['viewer', 'warehouse', 'production']:
        assert client.post(URL,json=data,headers={'x-tenant-role':role}).status_code == 403
    assert client.post(URL,json=data,headers={'origin':'https://evil.example'}).status_code==403
    assert not client.get(URL+QUERY,headers={'x-tenant-role':'viewer'}).json()['can_write']
    monkeypatch.setattr(api,'IS_DEMO',True)
    assert client.post(URL,json=data).status_code==403


def test_owner_actor_replay_and_no_money_change(client):
    before=api.get_management_economics(123,date(2026,9,1),date(2026,9,30))
    data=payload(client)
    response=client.post(URL,json=data,headers={'x-tenant-role':'manager','x-tenant-account':'account-42'})
    assert response.status_code==200, response.text
    assert client.post(URL,json=data,headers={'x-tenant-role':'manager','x-tenant-account':'account-42'}).json()['id']==response.json()['id']
    current=client.get(URL+QUERY).json()
    assert current['history'][0]['actor']=='account-42'
    assert current['count']==1
    after=api.get_management_economics(123,date(2026,9,1),date(2026,9,30))
    for key in ['accounting_profit_rub','management_profit_rub','manual_expenses_rub']: assert after[key]==before[key]
    assert len(current['history'])==1


def test_old_preview_cannot_confirm_new_report(client, monkeypatch):
    data=payload(client)
    monkeypatch.setattr(api,'_build_dashboard_cached', lambda *_: SimpleNamespace(financial_products=pd.DataFrame([{'Артикул WB':123,'Выручка':101,'Продано нетто':2,'Прибыль':21}])))
    assert client.post(URL,json=data).status_code==422
    assert client.get(URL+QUERY).json()['count']==0


def test_invalid_scope_and_control_text_rejected(client):
    data=payload(client)
    for extra in [dict(end='2026-08-31'),dict(article='bank'),dict(state='auto'),dict(reason='\n'),dict(expected_fingerprint='short')]:
        assert client.post(URL,json={**data,**extra}).status_code==422


@pytest.mark.parametrize('field,article',[('commission','wb_commission'),('delivery_rub','logistics_returns'),('storage_fee','storage'),('acceptance','acceptance')])
def test_actual_wb_article_blocks_zero(client, field, article):
    from db.core import connect
    with connect() as conn:
        conn.execute(f"INSERT INTO financial_report(rrd_id,nm_id,operation_date,{field},raw_json) VALUES(1,123,'2026-09-02',25,'{{}}')")
    item=next(row for row in client.get(URL+QUERY).json()['items'] if row['article']==article)
    data={**payload(client),'article':article,'expected_fingerprint':item['fingerprint']}
    assert client.post(URL,json=data).status_code==422
    data['state']='confirmed'
    assert client.post(URL,json=data).status_code==200


def test_legacy_commission_inclusion_does_not_break_attestation(client):
    from db.core import connect
    with connect() as conn:
        conn.execute("INSERT INTO manual_expenses(nm_id,article,amount_rub,allocation_basis,source,period_start,period_end,created_at,updated_at,include_in_management_result) VALUES(123,'wb_commission',50,'total','manual','2026-09-01','2026-09-30','','',1)")
    item=next(row for row in client.get(URL+QUERY).json()['items'] if row['article']=='wb_commission')
    data={**payload(client),'article':'wb_commission','state':'unknown','expected_fingerprint':item['fingerprint']}
    response=client.post(URL,json=data)
    assert response.status_code==200,response.text
