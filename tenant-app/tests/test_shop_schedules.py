import json,sqlite3,uuid
from datetime import datetime,timezone,timedelta
from concurrent.futures import ThreadPoolExecutor
from cryptography.fernet import Fernet
import pytest
import config
from db import core,shop_workspaces as shops,shared_warehouse as warehouse,shop_schedules as schedules
import shop_schedule_runner as runner

NOW=datetime(2026,9,30,10,tzinfo=timezone.utc)

@pytest.fixture
def state(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'DB_PATH',tmp_path/'main.sqlite3')
    monkeypatch.setattr(config,'DATA_DIR',tmp_path)
    monkeypatch.setattr(config,'DATA_ENCRYPTION_KEY',Fernet.generate_key().decode())
    monkeypatch.setattr(config,'DATA_ENCRYPTION_KEY_ID','test')
    monkeypatch.setattr(config,'DATA_ENCRYPTION_KEYRING','')
    core.init_db()
    shop=shops.create('First','secret','verified',register_warehouse=True)
    warehouse.grant(shop,'1',True,'owner')
    path=tmp_path/'snapshot.sqlite3'
    with sqlite3.connect(path) as conn:conn.execute('CREATE TABLE evidence(n INTEGER)');conn.execute('INSERT INTO evidence VALUES(42)')
    schedules.initialize()
    monkeypatch.setattr(runner,'current_role',lambda actor:'owner')
    def prepare(*args,**kwargs):
        row=shops.read(args[0])
        return dict(base_revision=row['revision'],identity=row['account_identity'],snapshot=path.read_bytes(),response={'saved':True},synced=True,source_errors=[])
    monkeypatch.setattr(runner,'prepare',prepare)
    return dict(shop=shop,path=path)

def configure(state,kind='sync',**kw):
    body=dict(enabled=True,expected_generation=0,interval_minutes=15 if kind=='sync' else 60,period_days=30,metrics=[] if kind=='sync' else ['pnl'],now=NOW)
    body.update(kw)
    return schedules.configure(state['shop'],kind,'1','owner',str(uuid.uuid4()),**body)

def events():
    with core.connect() as conn:return [dict(r) for r in conn.execute('SELECT * FROM shop_schedule_events')]

def test_default_disabled_owner_opt_in_requires_existing_acl_no_grant(state):
    value=schedules.listing(state['shop'],'1','owner')
    assert all(not row['enabled'] and not row['exists'] for row in value['schedules'])
    with pytest.raises(warehouse.WarehouseScopeError):schedules.configure(state['shop'],'sync','2','owner',str(uuid.uuid4()),enabled=True,expected_generation=0,interval_minutes=15,period_days=30,metrics=[])
    with pytest.raises(warehouse.WarehouseScopeError):schedules.configure(state['shop'],'sync','1','manager',str(uuid.uuid4()),enabled=True,expected_generation=0,interval_minutes=15,period_days=30,metrics=[])
    assert not runner.run_one(now=NOW)
    configure(state)
    with core.connect() as conn:assert conn.execute('SELECT COUNT(*) FROM warehouse_shop_acl').fetchone()[0]==1

def test_same_uuid_exact_config_replay_and_changed_payload_conflict(state):
    key=str(uuid.uuid4());body=dict(enabled=True,expected_generation=0,interval_minutes=15,period_days=30,metrics=[],now=NOW)
    first=schedules.configure(state['shop'],'sync','1','owner',key,**body)
    assert schedules.configure(state['shop'],'sync','1','owner',key,**body)==first
    with pytest.raises(warehouse.WarehouseConflict):schedules.configure(state['shop'],'sync','1','owner',key,**dict(body,period_days=10))
    warehouse.grant(state['shop'],'1',False,'owner')
    with pytest.raises(warehouse.WarehouseScopeError):schedules.configure(state['shop'],'sync','1','owner',key,**body)

def test_config_replay_fingerprint_includes_frozen_seller_identity(state,monkeypatch):
    key=str(uuid.uuid4());body=dict(enabled=True,expected_generation=0,interval_minutes=15,period_days=30,metrics=[],now=NOW)
    schedules.configure(state['shop'],'sync','1','owner',key,**body)
    original=schedules.target
    def changed(conn,*args,**kwargs):
        row=dict(original(conn,*args,**kwargs));row['account_identity']='different-seller';return row
    monkeypatch.setattr(schedules,'target',changed)
    with pytest.raises(warehouse.WarehouseConflict):schedules.configure(state['shop'],'sync','1','owner',key,**body)

def test_owner_can_disable_after_acl_removed_without_creating_grant(state):
    configure(state)
    with core.connect() as conn:conn.execute('DELETE FROM warehouse_shop_acl WHERE account=?',('1',))
    value=configure(state,enabled=False,expected_generation=1)
    assert not value['enabled']
    with core.connect() as conn:assert conn.execute('SELECT COUNT(*) FROM warehouse_shop_acl').fetchone()[0]==0

def test_encrypted_native_backup_restores_schedule_event_receipt_and_private_pdf(state,monkeypatch):
    import backup_tools as backup,sync_lock
    from shop_workspace_service import prepare
    monkeypatch.setattr(runner,'prepare',prepare)
    configure(state,'pdf')
    runner.run_one(now=NOW+timedelta(minutes=60))
    assert events()[0]['status']=='ok' and events()[0]['artifact_json']
    row=shops.read(state['shop']);before=events()
    for name,value in [('DATA_DIR',config.DATA_DIR),('DB_PATH',core.DB_PATH),('SETTINGS_PATH',config.DATA_DIR/'settings.json'),('BACKUP_DIR',config.DATA_DIR/'backups'),('DATA_ENCRYPTION_KEY',config.DATA_ENCRYPTION_KEY),('DATA_ENCRYPTION_KEY_ID','test'),('DATA_ENCRYPTION_KEYRING','')]:monkeypatch.setattr(backup,name,value)
    # backup_tools creates this directory at module import. Full suites import
    # it before our fixture, so a newly redirected directory must be prepared.
    backup.BACKUP_DIR.mkdir(parents=True,exist_ok=True)
    monkeypatch.setattr(sync_lock,'DB_PATH',core.DB_PATH)
    payload=backup.create_backup(kind='manual',keep=3).read_bytes()
    assert payload.startswith(backup.BACKUP_ENCRYPTION_PREFIX_V2) and b'secret' not in payload
    configure(state,'pdf',enabled=False,expected_generation=1)
    backup.restore_backup(payload)
    assert events()==before
    assert shops.read(state['shop'])['database_snapshot']==row['database_snapshot']
    assert next(s for s in schedules.listing(state['shop'],'1','owner')['schedules'] if s['kind']=='pdf')['enabled']
    assert not runner.run_one(now=NOW+timedelta(minutes=60))
    with core.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM shop_request_receipts').fetchone()[0]==1
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'

def test_one_atomic_success_restart_duplicate_tick_and_utc_window(state,monkeypatch):
    configure(state);calls=[]
    old=runner.prepare
    monkeypatch.setattr(runner,'prepare',lambda *a,**k:(calls.append((a,k)),old(*a,**k))[1])
    due=NOW+timedelta(minutes=15)
    assert runner.run_one(now=due)
    assert not runner.run_one(now=due)
    assert shops.read(state['shop'])['revision']==1 and shops.read(state['shop'])['synced_at']
    assert len(events())==1 and events()[0]['status']=='ok'
    assert calls[0][0][2:]==('2026-09-01','2026-09-30')
    with core.connect() as conn:assert conn.execute('SELECT COUNT(*) FROM shop_request_receipts').fetchone()[0]==1

@pytest.mark.parametrize('case',['role_before','role_after','acl_after','disable_after','credential_after','revision_after','partial'])
def test_revocation_conflict_partial_preserve_snapshot_and_do_not_commit(state,monkeypatch,case):
    configure(state);old=runner.prepare;calls=[]
    if case=='role_before':monkeypatch.setattr(runner,'current_role',lambda actor:'viewer')
    elif case=='role_after':
        roles=iter(['owner','viewer']);monkeypatch.setattr(runner,'current_role',lambda actor:next(roles))
    def prepared(*a,**k):
        calls.append(True);result=old(*a,**k)
        if case=='acl_after':warehouse.grant(state['shop'],'1',False,'owner')
        elif case=='disable_after':configure(state,enabled=False,expected_generation=1)
        elif case=='credential_after':shops.reconnect(state['shop'],'different-secret','verified')
        elif case=='revision_after':shops.store_snapshot(state['shop'],0,state['path'].read_bytes())
        elif case=='partial':result['synced']=False;result['source_errors']=['financial_error']
        return result
    monkeypatch.setattr(runner,'prepare',prepared)
    runner.run_one(now=NOW+timedelta(minutes=15))
    row=shops.read(state['shop'])
    assert row['revision']==(1 if case in {'revision_after','credential_after'} else 0)
    assert row['synced_at'] is None
    with core.connect() as conn:assert conn.execute('SELECT COUNT(*) FROM shop_request_receipts').fetchone()[0]==0
    if case=='disable_after':assert not events()
    else:assert events()[-1]['status']=='failed'
    if case=='role_before':assert not calls

def test_concurrent_claim_lease_expiry_same_run_uuid_and_lost_lease_guard(state):
    configure(state);due=NOW+timedelta(minutes=15)
    with ThreadPoolExecutor(2) as pool:jobs=list(pool.map(lambda _:schedules.claim(due),range(2)))
    job=next(j for j in jobs if j)
    assert sum(j is not None for j in jobs)==1
    replacement=schedules.claim(due+timedelta(seconds=schedules.LEASE_SECONDS+1))
    assert replacement['run_key']==job['run_key'] and replacement['lease_key']!=job['lease_key']
    with core.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        with pytest.raises(warehouse.WarehouseConflict):schedules.finish(conn,job,status='failed',now=due)

def test_failure_bounded_retry_same_uuid_then_skips_overdue_slots(state,monkeypatch):
    configure(state);monkeypatch.setattr(runner,'current_role',lambda actor:None)
    due=NOW+timedelta(minutes=15)
    for minutes in (0,5,15):assert runner.run_one(now=due+timedelta(minutes=minutes))
    assert len({r['run_key'] for r in events()})==1 and len(events())==3
    assert not runner.run_one(now=due+timedelta(minutes=15))
    assert all('secret' not in str(row) for row in events())


@pytest.mark.parametrize('raised',[False,True])
def test_pdf_budget_busy_defers_same_due_without_attempt_or_failure_event(state,monkeypatch,raised):
    from reports.pdf_budget import PdfBusy
    configure(state,'pdf')
    before=shops.read(state['shop'])
    def busy(*args,**kwargs):
        if raised:raise PdfBusy('busy')
        return {'response':{'pdf_busy':True,'status':503},'snapshot':None}
    monkeypatch.setattr(runner,'prepare',busy)
    due=NOW+timedelta(minutes=60)
    for seconds in (0,5,10,15):
        assert runner.run_one(now=due+timedelta(seconds=seconds))
        with core.connect() as conn:
            row=conn.execute('SELECT * FROM shop_schedules WHERE kind=?',('pdf',)).fetchone()
            assert row['next_due_utc']==schedules.utc(due) and row['attempts']==0 and row['lease_key'] is None
        assert not runner.run_one(now=due+timedelta(seconds=seconds+1))
    assert events()==[] and shops.read(state['shop'])==before


def test_pdf_busy_deferral_rejects_a_replaced_lease(state):
    configure(state,'pdf')
    due=NOW+timedelta(minutes=60)
    original=schedules.claim(due)
    replacement=schedules.claim(due+timedelta(seconds=schedules.LEASE_SECONDS+1))
    with core.connect() as conn:
        before=dict(conn.execute('SELECT * FROM shop_schedules WHERE id=?',(replacement['id'],)).fetchone())
        with pytest.raises(warehouse.WarehouseConflict):schedules.defer_pdf_busy(conn,original,now=due)
        after=dict(conn.execute('SELECT * FROM shop_schedules WHERE id=?',(replacement['id'],)).fetchone())
    assert before==after and events()==[]


def test_real_scheduled_child_busy_is_fast_and_keeps_original_snapshot_due_and_attempts(state,monkeypatch):
    from time import monotonic
    from shop_workspace_service import prepare
    monkeypatch.setattr(runner,'prepare',prepare)
    configure(state,'pdf')
    bad=config.DATA_DIR/'bad-shared-mount';bad.write_bytes(b'dummy')
    monkeypatch.setenv('WB_SAAS_PDF_SLOT_DIR',str(bad/'slots'))
    monkeypatch.setenv('WB_SAAS_PDF_WAIT_SECONDS','120')  # scheduled child must explicitly ask for zero wait
    before=shops.read(state['shop'])
    due=NOW+timedelta(minutes=60)
    started=monotonic()
    assert runner.run_one(now=due)
    assert monotonic()-started<15
    assert shops.read(state['shop'])==before and events()==[]
    with core.connect() as conn:
        row=conn.execute('SELECT * FROM shop_schedules WHERE kind=?',('pdf',)).fetchone()
        assert row['attempts']==0 and row['next_due_utc']==schedules.utc(due) and row['lease_key'] is None

def test_atomic_event_failure_rolls_back_snapshot_receipt_and_timestamp(state,monkeypatch):
    configure(state);original=schedules.finish
    def fail(conn,job,**kwargs):
        if kwargs['status']=='ok':raise RuntimeError('injected post snapshot failure')
        return original(conn,job,**kwargs)
    monkeypatch.setattr(schedules,'finish',fail)
    runner.run_one(now=NOW+timedelta(minutes=15))
    assert shops.read(state['shop'])['revision']==0
    with core.connect() as conn:assert conn.execute('SELECT COUNT(*) FROM shop_request_receipts').fetchone()[0]==0
    assert events()[0]['status']=='failed'

def test_sql_history_and_schedule_identity_replace_update_delete_guards(state):
    row=configure(state);runner.run_one(now=NOW+timedelta(minutes=15))
    with core.connect() as conn:
        for sql in ('UPDATE shop_schedule_events SET status="failed"','DELETE FROM shop_schedule_events','INSERT OR REPLACE INTO shop_schedule_events SELECT * FROM shop_schedule_events','UPDATE shop_schedule_config_receipts SET actor="2"','DELETE FROM shop_schedule_config_receipts','INSERT OR REPLACE INTO shop_schedule_config_receipts SELECT * FROM shop_schedule_config_receipts','INSERT OR REPLACE INTO shop_schedules SELECT * FROM shop_schedules','UPDATE shop_schedules SET actor="2"','DELETE FROM shop_schedules'):
            with pytest.raises(sqlite3.IntegrityError):conn.execute(sql)

def test_supplied_bridge_does_not_commit_caller_and_rollback_preserves_schedule(state):
    from db import shop_request_bridge as bridge
    with core.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        bridge.commit(state['shop'],'verified',0,str(uuid.uuid4()),{},'1','manager',snapshot=state['path'].read_bytes(),response={'ok':True},_conn=conn)
        assert conn.in_transaction
        conn.rollback()
    assert shops.read(state['shop'])['revision']==0

def test_real_private_pdf_fixed_period_archive_hash_two_shops_and_no_delivery(state,monkeypatch):
    import hashlib
    from shop_workspace_service import prepare
    second=shops.create('Second','unrelated-token','other-seller',register_warehouse=True)
    prepared=prepare(state['shop'],'scheduled_pdf','2026-09-01','2026-09-19',
                     schedule_id=7,generation=1,metrics=['pnl'],run_key=str(uuid.uuid4()),actor='1',role='manager')
    artifact=prepared['response']['artifact']
    assert artifact['start']=='2026-09-01' and artifact['end']=='2026-09-19'
    path=state['path'].parent/'actual-private-pdf.sqlite';path.write_bytes(prepared['snapshot'])
    with sqlite3.connect(path) as conn:
        asset=conn.execute('SELECT sha256,pdf FROM shop_report_assets WHERE run_id=?',(artifact['run_id'],)).fetchone()
        assert bytes(asset[1]).startswith(b'%PDF-') and hashlib.sha256(asset[1]).hexdigest()==asset[0]==artifact['sha256']
        definition=conn.execute('SELECT enabled,email_enabled,telegram_enabled FROM report_definitions').fetchone()
        assert definition==(0,0,0)
        assert conn.execute('SELECT delivery_status FROM report_runs').fetchone()[0]=='not_requested'
    assert shops.read(state['shop'])['revision']==0 and shops.read(second)['database_snapshot'] is None

def test_cross_process_child_slot_serializes_read_and_writes_fail_fast(state,monkeypatch):
    import subprocess,sys
    from threading import Event
    import shop_workspace_service as service
    script="from sync_lock import sync_lock;from pathlib import Path;import sys;\nwith sync_lock(Path(sys.argv[1])):\n print('locked',flush=True);sys.stdin.readline()"
    proc=subprocess.Popen([sys.executable,'-c',script,str(config.DATA_DIR/'.shop-worker.lock')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    called=Event()
    monkeypatch.setattr(service,'_execute',lambda *a,**k:(called.set(),{'ok':True})[1])
    try:
        assert proc.stdout.readline().strip()=='locked'
        with pytest.raises(ValueError,match='уже обрабатывается'):service.prepare(state['shop'],'sync','2026-09-01','2026-09-02',days=1)
        assert not called.is_set()
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(service.prepare,state['shop'],'report','2026-09-01','2026-09-02',_timeout=5)
            assert not called.wait(.1)
            proc.communicate('\n',timeout=5)
            assert future.result()['ok'] and called.is_set()
    finally:
        if proc.poll() is None:proc.kill();proc.communicate(timeout=5)

@pytest.mark.skipif(__import__('os').name=='nt',reason='Production Linux flock open-file-description inheritance')
def test_linux_parent_kill_keeps_inherited_child_slot_until_child_exit(state):
    import subprocess,sys,time
    from sync_lock import sync_lock,SyncBusyError
    release=config.DATA_DIR/'release-child'
    child_code="import time,sys;from pathlib import Path;\np=Path(sys.argv[1]);deadline=time.monotonic()+10\nwhile not p.exists() and time.monotonic()<deadline:time.sleep(.02)"
    script=f"""from sync_lock import sync_lock
from pathlib import Path
import subprocess,sys,time
with sync_lock(Path(sys.argv[1])) as handle:
 child=subprocess.Popen([sys.executable,'-c',{child_code!r},sys.argv[2]],pass_fds=(handle.fileno(),),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 print(child.pid,flush=True)
 sys.stdin.readline()
 child.wait()
"""
    path=config.DATA_DIR/'.shop-worker.lock'
    parent=subprocess.Popen([sys.executable,'-c',script,str(path),str(release)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert int(parent.stdout.readline())>0
        parent.kill();parent.communicate(timeout=3)
        with pytest.raises(SyncBusyError):
            with sync_lock(path):pass
        release.touch()
        deadline=time.monotonic()+4
        while True:
            try:
                with sync_lock(path):break
            except SyncBusyError:
                assert time.monotonic()<deadline
                time.sleep(.02)
    finally:
        release.touch()
        if parent.poll() is None:parent.kill();parent.communicate(timeout=3)
