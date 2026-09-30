import base64,json,uuid
from test_cashflow_api import client,api
import config,pytest
from db import core,shared_warehouse as wh,shop_workspaces as shops
from db.migration_import import FIELDS,preview_import,confirm_import

def token(sid):return 'e30.'+base64.urlsafe_b64encode(json.dumps({'sid':sid}).encode()).decode().rstrip('=')+'.fixture'
@pytest.fixture
def imported(client,monkeypatch):
 monkeypatch.setattr(config,'TENANT_SLUG','trusted-tenant');monkeypatch.setattr(config,'get_token',lambda:token('primary'))
 import shared_warehouse_setup as setup
 setup.initialize();wh.register_shop(0,shops.account_identity(token('primary')))
 with core.connect() as c:
  c.execute('BEGIN IMMEDIATE');setup.access(c,0,'1','owner');c.execute("INSERT INTO products_catalog(nm_id,product_name,raw_json) VALUES(100,'Imported',?)",(json.dumps({'sizes':[{'chrtID':10}]}),))
 values=['lot1',100,10,'2026-08-01','2026-09-01','ready',3,3,'0.335','1.01','Original source manually checked']
 text=';'.join(FIELDS['finished_lots'])+'\n'+';'.join(map(str,values));mapping={f:f for f in FIELDS['finished_lots']};p=preview_import('finished_lots',text,mapping);assert p['can_confirm'],p
 confirm_import('finished_lots',text,mapping,'opening',p['preview_fingerprint'],'1')
 return client

def headers(**extra):return {'x-tenant-account':'1','x-tenant-role':'owner','x-mh-shop-id':'0','x-mh-request-key':str(uuid.uuid4()),**extra}
base='/api/v1/shared-warehouse/imported-basis/'
def test_actual_preview_confirm_replay_origin_role_seller_and_selector(imported,monkeypatch):
 body={'nm_id':100,'reason':'Checked original authority'};h=headers()
 r=imported.post(base+'preview',json=body,headers=h);assert r.status_code==200,r.text
 assert r.json()['total_cents']==101 and r.json()['source_quality']=='manual_preliminary'
 with core.connect() as c:assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='warehouse_imported_transfers'").fetchone()
 confirm={**body,'fingerprint':r.json()['fingerprint']}
 assert imported.post(base+'confirm',json=confirm,headers={**h,'origin':'https://evil.test'}).status_code==403
 assert imported.post(base+'confirm',json=confirm,headers={**h,'x-mh-shop-id':'999'}).status_code in (403,409)
 r=imported.post(base+'confirm',json=confirm,headers=h);assert r.status_code==200,r.text
 assert imported.post(base+'confirm',json=confirm,headers=h).json()==r.json()
 assert imported.post(base+'confirm',json={**confirm,'reason':'Another authority'},headers=h).status_code==409
 for role in ('viewer','manager','warehouse'):assert imported.post(base+'confirm',json=confirm,headers={**h,'x-tenant-role':role}).status_code==403
 monkeypatch.setattr(config,'get_token',lambda:token('other'))
 assert imported.post(base+'confirm',json=confirm,headers=h).status_code==409

def test_actual_demo_denies_and_current_routes_registry(imported,monkeypatch):
 from shop_context import ROUTES
 from production_permissions import write_allowed
 for route in ('preview','confirm'):
  assert {'method':'POST','path':base+route,'scope':'tenant_shared'} in ROUTES
  assert not write_allowed('manager','POST',base+route)
 monkeypatch.setattr(api,'IS_DEMO',True)
 assert imported.post(base+'preview',json={'nm_id':100,'reason':'Original check'},headers=headers()).status_code==403
 assert imported.post(base+'confirm',json={'nm_id':100,'reason':'Original check','fingerprint':'a'*64},headers=headers()).status_code==403
