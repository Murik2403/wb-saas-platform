import os
import time
from unittest.mock import Mock

import app


def _old(path, days):
    path.write_bytes(b"x")
    t = time.time() - days * 86400
    os.utime(path, (t, t))


def test_prune_removes_old_legacy_tar_only_when_fresh_enc_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "BACKUP_MOUNT", tmp_path)
    d = tmp_path / "shop"
    d.mkdir()
    _old(d / "a.tar.gz", 30)
    _old(d / ".shop-20261015_010203_123456-0123456789ab.tar.gz.partial", 1)
    assert app._prune_old_backups("shop", 14) == 1  # only the stale partial; no fresh .enc yet
    assert (d / "a.tar.gz").exists()
    (d / "new.tar.gz.enc").write_bytes(b"x")
    assert app._prune_old_backups("shop", 14) == 1
    assert not (d / "a.tar.gz").exists() and (d / "new.tar.gz.enc").exists()


def test_prune_keeps_recent_legacy_and_old_enc_is_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "BACKUP_MOUNT", tmp_path)
    d = tmp_path / "shop"
    d.mkdir()
    _old(d / "recent.tar.gz", 2)
    _old(d / "old.tar.gz.enc", 30)
    (d / "new.tar.gz.enc").write_bytes(b"x")
    app._prune_old_backups("shop", 14)
    assert (d / "recent.tar.gz").exists() and not (d / "old.tar.gz.enc").exists()


def test_verify_internal_secret_constant_time_compare(monkeypatch):
    container = Mock()
    container.attrs = {"Config": {"Env": ["WB_SAAS_INTERNAL_API_SECRET=right"]}}
    client = Mock()
    client.containers.get.return_value = container
    monkeypatch.setattr(app, "_client", lambda: client)
    assert app.verify_internal_secret("shop", app.CredentialCheckRequest(expected_secret="right")) == {"valid": True}
    assert app.verify_internal_secret("shop", app.CredentialCheckRequest(expected_secret="wrong")) == {"valid": False}
