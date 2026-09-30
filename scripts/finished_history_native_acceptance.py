"""Exact-image hermetic CSV acceptance. Fresh synthetic DB; no external requests."""
import argparse,asyncio,json,os,sys,tempfile,uuid
from pathlib import Path
def main():
 p=argparse.ArgumentParser();p.add_argument('--app-dir',type=Path,default=Path('/app/tenant-app'));p.add_argument('--api-dir',type=Path);p.add_argument('--expected-build');p.add_argument('--output',type=Path,required=True);p.add_argument('--local-source',action='store_true');a=p.parse_args()
 if not a.local_source:
  assert os.name!='nt' and a.expected_build
  assert Path('/tmp/redesign-build-id').read_text().strip()==a.expected_build
 sys.path.insert(0,str(a.app_dir.resolve()));sys.path.insert(0,str((a.api_dir or a.app_dir).resolve()))
 with tempfile.TemporaryDirectory(prefix='mh-csv-native-') as private:
  os.environ.update(WB_SAAS_DATA_DIR=private,WB_SAAS_TENANT_SLUG='csv-private',WB_SAAS_IS_DEMO='0',WB_SAAS_INTERNAL_API_URL='',WB_SAAS_INTERNAL_API_SECRET='',PYTHON_KEYRING_BACKEND='keyring.backends.null.Keyring',MH_LOCAL_JOBS='0')
  from db import core
  from db.migration_import import FIELDS
  from db.fifo_finished_goods import _move_finished_goods_fifo,read_inventory_valuation
  import api_server,httpx
  core.init_db()
  with core.connect() as c:c.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES(100,'A','Private synthetic item',?)",(json.dumps({'sizes':[{'chrtID':10}]}),))
  async def request(path,body,role='owner'):
   async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_server.app),base_url='http://127.0.0.1') as client:return await client.post('/api/v1/migration-import/'+path,json=body,headers={'x-tenant-account':'1','x-tenant-role':role,'x-mh-shop-id':'0'})
  def payload(kind,values):return dict(kind=kind,csv_text=';'.join(FIELDS[kind])+'\n'+';'.join(map(str,values)),mapping={f:f for f in FIELDS[kind]})
  def confirm(data):
   r=asyncio.run(request('preview',data));assert r.status_code==200,r.text;assert r.json()['can_confirm'],r.text
   body={**data,'request_key':str(uuid.uuid4()),'preview_fingerprint':r.json()['preview_fingerprint']}
   r=asyncio.run(request('confirm',body));assert r.status_code==200,r.text
   assert asyncio.run(request('confirm',body)).json()['replayed']
   assert asyncio.run(request('confirm',body,'viewer')).status_code==403
   return body
  confirm(payload('finished_lots',['opening',100,10,'2026-08-01','2026-09-01','ready',3,3,'0.335','1.01','Synthetic manually checked opening']))
  amounts=[]
  for i in range(3):
   with core.connect() as c:
    mid=c.execute("INSERT INTO inventory_movements(event_key,movement_type,nm_id,movement_date,status,created_at) VALUES(?,'consumption',100,'2026-09-02','applied','now')",(str(i),)).lastrowid
    amounts.append(_move_finished_goods_fifo(c,mid,100,'A','Private synthetic item',1,'ready',None,10)[0])
    c.execute('UPDATE product_pipeline SET ready_units=? WHERE nm_id=100',(2-i,))
   if i==1:assert read_inventory_valuation().iloc[0]['ready_value_rub']==.33
  assert amounts==[.34,.34,.33],amounts
  assert read_inventory_valuation().iloc[0]['ready_value_rub']==0
  # Independent historical opening: no physical reconstruction from archive.
  with core.connect() as c:c.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES(200,'B','Historical synthetic item',?)",(json.dumps({'sizes':[{'chrtID':20}]}),))
  confirm(payload('finished_lots',['archive',200,20,'2026-08-01','2026-09-01','ready',3,0,'0.335','0','Synthetic archived batch']))
  confirm(payload('historical_documents',['alloc','sale',10001,200,20,'2026-08-02',1,'sale',3,'archive',3,'-',30,25,5,0,0,0,0,0,'Synthetic archived sale checked']))
  with core.connect() as c:
   assert c.execute('SELECT cogs_cents FROM migration_historical_documents').fetchone()[0]==101
   assert c.execute('SELECT COUNT(*) FROM finished_goods_cost_layers WHERE nm_id=200').fetchone()[0]==0
  from db.financial_fifo_reconciliation import read_financial_fifo_reconciliation
  from db.historical_cogs import project_historical_cogs
  reconciliation=read_financial_fifo_reconciliation('2026-08-01','2026-08-31')
  assert reconciliation['status'].tolist()==['manual_source']
  _,_,summary=project_historical_cogs(reconciliation)
  assert summary['manual_source_cogs_rub']==1.01 and not summary['cogs_complete'] and summary['exact_unit_coverage_percent']==0
  result=dict(accepted=True,expected_build=a.expected_build,source='synthetic_private',external_requests=False,public_api_preview_confirm_replay=True,manual_history_preliminary=True,physical_consumption_rub=amounts,total_cents=101)
  a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
if __name__=='__main__':main()
