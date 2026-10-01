import json
from uuid import uuid4
from test_cashflow_api import client
from db import core
from db.migration_import import FIELDS


def test_real_middleware_template_primary_roles_and_attachment_confirmation(client):
    core.ensure_financial_schema()
    with core.connect() as c:
        c.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES(100,'A','Item',?)",(json.dumps({'sizes':[{'chrtID':10}]}),))
        c.execute("INSERT INTO financial_report(rrd_id,nm_id,quantity,operation_date,operation_name,retail_amount,ppvz_for_pay,raw_json) VALUES(11,100,3,'2026-08-02','Продажа',30,30,?)",(json.dumps({'srid':'WB11','chrt_id':10}),))
    url='/api/v1/migration-import/historical-attachments/template?start=2026-08-01&end=2026-08-31'
    for role in ('owner','manager','viewer','warehouse'):
        result=client.get(url,headers={'x-tenant-role':role})
        assert result.status_code==200
        assert 'no-store' in result.headers['cache-control'] and 'WB11' in result.text
    assert client.get(url,headers={'x-tenant-role':'integration_reader'}).status_code==403
    assert client.get(url,headers={'x-tenant-role':'invalid'}).status_code==403
    assert client.get(url,headers={'x-mh-shop-id':'1','x-tenant-role':'owner','x-tenant-account':'1'}).status_code==409
    lot=['L1',100,10,'2026-08-01','2026-09-01','ready',3,0,10,0,'synthetic lot documents']
    body={'kind':'finished_lots','csv_text':';'.join(FIELDS['finished_lots'])+'\n'+';'.join(map(str,lot)),'mapping':{f:f for f in FIELDS['finished_lots']}}
    h={'x-tenant-role':'owner','x-tenant-account':'1'}
    p=client.post('/api/v1/migration-import/preview',json=body,headers=h).json()
    assert client.post('/api/v1/migration-import/confirm',json={**body,'request_key':str(uuid4()),'preview_fingerprint':p['preview_fingerprint']},headers=h).status_code==200
    import csv,io
    row=next(csv.DictReader(io.StringIO(client.get(url,headers=h).text.lstrip('\ufeff')),delimiter=';'))
    row.update(source_id='A1',document_id='D1',sequence='1',lot_source_id='L1',allocation_units='3',original_sale_document='-',document_proof='synthetic sale documentary evidence')
    output=io.StringIO();w=csv.DictWriter(output,fieldnames=FIELDS['historical_attachments'],delimiter=';');w.writeheader();w.writerow(row)
    body={'kind':'historical_attachments','csv_text':output.getvalue(),'mapping':{f:f for f in FIELDS['historical_attachments']}}
    p=client.post('/api/v1/migration-import/preview',json=body,headers=h).json();assert p['can_confirm'],p
    body.update(request_key=str(uuid4()),preview_fingerprint=p['preview_fingerprint'])
    assert client.post('/api/v1/migration-import/confirm',json=body,headers={**h,'x-tenant-role':'viewer'}).status_code==403
    assert client.post('/api/v1/migration-import/confirm',json=body,headers={**h,'origin':'https://evil.test'}).status_code==403
    assert client.post('/api/v1/migration-import/confirm',json=body,headers={**h,'x-tenant-role':'manager'}).status_code==200
    assert client.post('/api/v1/migration-import/confirm',json=body,headers=h).json()['replayed']
    with core.connect() as c:assert c.execute('SELECT COUNT(*) FROM financial_report').fetchone()[0]==1
