from __future__ import annotations

import sqlite3
import tarfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

import app


class _Client:
    def __init__(self):
        self.old = Mock()
        self.old.id = 'original-demo-id'
        self.old.name = 'wb-tenant-demo'
        self.old.attrs = {"State": {"Status": "running"}}
        self.old_removed = False
        self.old_renamed = False
        self.old.rename.side_effect = self._rename_old
        self.old.remove.side_effect = self._remove_old
        self.containers = Mock()
        self.containers.get.side_effect = self._get

    def _remove_old(self, *_args, **_kwargs):
        self.old_removed = True

    def _rename_old(self,name):
        self.old_renamed = name != 'wb-tenant-demo'
        self.old.name = name

    def _get(self, name):
        if name == self.old.id and not self.old_removed:
            return self.old
        if name == "wb-tenant-demo" and not self.old_removed and not self.old_renamed:
            return self.old
        raise RuntimeError("not found")


def _prepared(monkeypatch, tmp_path):
    events = []
    client = _Client()
    monkeypatch.setattr(app, "BACKUP_ENCRYPTION_KEY", "test-only-backup-secret-that-is-long-enough")
    monkeypatch.setattr(app, "_client", lambda: client)
    monkeypatch.setattr(app, "_volume", lambda _client, _slug: SimpleNamespace(name="wb-tenant-data-demo"))
    monkeypatch.setattr(app, "BACKUP_MOUNT", tmp_path)
    monkeypatch.setattr(app, "_backup_volume", lambda *_args: events.append("backup") or {"archive": "snapshot.tar.gz.enc"})
    monkeypatch.setattr(app, "_verify_demo_backup", lambda *_args: events.append("verify") or ".snapshot.verified.tar.gz")
    monkeypatch.setattr(app, "_restore_demo_backup", lambda *_args: events.append("restore"))
    def run(*_args, **_kwargs):
        events.append("seed")

    client.containers.run.side_effect = run
    monkeypatch.setattr(app, "_validate_demo_seed", lambda *_args: events.append("validate") or {"ok": True})
    monkeypatch.setattr(app, "_start_demo_container", lambda *_args: events.append("start-new"))
    monkeypatch.setattr(app, "_wait_demo_health", lambda *_args: events.append("healthy"))
    return client, events


def test_demo_reseed_verifies_backup_before_running_seed(monkeypatch, tmp_path):
    client, events = _prepared(monkeypatch, tmp_path)

    result = app.reseed_demo()

    assert result == {"container": "wb-tenant-demo"}
    assert events == ["backup", "verify", "seed", "validate", "start-new", "healthy"]
    client.old.stop.assert_called_once()
    client.old.remove.assert_called_once_with(force=True)
    client.containers.run.assert_called_once()


def test_demo_reseed_missing_backup_key_fails_before_docker_or_stop(monkeypatch):
    client = _Client()
    monkeypatch.setattr(app, "BACKUP_ENCRYPTION_KEY", "")
    monkeypatch.setattr(app, "_client", lambda: client)

    with pytest.raises(HTTPException) as raised:
        app.reseed_demo()

    assert raised.value.status_code == 503
    assert "key" not in raised.value.detail.lower()
    client.old.stop.assert_not_called()
    client.containers.get.assert_not_called()


@pytest.mark.parametrize("failure", ["seed", "validation"])
def test_demo_reseed_failure_restores_backup_and_keeps_old_container(monkeypatch, tmp_path, failure):
    client, events = _prepared(monkeypatch, tmp_path)
    if failure == "seed":
        client.containers.run.side_effect = lambda *_args, **_kwargs: (events.append("seed"), (_ for _ in ()).throw(RuntimeError("seed details must not leak")))[1]
    else:
        monkeypatch.setattr(app, "_validate_demo_seed", lambda *_args: (_ for _ in ()).throw(ValueError("validation details must not leak")))

    with pytest.raises(HTTPException) as raised:
        app.reseed_demo()

    assert raised.value.status_code == 502
    assert "details must not leak" not in raised.value.detail
    assert events[:3] == ["backup", "verify", "seed"]
    assert events[-1] == "restore"
    client.old.start.assert_called_once()
    client.old.remove.assert_not_called()


def test_demo_reseed_recreate_failure_restores_snapshot_and_recreates_old(monkeypatch, tmp_path):
    client, events = _prepared(monkeypatch, tmp_path)
    starts = iter([RuntimeError("create details must not leak"), None])

    def start(_client, _slug):
        events.append("start-new")
        error = next(starts)
        if error:
            raise error

    monkeypatch.setattr(app, "_start_demo_container", start)

    with pytest.raises(HTTPException) as raised:
        app.reseed_demo()

    assert raised.value.status_code == 502
    assert "details must not leak" not in raised.value.detail
    assert events == ["backup", "verify", "seed", "validate", "start-new", "restore"]
    client.old.remove.assert_not_called()
    client.old.start.assert_called_once()
    assert client.old.rename.call_args.args == ('wb-tenant-demo',)


def test_unhealthy_candidate_restores_original_before_backup_cleanup(monkeypatch, tmp_path):
    client, events = _prepared(monkeypatch, tmp_path)
    marker = tmp_path/'demo'/'.snapshot.verified.tar.gz'
    marker.parent.mkdir(exist_ok=True)
    marker.write_bytes(b'verified')
    def unhealthy(_client):
        assert marker.exists()
        events.append('unhealthy')
        raise RuntimeError('health failed')
    monkeypatch.setattr(app, '_wait_demo_health', unhealthy)
    with pytest.raises(HTTPException):
        app.reseed_demo()
    assert events[-2:] == ['unhealthy','restore']
    client.old.remove.assert_not_called()
    client.old.start.assert_called_once()
    assert client.old.rename.call_args.args == ('wb-tenant-demo',)


def test_sqlite_helpers_override_tenant_entrypoint():
    client = Mock()
    client.containers.run.return_value = b'{"ok":true}'
    assert app._validate_demo_seed('wb-tenant-data-demo', client)['ok']
    args = client.containers.run.call_args.kwargs
    assert args['entrypoint'] == ['python3'] and args['command'][0] == '-c'
    client.containers.run.side_effect = [b'', b'', b'ok']
    app._restore_demo_backup('wb-tenant-data-demo','demo','.verified.tar.gz',client)
    args = client.containers.run.call_args.kwargs
    assert args['entrypoint'] == ['python3'] and args['command'][0] == '-c'


@pytest.mark.parametrize('stage',['seed','health'])
def test_process_interruption_journal_recovers_original_on_restart(monkeypatch,tmp_path,stage):
    from demo_recovery import DemoRecoveryJournal
    client,events = _prepared(monkeypatch,tmp_path)
    if stage=='seed':
        client.containers.run.side_effect=SystemExit('simulated process interruption')
    else:
        monkeypatch.setattr(app,'_wait_demo_health',Mock(side_effect=SystemExit('simulated process interruption')))
    with pytest.raises(SystemExit):
        app.reseed_demo()
    journal=DemoRecoveryJournal(tmp_path/'demo')
    assert journal.read()['phase']=='mutating'
    client.old.remove.assert_not_called()
    monkeypatch.setattr(app,'_wait_demo_health',Mock())
    app._recover_demo_reseed(client)
    assert journal.read() is None
    assert client.old.name=='wb-tenant-demo'
    client.old.start.assert_called_once()


def test_verified_demo_backup_requires_valid_sqlite_database(tmp_path, monkeypatch):
    backup_dir = tmp_path / "demo"
    backup_dir.mkdir()
    db = tmp_path / "wb_dashboard.sqlite3"
    conn = sqlite3.connect(db)
    try:
        conn.executescript("CREATE TABLE sales(id TEXT); CREATE TABLE procurement_orders(id INTEGER); CREATE TABLE products_catalog(nm_id INTEGER);")
    finally:
        conn.close()
    archive_path = backup_dir / "snapshot.tar.gz.enc"
    tar_path = tmp_path / "source.tar.gz"
    with tarfile.open(tar_path, "w:gz") as archive:
        archive.add(db, arcname="./wb_dashboard.sqlite3")
    monkeypatch.setattr(app, "BACKUP_MOUNT", tmp_path)
    monkeypatch.setattr(app, "BACKUP_ENCRYPTION_KEY", "backup-secret-that-is-longer-than-thirty-two-characters")
    app.encrypt_file(tar_path, archive_path, app.BACKUP_ENCRYPTION_KEY)

    verified = app._verify_demo_backup(archive_path.name, "demo")

    assert (backup_dir / verified).is_file()
    (backup_dir / verified).unlink()
    archive_path.write_bytes(b"not an authenticated archive")
    with pytest.raises(Exception):
        app._verify_demo_backup(archive_path.name, "demo")
