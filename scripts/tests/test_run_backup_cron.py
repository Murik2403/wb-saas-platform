import importlib.util,json,sys
import pytest
from pathlib import Path
from types import SimpleNamespace

def module(monkeypatch):
    if sys.platform=='win32':monkeypatch.setitem(sys.modules,'fcntl',SimpleNamespace(LOCK_EX=1,LOCK_NB=2,flock=lambda *args:None))
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    spec=importlib.util.spec_from_file_location('backup_cron_test',Path(__file__).resolve().parents[1]/'run_backup_cron.py')
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

def test_scheduler_copies_encrypted_history_but_never_enables_remote_deletion(tmp_path,monkeypatch):
    cron=module(monkeypatch)
    monkeypatch.setattr(cron,'inventory_groups',lambda:['demo'])
    for group in ('control-plane','retired'):
        folder=tmp_path/group;folder.mkdir();(folder/'ok.tar.gz.enc').write_bytes(b'encrypted')
    empty=tmp_path/'plaintext-only';empty.mkdir();(empty/'old.tar.gz').write_bytes(b'plaintext')
    monkeypatch.setenv('WB_SAAS_BACKUP_DIR',str(tmp_path))
    monkeypatch.setenv('WB_SAAS_OFFSITE_RETENTION_DAYS','7')
    monkeypatch.setenv('WB_SAAS_OFFSITE_DELETE_CONFIRMED','1')
    env=cron.environment('owner@example.com')
    assert set(env['WB_SAAS_OFFSITE_ARCHIVE_GROUPS'].split(','))=={'control-plane','retired'}
    assert 'WB_SAAS_OFFSITE_RETENTION_DAYS' not in env
    assert 'WB_SAAS_OFFSITE_DELETE_CONFIRMED' not in env
    assert json.loads(env['WB_SAAS_BACKUP_ALERT_COMMAND_JSON'])[-1]=='owner@example.com'

def test_missing_owner_recipient_is_rejected_before_mail(tmp_path,monkeypatch):
    cron=module(monkeypatch)
    import pytest
    with pytest.raises(ValueError):cron.alert_command('owner@example.com\nBcc: attacker@example.com')

def test_gateway_snapshot_failure_propagates_without_exposing_process_output(monkeypatch,capsys):
    cron=module(monkeypatch)
    def run(*args,**kwargs):return SimpleNamespace(returncode=1,stdout='private-secret',stderr='private-secret')
    monkeypatch.setattr(cron.subprocess,'run',run)
    assert cron.gateway_exec('raise RuntimeError("synthetic")')==1
    assert 'private' not in capsys.readouterr().out


def test_new_registered_tenant_without_archives_is_required_and_alerts(tmp_path,monkeypatch):
    cron=module(monkeypatch)
    monkeypatch.setenv('WB_SAAS_BACKUP_DIR',str(tmp_path))
    monkeypatch.setenv('WB_SAAS_BACKUP_STATUS_FILE',str(tmp_path/'state.json'))
    monkeypatch.delenv('WB_SAAS_BACKUP_REQUIRED_GROUPS',raising=False)
    monkeypatch.setattr(cron,'inventory_groups',lambda:['demo','new-client'],raising=False)
    for group in ('control-plane','demo'):
        folder=tmp_path/group;folder.mkdir();(folder/'ok.tar.gz.enc').write_bytes(b'WBSAASBK2fixture')
    (tmp_path/'state.json').write_text(json.dumps({'verified_at':cron.backup_offsite.time.time(),'offsite_verified':True}))
    env=cron.environment('owner@example.com')
    assert env['WB_SAAS_BACKUP_REQUIRED_GROUPS']=='control-plane,demo,training-media,new-client'
    events=[]
    monkeypatch.setattr(cron.backup_offsite,'alert',lambda event,env:events.append(event) or 'accepted')
    assert cron.backup_offsite.check_freshness(env)==1
    assert events[0]['code']=='local_backup_stale'


def test_explicit_required_groups_are_additive_and_control_plane_cannot_be_removed(tmp_path,monkeypatch):
    cron=module(monkeypatch)
    monkeypatch.setenv('WB_SAAS_BACKUP_DIR',str(tmp_path))
    monkeypatch.setenv('WB_SAAS_BACKUP_REQUIRED_GROUPS','escrow,paused')
    monkeypatch.setattr(cron,'inventory_groups',lambda:['demo','paused','new-client'])
    assert cron.environment('owner@example.com')['WB_SAAS_BACKUP_REQUIRED_GROUPS']=='control-plane,demo,training-media,escrow,paused,new-client'


def test_demo_is_required_even_when_absent_from_customer_registry(tmp_path,monkeypatch):
    cron=module(monkeypatch)
    monkeypatch.setenv('WB_SAAS_BACKUP_DIR',str(tmp_path))
    monkeypatch.delenv('WB_SAAS_BACKUP_REQUIRED_GROUPS',raising=False)
    monkeypatch.setattr(cron,'inventory_groups',lambda:['new-client'])
    assert cron.environment('owner@example.com')['WB_SAAS_BACKUP_REQUIRED_GROUPS']=='control-plane,demo,training-media,new-client'


@pytest.mark.parametrize('configured',['../private','ok,,missing','BadSlug'])
def test_invalid_explicit_groups_fail_closed(tmp_path,monkeypatch,configured):
    cron=module(monkeypatch)
    monkeypatch.setenv('WB_SAAS_BACKUP_DIR',str(tmp_path))
    monkeypatch.setenv('WB_SAAS_BACKUP_REQUIRED_GROUPS',configured)
    monkeypatch.setattr(cron,'inventory_groups',lambda:['demo'])
    with pytest.raises(RuntimeError,match='backup_inventory_unavailable'):
        cron.environment('owner@example.com')


@pytest.mark.parametrize('result',[
    SimpleNamespace(returncode=1,stdout='private-secret',stderr='private-secret'),
    SimpleNamespace(returncode=0,stdout='["../private"]',stderr=''),
    SimpleNamespace(returncode=0,stdout='{"slug":"demo"}',stderr=''),
    SimpleNamespace(returncode=0,stdout='[true]',stderr=''),
])
def test_inventory_rejects_failure_or_invalid_shape_without_logging_output(monkeypatch,capsys,result):
    cron=module(monkeypatch)
    monkeypatch.setattr(cron.subprocess,'run',lambda *a,**k:result)
    with pytest.raises(RuntimeError,match='backup_inventory_unavailable'):
        cron.inventory_groups()
    assert 'private' not in capsys.readouterr().out


def test_inventory_read_is_bounded_and_only_returns_validated_unique_slugs(monkeypatch):
    cron=module(monkeypatch);calls=[]
    def run(args,**kwargs):
        calls.append((args,kwargs))
        return SimpleNamespace(returncode=0,stdout='["stopped-tenant","demo","demo"]',stderr='')
    monkeypatch.setattr(cron.subprocess,'run',run)
    assert cron.inventory_groups()==['stopped-tenant','demo']
    args,kwargs=calls[0]
    assert args[:5]==['docker','exec','-i','wb-saas-gateway-1','python']
    assert 0<kwargs['timeout']<=30
    assert 'SELECT DISTINCT slug FROM tenant_instances' in args[-1]


def test_inventory_timeout_becomes_sanitized_failure(monkeypatch):
    cron=module(monkeypatch)
    def run(*a,**k):raise cron.subprocess.TimeoutExpired('private-secret',30)
    monkeypatch.setattr(cron.subprocess,'run',run)
    with pytest.raises(RuntimeError,match='backup_inventory_unavailable'):
        cron.inventory_groups()


def test_registry_keeps_stopped_data_and_drops_finalized_deletion_without_requiring_history(monkeypatch,tmp_path):
    from contextlib import contextmanager,redirect_stdout
    import io,sqlite3
    cron=module(monkeypatch)
    conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
    conn.execute('CREATE TABLE tenant_instances (slug TEXT,status TEXT)')
    conn.executemany('INSERT INTO tenant_instances VALUES (?,?)',[('demo','running'),('paused','stopped'),('former','stopped')])
    @contextmanager
    def connect():yield conn
    monkeypatch.setitem(sys.modules,'logic',SimpleNamespace(db=SimpleNamespace(connect=connect)))
    def run(args,**kwargs):
        output=io.StringIO()
        with redirect_stdout(output):exec(args[-1],{})
        return SimpleNamespace(returncode=0,stdout=output.getvalue(),stderr='')
    monkeypatch.setattr(cron.subprocess,'run',run)
    monkeypatch.setenv('WB_SAAS_BACKUP_DIR',str(tmp_path))
    monkeypatch.delenv('WB_SAAS_BACKUP_REQUIRED_GROUPS',raising=False)
    former=tmp_path/'former';former.mkdir();(former/'ok.tar.gz.enc').write_bytes(b'WBSAASBK2fixture')
    assert cron.environment('owner@example.com')['WB_SAAS_BACKUP_REQUIRED_GROUPS']=='control-plane,demo,training-media,former,paused'
    # finalize_deletion removes the tenant_instances row; encrypted history
    # remains copy-only rather than requiring a new snapshot of erased data.
    conn.execute("DELETE FROM tenant_instances WHERE slug='former'")
    env=cron.environment('owner@example.com')
    assert env['WB_SAAS_BACKUP_REQUIRED_GROUPS']=='control-plane,demo,training-media,paused'
    assert env['WB_SAAS_OFFSITE_ARCHIVE_GROUPS']=='former'
    conn.close()


@pytest.mark.parametrize('mode',['daily','check'])
def test_inventory_failure_alerts_and_never_runs_backup_or_freshness(tmp_path,monkeypatch,capsys,mode):
    cron=module(monkeypatch)
    monkeypatch.setattr(cron.os,'geteuid',lambda:0,raising=False)
    monkeypatch.setattr(cron,'open',lambda *a,**k:(tmp_path/'lock').open('a'),raising=False)
    monkeypatch.setattr(cron.fcntl,'flock',lambda *a:None)
    def failed(*args):raise RuntimeError('backup_inventory_unavailable')
    monkeypatch.setattr(cron,'environment',failed)
    monkeypatch.setattr(cron,'gateway_exec',lambda *a,**k:pytest.fail('snapshot ran after inventory failure'))
    monkeypatch.setattr(cron.backup_offsite,'run',lambda *a,**k:pytest.fail('copy ran after inventory failure'))
    monkeypatch.setattr(cron.backup_offsite,'check_freshness',lambda *a,**k:pytest.fail('check ran after inventory failure'))
    events=[]
    monkeypatch.setattr(cron.backup_offsite,'alert',lambda event,env:events.append(event) or 'accepted')
    monkeypatch.setattr(sys,'argv',['run_backup_cron.py',mode,'--alert-to','owner@example.com'])
    assert cron.main()==1
    assert events==[{'kind':'backup_inventory_failure','code':'backup_inventory_unavailable'}]
    assert 'backup_inventory_unavailable' in capsys.readouterr().out


@pytest.mark.parametrize('training_fails',[False,True])
def test_daily_order_snapshots_then_training_then_copy_failure_stops_upload(tmp_path,monkeypatch,capsys,training_fails):
    cron=module(monkeypatch);events=[]
    monkeypatch.setattr(cron.os,'geteuid',lambda:0,raising=False)
    monkeypatch.setattr(cron,'open',lambda *a,**k:(tmp_path/'lock').open('a'),raising=False)
    monkeypatch.setattr(cron.fcntl,'flock',lambda *a:None)
    monkeypatch.setattr(cron,'environment',lambda recipient:{'WB_SAAS_BACKUP_DIR':str(tmp_path)})
    monkeypatch.setattr(cron,'gateway_exec',lambda *a,**kw:events.append('snapshots') or 0)
    def training(*args):
        events.append('training')
        if training_fails:raise RuntimeError('synthetic-private-do-not-log')
        return {'ok':True,'kind':'training_media_backup','files':32,'lessons':16}
    monkeypatch.setattr(cron.training_media_backup_runtime,'run',training)
    monkeypatch.setattr(cron.backup_offsite,'run',lambda env:events.append('offsite') or 0)
    monkeypatch.setattr(cron.backup_offsite,'alert',lambda event,env:events.append('alert') or 'accepted')
    monkeypatch.setattr(sys,'argv',['run_backup_cron.py','daily','--alert-to','owner@example.com'])
    assert cron.main()==int(training_fails)
    assert events==(['snapshots','training','alert'] if training_fails else ['snapshots','training','offsite'])
    assert 'synthetic-private-do-not-log' not in capsys.readouterr().out


def test_missing_training_media_group_fails_hourly_freshness_without_running_training(tmp_path,monkeypatch):
    cron=module(monkeypatch)
    monkeypatch.setenv('WB_SAAS_BACKUP_DIR',str(tmp_path));monkeypatch.setenv('WB_SAAS_BACKUP_STATUS_FILE',str(tmp_path/'state.json'))
    monkeypatch.delenv('WB_SAAS_BACKUP_REQUIRED_GROUPS',raising=False)
    monkeypatch.setattr(cron,'inventory_groups',lambda:[])
    for group in ('control-plane','demo'):
        folder=tmp_path/group;folder.mkdir();(folder/'a.tar.gz.enc').write_bytes(b'WBSAASBK2fixture')
    (tmp_path/'state.json').write_text(json.dumps({'verified_at':cron.backup_offsite.time.time(),'offsite_verified':True}))
    monkeypatch.setattr(cron.training_media_backup_runtime,'run',lambda *a,**k:pytest.fail('hourly watchdog must not pack'))
    alerts=[];monkeypatch.setattr(cron.backup_offsite,'alert',lambda event,env:alerts.append(event) or 'accepted')
    assert cron.backup_offsite.check_freshness(cron.environment('owner@example.com'))==1
    assert alerts[0]['code']=='local_backup_stale'
