from test_cashflow_api import client
import pytest
from uuid import uuid4
from db.migration_import import FIELDS

def payload():return {'kind':'materials','csv_text':'source_id;material_name;unit\nm1;Ткань;м','mapping':{f:f for f in FIELDS['materials']}}
@pytest.mark.parametrize('role',['viewer','warehouse','technologist','cutter','seamstress'])
def test_read_preview_but_not_confirm(client,role):
 h={'x-tenant-role':role};p=client.post('/api/v1/migration-import/preview',json=payload(),headers=h)
 assert p.status_code==200,p.text
 body={**payload(),'request_key':str(uuid4()),'preview_fingerprint':p.json()['preview_fingerprint']}
 assert client.post('/api/v1/migration-import/confirm',json=body,headers=h).status_code==403
 assert client.get('/api/v1/migration-import/history',headers=h).json()['items']==[]
@pytest.mark.parametrize('role',['owner','manager'])
def test_confirm_and_replay(client,role):
 h={'x-tenant-role':role};p=client.post('/api/v1/migration-import/preview',json=payload(),headers=h).json()
 body={**payload(),'request_key':str(uuid4()),'preview_fingerprint':p['preview_fingerprint']}
 r=client.post('/api/v1/migration-import/confirm',json=body,headers=h)
 assert r.status_code==200,r.text
 assert r.json()['imported_count']==1
 assert client.post('/api/v1/migration-import/confirm',json=body,headers=h).json()['replayed']
 assert len(client.get('/api/v1/migration-import/history').json()['items'])==1

def test_origin_and_invalid_payload(client):
 assert client.post('/api/v1/migration-import/preview',json=payload(),headers={'origin':'https://evil.test'}).status_code==403
 assert client.post('/api/v1/migration-import/preview',json={**payload(),'kind':'unknown'}).status_code==422
 assert client.post('/api/v1/migration-import/confirm',json={**payload(),'request_key':'bad','preview_fingerprint':'bad'}).status_code==422

def finished_payload():
 from db import core
 import json
 with core.connect() as c:c.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES(100,'A','Item',?)",(json.dumps({'sizes':[{'chrtID':10}]}),))
 row=['lot1',100,10,'2026-08-01','2026-09-01','ready',3,3,'0.335','1.01','Manual source document checked']
 return {'kind':'finished_lots','csv_text':';'.join(FIELDS['finished_lots'])+'\n'+';'.join(map(str,row)),'mapping':{f:f for f in FIELDS['finished_lots']}}

def test_finished_actual_api_replay_role_and_origin(client):
 data=finished_payload();h={'x-tenant-account':'1','x-tenant-role':'owner','x-mh-shop-id':'0'}
 response=client.post('/api/v1/migration-import/preview',json=data,headers=h);assert response.status_code==200,response.text
 assert response.json()['manual_source'] and not response.json()['physical_replay']
 body={**data,'request_key':str(uuid4()),'preview_fingerprint':response.json()['preview_fingerprint']}
 assert client.post('/api/v1/migration-import/confirm',json=body,headers={**h,'origin':'https://evil.test'}).status_code==403
 assert client.post('/api/v1/migration-import/confirm',json=body,headers=h).json()['imported_count']==1
 assert client.post('/api/v1/migration-import/confirm',json=body,headers=h).json()['replayed']
 assert client.post('/api/v1/migration-import/confirm',json=body,headers={**h,'x-tenant-role':'viewer'}).status_code==403
 changed={**body,'csv_text':body['csv_text'].replace('Manual source','Different source')}
 assert client.post('/api/v1/migration-import/confirm',json=changed,headers=h).status_code==422

def test_finished_stale_preview_catalog_and_secondary(client):
 from db import core
 data=finished_payload();response=client.post('/api/v1/migration-import/preview',json=data).json()
 with core.connect() as c:c.execute("UPDATE products_catalog SET product_name='Changed' WHERE nm_id=100")
 body={**data,'request_key':str(uuid4()),'preview_fingerprint':response['preview_fingerprint']}
 assert client.post('/api/v1/migration-import/confirm',json=body).status_code==422
 assert client.post('/api/v1/migration-import/preview',json=data,headers={'x-mh-shop-id':'999'}).status_code in (403,409)

@pytest.mark.parametrize('kind',['finished_lots','historical_documents'])
def test_new_kinds_demo_denied(client,monkeypatch,kind):
 from test_cashflow_api import api
 data=finished_payload();data['kind']=kind
 monkeypatch.setattr(api,'IS_DEMO',True)
 assert client.post('/api/v1/migration-import/preview',json=data).status_code in (200,422)
 assert client.post('/api/v1/migration-import/confirm',json={**data,'request_key':str(uuid4()),'preview_fingerprint':'0'*64}).status_code==403

def test_historical_actual_confirm_replay_current_role(client):
 data=finished_payload();data['csv_text']=data['csv_text'].replace(';ready;3;3;0.335;1.01;',';ready;3;0;0.335;0;')
 h={'x-tenant-account':'1','x-tenant-role':'owner'}
 p=client.post('/api/v1/migration-import/preview',json=data,headers=h).json()
 assert client.post('/api/v1/migration-import/confirm',json={**data,'request_key':str(uuid4()),'preview_fingerprint':p['preview_fingerprint']},headers=h).status_code==200
 values=['allocation1','sale1',101,100,10,'2026-08-02',1,'sale',3,'lot1',3,'-',30,25,5,0,0,0,0,0,'Original sale verified manually']
 history={'kind':'historical_documents','csv_text':';'.join(FIELDS['historical_documents'])+'\n'+';'.join(map(str,values)),'mapping':{f:f for f in FIELDS['historical_documents']}}
 p=client.post('/api/v1/migration-import/preview',json=history,headers=h).json();assert p['can_confirm'],p
 body={**history,'request_key':str(uuid4()),'preview_fingerprint':p['preview_fingerprint']}
 assert client.post('/api/v1/migration-import/confirm',json=body,headers=h).json()['imported_count']==1
 assert client.post('/api/v1/migration-import/confirm',json=body,headers=h).json()['replayed']
 assert client.post('/api/v1/migration-import/confirm',json=body,headers={**h,'x-tenant-role':'viewer'}).status_code==403
