"""Mount read-only into the exact Linux image; no pytest or external services.

Creates an entirely fresh private directory and synthetic shops. Real business
API, scheduler, private PDF worker and encrypted backup paths are exercised.
Gateway membership is an explicit hermetic fixture; no real opt-in or WB sync.
"""
from __future__ import annotations
import argparse,asyncio,base64,hashlib,json,os,sqlite3,subprocess,sys,tempfile,time,uuid
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
from contextlib import closing

def token(sid):
    return 'fixture.'+base64.urlsafe_b64encode(json.dumps({'sid':sid}).encode()).decode().rstrip('=')+'.fixture'

def crash_probe(root,app_dir):
    """Use actual worker_slot + _execute + subprocess.run(pass_fds) path."""
    if os.name=='nt':return {'accepted':False,'reason':'Linux flock test skipped on Windows'}
    from sync_lock import sync_lock,SyncBusyError
    import shop_workspace_service as service
    probe=root/'crash';probe.mkdir()
    ready=probe/'ready';release=probe/'release'
    child_code="""import json,os,sys,time
from pathlib import Path
payload=json.load(sys.stdin)
root=Path(payload['probe_root'])
(root/'ready').write_text(str(os.getpid()))
deadline=time.monotonic()+30
while not (root/'release').exists() and time.monotonic()<deadline:time.sleep(.02)
Path(os.environ['SHOP_RESULT_PATH']).write_text(json.dumps({'fixture':'child exited'}))
"""
    (probe/'shop_workspace_worker.py').write_text(child_code)
    parent_code="""import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import shop_workspace_service as service
service.__file__=str(Path(sys.argv[2])/'service.py')
service.shops.worker_input=lambda shop:dict(revision=0,identity='fixture',token='fixture',snapshot=None,source_errors=[])
service.prepare(1,'report','2026-09-01','2026-09-19',_timeout=40,probe_root=sys.argv[2])
"""
    parent=subprocess.Popen([sys.executable,'-c',parent_code,str(app_dir),str(probe)],
                            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        deadline=time.monotonic()+12
        while not ready.exists():
            if parent.poll() is not None:raise AssertionError('Crash parent fixture failed: '+parent.communicate()[1][-1200:])
            assert time.monotonic()<deadline,'Crash child did not become ready'
            time.sleep(.02)
        child_pid=int(ready.read_text());assert child_pid>0
        parent.kill();parent.communicate(timeout=3)
        try:
            with sync_lock(root/'data'/'.shop-worker.lock'):pass
        except SyncBusyError:pass
        else:raise AssertionError('Killed parent released a living child slot')
        try:
            with service.worker_slot('sync',{},time.monotonic()+1):pass
        except ValueError:pass
        else:raise AssertionError('Actual service admitted a second child')
        release.touch();deadline=time.monotonic()+5
        while True:
            try:
                with sync_lock(root/'data'/'.shop-worker.lock'):break
            except SyncBusyError:
                assert time.monotonic()<deadline,'Child slot not released after exit'
                time.sleep(.02)
        return {'accepted':True,'actual_executor':True,'parent_killed':True,'child_slot_retained':True}
    finally:
        release.touch()
        if parent.poll() is None:parent.kill();parent.communicate(timeout=3)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--app-dir',type=Path,default=Path('/app/tenant-app'))
    parser.add_argument('--api-dir',type=Path)
    parser.add_argument('--expected-build')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--local-source',action='store_true')
    args=parser.parse_args()
    if not args.local_source:
        assert os.name!='nt','Exact-image acceptance must run on Linux'
        assert args.expected_build,'Pin the exact built image revision'
        assert Path('/tmp/redesign-build-id').read_text().strip()==args.expected_build,'Image revision mismatch'
    sys.path.insert(0,str(args.app_dir.resolve()))
    sys.path.insert(0,str((args.api_dir or args.app_dir).resolve()))
    with tempfile.TemporaryDirectory(prefix='mh-native-schedules-') as private:
        root=Path(private);data=root/'data';data.mkdir()
        from cryptography.fernet import Fernet
        key=Fernet.generate_key().decode()
        # These are private fixture values, never passed to a production volume.
        os.environ.update(WB_SAAS_DATA_DIR=str(data),WB_SAAS_TENANT_SLUG='schedule-fixture',
                          WB_SAAS_DATA_ENCRYPTION_KEY=key,WB_SAAS_DATA_ENCRYPTION_KEY_ID='fixture',
                          WB_SAAS_DATA_ENCRYPTION_KEYRING='',WB_SAAS_IS_DEMO='0',
                          WB_SAAS_INTERNAL_API_URL='',WB_SAAS_INTERNAL_API_SECRET='',
                          PYTHON_KEYRING_BACKEND='keyring.backends.null.Keyring',MH_LOCAL_JOBS='0')
        import config
        from db import core,shop_workspaces as shops,shared_warehouse as warehouse,shop_schedules as schedules
        import shop_schedule_runner as runner
        import api_server
        import httpx
        core.init_db()
        ids=[]
        for number in (1,2):
            selected_token=token('native-schedules-'+str(number))
            shop=shops.create('Private fixture '+str(number),selected_token,shops.account_identity(selected_token),register_warehouse=True)
            warehouse.grant(shop,'1',True,'owner');ids.append(shop)
        async def request(method,shop,kind=None,body=None,key=None,role='owner'):
            headers={'x-tenant-account':'1','x-tenant-role':role,'x-mh-shop-id':str(shop)}
            if key:headers['x-mh-request-key']=key
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_server.app),base_url='http://127.0.0.1') as client:
                url=f'/api/v1/shop-schedules/{shop}'+('/'+kind if kind else '')
                return await client.request(method,url,headers=headers,json=body) if body is not None else await client.request(method,url,headers=headers)
        for shop in ids:
            response=asyncio.run(request('GET',shop));assert response.status_code==200,response.text
            assert all(not value['enabled'] and not value['exists'] for value in response.json()['schedules'])
            body=dict(enabled=True,expected_generation=0,interval_minutes=60,period_days=19,metrics=['pnl'])
            key=str(uuid.uuid4())
            response=asyncio.run(request('PUT',shop,'pdf',body,key));assert response.status_code==200,response.text
            assert asyncio.run(request('PUT',shop,'pdf',body,key)).json()==response.json()
            assert asyncio.run(request('PUT',shop,'pdf',body,key,role='viewer')).status_code==403
        with core.connect() as conn:
            due=max(datetime.fromisoformat(row[0]) for row in conn.execute('SELECT next_due_utc FROM shop_schedules'))
        # Only the external membership source is stubbed. The scheduler/PDF
        # business implementation and child subprocess are not replaced.
        with patch.object(runner,'current_role',lambda actor:'owner'):
            assert runner.run_one(now=due) and runner.run_one(now=due)
            assert not runner.run_one(now=due)
        artifacts=[]
        for shop in ids:
            response=asyncio.run(request('GET',shop));assert response.status_code==200,response.text
            value=response.json();assert len(value['runs'])==1 and value['runs'][0]['status']=='ok'
            event=value['runs'][0];artifact=event['artifact'];assert artifact and event['shop_id']==shop
            snapshot=root/f'shop-{shop}.sqlite';snapshot.write_bytes(shops.read(shop)['database_snapshot'])
            with closing(sqlite3.connect(snapshot)) as conn:
                row=conn.execute('SELECT sha256,pdf FROM shop_report_assets WHERE run_id=?',(artifact['run_id'],)).fetchone()
                assert row and bytes(row[1]).startswith(b'%PDF-')
                assert hashlib.sha256(row[1]).hexdigest()==row[0]==artifact['sha256']
                assert conn.execute('SELECT enabled,email_enabled,telegram_enabled FROM report_definitions').fetchone()==(0,0,0)
                assert conn.execute('SELECT delivery_status FROM report_runs').fetchone()[0]=='not_requested'
            assert shops.read(shop)['revision']==1
            artifacts.append(dict(shop_id=shop,run_id=artifact['run_id'],sha256=artifact['sha256'],pdf_bytes=len(row[1]),start=artifact['start'],end=artifact['end']))
        assert artifacts[0]['sha256']!=artifacts[1]['sha256']
        # Native encrypted whole-database backup, not a synthetic ZIP fixture.
        import backup_tools
        payload=backup_tools.create_backup(kind='manual',keep=3).read_bytes()
        assert payload.startswith(backup_tools.BACKUP_ENCRYPTION_PREFIX_V2)
        before=[shops.read(shop)['database_snapshot'] for shop in ids]
        body=dict(enabled=False,expected_generation=1,interval_minutes=60,period_days=19,metrics=['pnl'])
        for shop in ids:
            warehouse.grant(shop,'1',False,'owner')
            value=asyncio.run(request('GET',shop)).json();assert value['can_configure'] and not value['can_enable']
            assert asyncio.run(request('PUT',shop,'pdf',body,str(uuid.uuid4()))).status_code==200
        backup_tools.restore_backup(payload)
        assert before==[shops.read(shop)['database_snapshot'] for shop in ids]
        with patch.object(runner,'current_role',lambda actor:'owner'):assert not runner.run_one(now=due)
        with core.connect() as conn:
            assert conn.execute('SELECT COUNT(*) FROM shop_schedule_events').fetchone()[0]==2
            assert conn.execute('SELECT COUNT(*) FROM shop_request_receipts').fetchone()[0]==2
            assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        crash=crash_probe(root,args.app_dir.resolve())
        if not args.local_source:assert crash['accepted']
        hashes={name:hashlib.sha256((args.app_dir/name).read_bytes()).hexdigest() for name in (
            'shop_schedule_runner.py','shop_membership.py','shop_workspace_service.py','shop_workspace_worker.py',
            'sync_lock.py','db/shop_schedules.py','db/shop_request_bridge.py','shop_route_scopes.json')}
        api_dir=(args.api_dir or args.app_dir).resolve()
        hashes.update({name:hashlib.sha256((api_dir/name).read_bytes()).hexdigest() for name in ('api_server.py','shop_schedules_api.py')})
        result=dict(accepted=True,exact_image=not args.local_source,build=args.expected_build,
                    public_api_asgi=True,real_private_pdf=True,artifacts=artifacts,
                    encrypted_backup_restore=True,no_duplicate_due=True,owner_disable_after_acl_revoke=True,
                    linux_crash=crash,gateway_membership='explicit hermetic fixture; Gateway live acceptance separate',
                    external_wb_sync=False,source_hashes=hashes)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'accepted':result['accepted'],'exact_image':result['exact_image'],'linux_crash':crash,
                      'pdf_count':len(artifacts),'output':str(args.output)}))

if __name__=='__main__':main()
