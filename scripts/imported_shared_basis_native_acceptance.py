"""Hermetic exact-image public API proof; synthetic seller, no WB attestation."""
import argparse,asyncio,base64,hashlib,json,os,sys,tempfile,uuid
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--app-dir',type=Path,default=Path('/app/tenant-app'));p.add_argument('--api-dir',type=Path);p.add_argument('--expected-build');p.add_argument('--output',type=Path,required=True);p.add_argument('--local-source',action='store_true');a=p.parse_args()
 if not a.local_source:
  assert os.name!='nt' and a.expected_build
  assert Path('/tmp/redesign-build-id').read_text().strip()==a.expected_build
 sys.path.insert(0,str(a.app_dir.resolve()));sys.path.insert(0,str((a.api_dir or a.app_dir).resolve()))
 with tempfile.TemporaryDirectory(prefix='mh-imported-shared-native-') as private:
  # Replace discovered environment before importing runtime modules.
  for name in tuple(os.environ):
   if name.startswith(('WB_SAAS_','SMTP_','OZON_')):os.environ.pop(name)
  from cryptography.fernet import Fernet
  os.environ.update(WB_SAAS_DATA_DIR=private,WB_SAAS_TENANT_SLUG='transfer-private',WB_SAAS_DATA_ENCRYPTION_KEY=Fernet.generate_key().decode(),WB_SAAS_IS_DEMO='0',WB_SAAS_INTERNAL_API_URL='',WB_SAAS_INTERNAL_API_SECRET='',PYTHON_KEYRING_BACKEND='keyring.backends.null.Keyring',MH_LOCAL_JOBS='0')
  import config,api_server,httpx,shared_warehouse_setup as setup
  from db import core,shared_warehouse as wh,shop_workspaces as shops
  from db.migration_import import FIELDS
  from db.fifo_finished_goods import read_inventory_valuation
  core.init_db();core.ensure_financial_schema();setup.initialize()
  synthetic='e30.'+base64.urlsafe_b64encode(json.dumps({'sid':'synthetic-private-only'}).encode()).decode().rstrip('=')+'.fixture'
  config.save_token(synthetic);assert config.get_token()==synthetic
  # Explicit fixture binding. No network verification or production token.
  wh.register_shop(0,shops.account_identity(synthetic))
  with core.connect() as c:
   c.execute('BEGIN IMMEDIATE');setup.access(c,0,'1','owner')
   c.execute("INSERT INTO products_catalog(nm_id,product_name,raw_json) VALUES(100,'Synthetic imported SKU',?)",(json.dumps({'sizes':[{'chrtID':10},{'chrtID':11}]}),))
  async def post(path,body,key=None,role='owner',extra=None):
   h={'x-tenant-account':'1','x-tenant-role':role,'x-mh-shop-id':'0','x-mh-request-key':key or str(uuid.uuid4()),**(extra or {})}
   async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_server.app),base_url='http://127.0.0.1') as client:return await client.post('/api/v1/'+path,json=body,headers=h)
  def call(path,body,key=None):
   r=asyncio.run(post(path,body,key));assert r.status_code==200,(path,r.status_code,r.text);return r.json()
  def migration(kind,rows):
   body={'kind':kind,'csv_text':';'.join(FIELDS[kind])+'\n'+'\n'.join(';'.join(map(str,row)) for row in rows),'mapping':{f:f for f in FIELDS[kind]}}
   preview=call('migration-import/preview',body);assert preview['can_confirm'],preview
   call('migration-import/confirm',{**body,'preview_fingerprint':preview['preview_fingerprint'],'request_key':str(uuid.uuid4())})
  migration('finished_lots',[[f'lot{chrt}',100,chrt,'2026-08-01','2026-09-01','ready',3,3,'0.335','1.01','Synthetic original checked'] for chrt in (10,11)])
  with core.connect() as c:original=[tuple(r) for r in c.execute('SELECT * FROM finished_goods_cost_layers ORDER BY id')]
  body={'nm_id':100,'reason':'Synthetic complete ready authority transfer'};preview=call('shared-warehouse/imported-basis/preview',body)
  assert preview['total_cents']==202 and preview['total_units']==6 and preview['source_quality']=='manual_preliminary'
  with core.connect() as c:assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='warehouse_imported_transfers'").fetchone()
  confirm={**body,'fingerprint':preview['fingerprint']};key=str(uuid.uuid4())
  assert asyncio.run(post('shared-warehouse/imported-basis/confirm',confirm,key,extra={'origin':'https://foreign.invalid'})).status_code==403
  result=call('shared-warehouse/imported-basis/confirm',confirm,key);assert call('shared-warehouse/imported-basis/confirm',confirm,key)==result
  assert asyncio.run(post('shared-warehouse/imported-basis/confirm',confirm,key,'viewer')).status_code==403
  with core.connect() as c:
   assert [tuple(r) for r in c.execute('SELECT * FROM finished_goods_cost_layers ORDER BY id')]==original
   for table in ('inventory_movements','financial_report','sales_fifo_events'):assert c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==0
  assert read_inventory_valuation().iloc[0]['ready_value_rub']==2.02
  item=next(x for x in result['items'] if x['chrt_id']==10)
  mapping={'nm_id':100,'chrt_id':10,'item_id':item['id']};proof=call('shared-warehouse/0/mappings/preview',mapping)
  call('shared-warehouse/0/mappings/confirm',{**mapping,'fingerprint':proof['fingerprint']})
  costs=[];reservations=[]
  for index in range(3):
   reserve={'nm_id':100,'chrt_id':10,'quantity':1,'document_date':'2026-09-02'};proof=call('shared-warehouse/0/reserve/preview',reserve)
   costs.append(proof['layers'][0]['cost_rub']);reservations.append(call('shared-warehouse/0/reserve/confirm',{**reserve,'source_ref':f'reserve{index}','fingerprint':proof['fingerprint']}))
  assert costs==[.34,.34,.33],costs
  rid=reservations[0]['id'];prefix=f'shared-warehouse/0/reservations/{rid}/'
  call(prefix+'transition',{'kind':'commit','source_ref':'dispatch','document_date':'2026-09-02','revision':1})
  call(prefix+'transition',{'kind':'receipt','source_ref':'arrival','document_date':'2026-09-02','revision':2})
  quantity={'quantity':1,'document_date':'2026-09-02'};proof=call(prefix+'sale/preview',quantity);sale_key=str(uuid.uuid4())
  sale=call(prefix+'sale/confirm',{**quantity,'source_ref':'sale','fingerprint':proof['fingerprint']},sale_key);assert sale['cost_rub']==.34
  returned=call('shared-warehouse/0/returns/confirm',{'sale_request_key':sale_key,'quantity':1,'document_date':'2026-09-03','source_ref':'return','revision':4});assert returned['cost_rub']==.34
  # No external WB snapshot was seeded: physical ledger value is distinct from
  # the UI valuation of marketplace snapshot quantities.
  value=read_inventory_valuation().iloc[0];assert value['wb_units']==0
  assert round(value['ready_layer_amount']+value['wb_layer_amount']+value['inbound_layer_amount'],2)==2.02
  with core.connect() as c:assert c.execute('SELECT COUNT(*) FROM financial_report').fetchone()[0]==0
  source_paths=(a.app_dir/'imported_shared_basis.py',a.app_dir/'db/imported_shared_basis.py',(a.api_dir or a.app_dir)/'shared_warehouse_api.py',a.app_dir/'shop_route_scopes.json')
  output={'accepted':True,'expected_build':a.expected_build,'source':'synthetic_private','external_requests':False,'real_seller_verified':False,'public_api_transfer_replay':True,'public_api_reserve_dispatch_arrival_sale_return':True,'no_extra_financial_rows':True,'reserve_costs_rub':costs,'preserved_total_cents':202,'source_hashes':{str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}}
  a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(output,indent=2),encoding='utf-8');print(json.dumps(output))

if __name__=='__main__':main()
