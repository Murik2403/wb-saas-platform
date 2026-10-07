import importlib.util,json,sys
from pathlib import Path
from types import SimpleNamespace

def module(monkeypatch):
    if sys.platform=='win32':monkeypatch.setitem(sys.modules,'fcntl',SimpleNamespace(LOCK_EX=1,LOCK_NB=2,flock=lambda *args:None))
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    spec=importlib.util.spec_from_file_location('backup_cron_test',Path(__file__).resolve().parents[1]/'run_backup_cron.py')
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

def test_scheduler_copies_encrypted_history_but_never_enables_remote_deletion(tmp_path,monkeypatch):
    cron=module(monkeypatch)
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
