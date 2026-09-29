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
