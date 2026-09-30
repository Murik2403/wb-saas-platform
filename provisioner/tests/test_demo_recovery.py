from unittest.mock import Mock
import pytest
import app
from demo_recovery import DemoRecoveryJournal


def prepare(tmp_path,monkeypatch,phase):
    monkeypatch.setattr(app,'BACKUP_MOUNT',tmp_path)
    monkeypatch.setattr(app,'BACKUP_ENCRYPTION_KEY','test-only')
    journal=DemoRecoveryJournal(tmp_path/'demo')
    journal.write(phase,'original-id','snapshot.tar.gz.enc' if phase=='mutating' else None)
    original=Mock(id='original-id',name='wb-tenant-demo-refresh-rollback-1')
    original.attrs={'State':{'Status':'exited'}}
    candidate=Mock(id='candidate-id')
    seed=Mock()
    client=Mock()
    client.containers.get.side_effect=lambda name: {'original-id':original,'wb-tenant-demo':candidate,'wb-tenant-demo-seed':seed}[name]
    monkeypatch.setattr(app,'_client',lambda:client)
    monkeypatch.setattr(app,'_verify_demo_backup',Mock(return_value='.snapshot.verified.tar.gz'))
    monkeypatch.setattr(app,'_restore_demo_backup',Mock())
    monkeypatch.setattr(app,'_wait_demo_health',Mock())
    return journal,original,candidate,seed,client


def test_crash_before_seed_restarts_unchanged_original(tmp_path,monkeypatch):
    journal,original,candidate,seed,client=prepare(tmp_path,monkeypatch,'stopping')
    app._recover_demo_reseed()
    original.start.assert_called_once()
    app._restore_demo_backup.assert_not_called()
    candidate.stop.assert_not_called()
    assert journal.read() is None


def test_crash_during_seed_or_replacement_restores_original_image_and_verified_db(tmp_path,monkeypatch):
    journal,original,candidate,seed,client=prepare(tmp_path,monkeypatch,'mutating')
    app._recover_demo_reseed()
    seed.stop.assert_called_once()
    seed.remove.assert_called_once_with(force=True)
    candidate.stop.assert_called_once()
    candidate.remove.assert_called_once_with(force=True)
    app._verify_demo_backup.assert_called_once_with('snapshot.tar.gz.enc','demo')
    app._restore_demo_backup.assert_called_once_with('wb-tenant-data-demo','demo','.snapshot.verified.tar.gz',client)
    original.rename.assert_called_once_with('wb-tenant-demo')
    original.start.assert_called_once()
    app._wait_demo_health.assert_called_once_with(client)
    assert journal.read() is None


def test_failed_crash_restore_retains_marker_and_does_not_start_original(tmp_path,monkeypatch):
    journal,original,candidate,seed,client=prepare(tmp_path,monkeypatch,'mutating')
    app._restore_demo_backup.side_effect=RuntimeError('failed integrity')
    with pytest.raises(RuntimeError):
        app._recover_demo_reseed()
    assert journal.read()['phase']=='mutating'
    original.start.assert_not_called()


def test_startup_recovers_before_scheduling(tmp_path,monkeypatch):
    journal,original,candidate,seed,client=prepare(tmp_path,monkeypatch,'mutating')
    monkeypatch.setenv('WB_SAAS_DEMO_DAILY_REFRESH','0')
    app.start_daily_demo()
    assert journal.read() is None
    assert app._DEMO_DAILY.enabled is False
