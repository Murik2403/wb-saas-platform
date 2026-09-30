import json
import os
from pathlib import Path
import subprocess
import sys

def test_real_private_policy_blocks_write_clients_and_physical_aliases(tmp_path):
    code='''
import json
from wb_api import WBAPI
WBAPI._request=lambda self,method,url,**kwargs:dict(token=self.token,method=method)
import shop_worker_policy
shop_worker_policy.install()
client=WBAPI('selected-only')
assert client._request('GET',client.PRICES_URL+'/api/v2/list/goods/filter')['token']=='selected-only'
assert client._request('POST',client.CONTENT_URL+'/content/v2/get/cards/list')['method']=='POST'
for method,url in [('POST',client.PRICES_URL+'/api/v2/upload/task'),('GET','https://attacker.invalid/read'),('PATCH',client.FEEDBACKS_URL+'/api/v1/questions')]:
 try:client._request(method,url);raise AssertionError('allowed external write')
 except ValueError:pass
import db.fifo_finished_goods as finished
from db.shared_warehouse import WarehouseScopeError
for function in (finished._create_finished_goods_layer,finished._move_finished_goods_fifo):
 try:function();raise AssertionError('allowed physical mutation')
 except WarehouseScopeError:pass
import sync
assert sync.process_sales_fifo_events()=={'sales':0,'returns':0,'errors':0}
print('policy-ok')
'''
    env=dict(os.environ,WB_SAAS_DATA_DIR=str(tmp_path))
    result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert 'policy-ok' in result.stdout

def test_private_request_sync_preserves_actual_partial_quality_not_old_clean_state(tmp_path):
    code='''
import io,json,os,sys
import sync
sync.sync_all=lambda:{'ads_error':'sensitive detail must not escape','orders':1}
sys.stdin=io.StringIO(json.dumps({'token':'unused','action':'request','actor':'owner@test','role':'owner',
 'start':'2026-09-01','end':'2026-09-02','source_errors':[],
 'request':{'method':'POST','path':'/api/v1/sync','query':'','body':'','content_type':'application/json'}}))
import shop_workspace_worker
shop_workspace_worker.main()
'''
    output=tmp_path/'result.json'
    env=dict(os.environ,WB_SAAS_DATA_DIR=str(tmp_path),SHOP_RESULT_PATH=str(output))
    result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    value=json.loads(output.read_text())
    assert value['status']==200
    assert value['sync_errors']==['ads_error','shared_fifo_pending']
    assert 'sensitive detail' not in output.read_text()


def test_public_full_sync_freshness_requires_new_successful_log(tmp_path):
    for status in ('success','partial','error','unchanged'):
        directory=tmp_path/status;directory.mkdir();output=directory/'result.json'
        code="""
import io,json,os,sys
from db.core import init_db,start_sync_log,finish_sync_log
init_db();old=start_sync_log();finish_sync_log(old,'success','old')
import sync
def full_sync():
 status=os.environ['TEST_SYNC_STATUS']
 if status!='unchanged':
  identifier=start_sync_log();finish_sync_log(identifier,status,'new')
 return {'orders':1,**({'ads_error':'private detail'} if status in {'partial','error'} else {})}
sync.sync_all=full_sync
sys.stdin=io.StringIO(json.dumps({'token':'unused','action':'request','actor':'owner@test','role':'owner',
 'start':'2026-09-01','end':'2026-09-02','source_errors':[],
 'request':{'method':'POST','path':'/api/v1/sync','query':'','body':'','content_type':'application/json'}}))
import shop_workspace_worker
shop_workspace_worker.main()
"""
        env=dict(os.environ,WB_SAAS_DATA_DIR=str(directory),SHOP_RESULT_PATH=str(output),TEST_SYNC_STATUS=status)
        result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=30)
        assert result.returncode==0,result.stderr
        value=json.loads(output.read_text())
        assert value['synced'] is (status=='success')
        assert 'shared_fifo_pending' in value['sync_errors']
