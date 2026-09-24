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
        self.old.attrs = {"State": {"Status": "running"}}
        self.old_removed = False
        self.old.remove.side_effect = self._remove_old
        self.containers = Mock()
        self.containers.get.side_effect = self._get

    def _remove_old(self, *_args, **_kwargs):
        self.old_removed = True

    def _get(self, name):
        if name == "wb-tenant-demo" and not self.old_removed:
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
    return client, events


def test_demo_reseed_verifies_backup_before_running_seed(monkeypatch, tmp_path):
    client, events = _prepared(monkeypatch, tmp_path)

    result = app.reseed_demo()

    assert result == {"container": "wb-tenant-demo"}
    assert events == ["backup", "verify", "seed", "validate", "start-new"]
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
    assert events == ["backup", "verify", "seed", "validate", "start-new", "restore", "start-new"]
    client.old.remove.assert_called_once_with(force=True)


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
