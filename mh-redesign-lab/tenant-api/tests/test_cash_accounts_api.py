from test_cashflow_api import client
import pytest

def account(client,key='create-account-1',currency='RUB'):
    response=client.post('/api/v1/cash-accounts',json=dict(request_key=key,name='Bank',currency=currency))
    assert response.status_code==200,response.text
    return response.json()['id']

def csv():return 'external_id;date;direction;amount;currency;exchange_rate;category;reference;plan_id\nbank-1;2026-09-12;inflow;100;RUB;1;other;;'

def test_account_retry_preview_confirm_and_report(client):
    a=account(client);assert account(client)==a
    assert client.post('/api/v1/cash-accounts',json=dict(request_key='create-account-1',name='Other',currency='RUB')).status_code==422
    payload=dict(account_id=a,csv=csv())
    preview=client.post('/api/v1/cash-accounts/statements/preview',json=payload)
    assert preview.status_code==200,preview.text
    assert client.get('/api/v1/cash-accounts?start=2026-09-01&end=2026-09-20').json()['accounts'][1]['items']==[]
    body=payload|dict(request_key='confirm-statement-1',fingerprint=preview.json()['fingerprint'],confirmed=True)
    confirmed=client.post('/api/v1/cash-accounts/statements/confirm',json=body,headers={'x-tenant-account':'owner@test'})
    assert confirmed.status_code==200,confirmed.text
    assert client.post('/api/v1/cash-accounts/statements/confirm',json=body).json()==confirmed.json()
    assert client.get('/api/v1/cash-accounts?start=2026-09-01&end=2026-09-20').json()['accounts'][1]['actual_closing_minor']==10000

@pytest.mark.parametrize('role',['viewer','warehouse','technologist','cutter','seamstress'])
def test_reader_preview_allowed_financial_writes_forbidden(client,role):
    a=account(client);headers={'x-tenant-role':role}
    assert client.post('/api/v1/cash-accounts/statements/preview',json=dict(account_id=a,csv=csv()),headers=headers).status_code==200
    assert client.post('/api/v1/cash-accounts',json=dict(request_key='other-account-1',name='Bank',currency='RUB'),headers=headers).status_code==403
    assert client.patch(f'/api/v1/cash-accounts/{a}',json=dict(revision=1,active=False),headers=headers).status_code==403

def test_security_integration_reader_and_origin(client):
    a=account(client);data=dict(account_id=a,csv=csv())
    assert client.post('/api/v1/cash-accounts/statements/preview',json=data,headers={'x-tenant-role':'integration_reader'}).status_code==403
    assert client.get('/api/v1/cash-accounts?start=2026-09-01&end=2026-09-20',headers={'x-tenant-role':'integration_reader'}).status_code==403
    assert client.post('/api/v1/cash-accounts/statements/preview',json=data,headers={'origin':'https://evil.test'}).status_code==403
    assert client.post('/api/v1/cash-accounts/statements/confirm',json=data|dict(request_key='confirm-1',fingerprint='a'*64,confirmed=False)).status_code==422

def test_routes_transfer_not_consumed_by_dynamic_account_path(client):
    a,b=account(client),account(client,'create-account-2')
    body=dict(request_key='transfer-route-1',from_account_id=a,to_account_id=b,from_amount='100',to_amount='100',from_rate='1',to_rate='1',status='actual',planned_date='2026-09-10',actual_date='2026-09-10')
    response=client.post('/api/v1/cash-accounts/transfers',json=body)
    assert response.status_code==200,response.text
    assert client.patch('/api/v1/cashflow/entries/'+str(response.json()['from_entry_id']),json={'status':'cancelled'}).status_code==200
