import json,os,time
from types import SimpleNamespace
from pathlib import Path
import scripts.backup_offsite as offsite

def environment(tmp_path):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    key=rsa.generate_private_key(public_exponent=65537,key_size=3072)
    public=tmp_path/'public.pem';public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    (tmp_path/'control-plane').mkdir()
    (tmp_path/'control-plane'/'fixture.tar.gz.enc').write_bytes(b'WBSAASBK1fixture')
    (tmp_path/'control-plane'/'.fixture.tar.gz.partial').write_bytes(b'plaintext')
    return {'WB_SAAS_BACKUP_DIR':str(tmp_path),'WB_SAAS_BACKUP_STATUS_FILE':str(tmp_path/'status.json'),
            'WB_SAAS_OFFSITE_LOG_FILE':str(tmp_path/'offsite.log'),
            'WB_SAAS_OFFSITE_PUBLIC_RECIPIENT':str(public),'WB_SAAS_OFFSITE_STAGING_DIR':str(tmp_path.parent/(tmp_path.name+'-staging')),
            'WB_SAAS_OFFSITE_REMOTE':'synthetic:bucket/prefix','WB_SAAS_OFFSITE_KEY_ESCROW_CONFIRMED':'1'}

def test_copy_and_check_include_encrypted_only_and_never_sync_or_delete(tmp_path,monkeypatch):
    calls=[];env=environment(tmp_path)
    monkeypatch.setattr(offsite.subprocess,'run',lambda args,**kwargs:(calls.append(args),SimpleNamespace(returncode=0))[1])
    assert offsite.run(env)==0
    assert [a[1] for a in calls]==['copy','check']
    assert all('--files-from' in a and a[3].endswith('/offsite-v2') for a in calls)
    assert '--download' in calls[1]
    assert json.loads((tmp_path/'status.json').read_text())['offsite_verified']

def test_unconfirmed_separate_key_escrow_does_not_disable_existing_copy(tmp_path,monkeypatch):
    env=environment(tmp_path);env.pop('WB_SAAS_OFFSITE_KEY_ESCROW_CONFIRMED')
    calls=[];monkeypatch.setattr(offsite.subprocess,'run',lambda args,**kwargs:(calls.append(args),SimpleNamespace(returncode=0))[1])
    assert offsite.run(env)==0 and len(calls)==2
    status=json.loads((tmp_path/'status.json').read_text())
    assert status['key_escrow_confirmed'] is False and status['alert']=='unconfigured'

def test_26_hour_local_freshness_failure_alerts_without_remote_copy(tmp_path,monkeypatch):
    env=environment(tmp_path);env['WB_SAAS_BACKUP_ALERT_COMMAND_JSON']='["synthetic-alert"]'
    archive=tmp_path/'control-plane'/'fixture.tar.gz.enc';os.utime(archive,(time.time()-27*3600,)*2)
    calls=[];monkeypatch.setattr(offsite.subprocess,'run',lambda args,**kwargs:(calls.append((args,kwargs)),SimpleNamespace(returncode=0))[1])
    assert offsite.run(env)==1 and calls[0][0]==['synthetic-alert']
    assert json.loads(calls[0][1]['input'])['code']=='local_backup_stale'

def test_remote_delete_requires_explicit_bounded_policy(tmp_path,monkeypatch):
    env=environment(tmp_path);env['WB_SAAS_OFFSITE_RETENTION_DAYS']='30'
    calls=[];monkeypatch.setattr(offsite.subprocess,'run',lambda args,**kwargs:(calls.append(args),SimpleNamespace(returncode=0))[1])
    assert offsite.run(env)==1 and not any(a[1]=='delete' for a in calls)
    env['WB_SAAS_OFFSITE_DELETE_CONFIRMED']='1';calls.clear()
    assert offsite.run(env)==0 and calls[-1][1]=='delete' and calls[-1][calls[-1].index('--min-age')+1]=='30d'

def test_provider_error_is_sanitized_and_alert_failure_is_not_claimed_sent(tmp_path,monkeypatch):
    env=environment(tmp_path);env['WB_SAAS_BACKUP_ALERT_COMMAND_JSON']='["synthetic-alert"]'
    monkeypatch.setattr(offsite.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=1,stderr='private-access-token'))
    assert offsite.run(env)==1
    text=(tmp_path/'status.json').read_text();assert 'private' not in text
    assert json.loads(text)['alert']=='failed'

def test_independent_watchdog_detects_offsite_stale_without_destroying_success_evidence(tmp_path):
    env=environment(tmp_path);status=Path(env['WB_SAAS_BACKUP_STATUS_FILE'])
    original={'offsite_verified':True,'verified_at':time.time()-27*3600}
    status.write_text(json.dumps(original))
    assert offsite.check_freshness(env)==1
    assert json.loads(status.read_text())==original
    assert json.loads(status.with_name('status.health.json').read_text())['error']=='offsite_backup_stale'

def test_missing_staging_file_cannot_be_silently_verified(tmp_path,monkeypatch):
    env=environment(tmp_path)
    def run(args,**kwargs):
        if args[1]=='check':
            listed=Path(args[args.index('--files-from')+1]).read_text().strip()
            (Path(args[2])/listed).unlink()
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(offsite.subprocess,'run',run)
    assert offsite.run(env)==1
    assert json.loads((tmp_path/'status.json').read_text())['error']=='offsite_source_changed'
