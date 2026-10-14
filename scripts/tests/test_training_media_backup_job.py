"""Daily course copies/retention with synthetic media and synthetic keys only."""
import hashlib
import json
import os
from pathlib import Path
import time

import pytest
from scripts import training_media_backup_job as job
from scripts.tests.test_training_media_backup import KEY, build_pack


def environment(tmp_path):
    source = build_pack(tmp_path / "media")
    return {"WB_SAAS_TRAINING_MEDIA_SOURCE_DIR": str(source), "WB_SAAS_BACKUP_DIR": str(tmp_path / "backups"),
            "WB_SAAS_BACKUP_ENCRYPTION_KEY": KEY, "WB_SAAS_BACKUP_KEEP_DAYS": "14"}


def make_old(directory, packed, *, stamp="20000101T000000Z"):
    current = directory / packed["archive"]
    old = directory / f"training-media-{packed['manifest_sha256'][:12]}-{stamp}-012345.tar.gz.enc"
    current.rename(old)
    os.utime(old, (1000, 1000))
    return old


def test_daily_force_refreshes_unchanged_release_and_preserves_unknown_or_wrong_key(tmp_path, capsys):
    env = environment(tmp_path); directory = Path(env["WB_SAAS_BACKUP_DIR"]) / "training-media"
    source = Path(env["WB_SAAS_TRAINING_MEDIA_SOURCE_DIR"])
    old = make_old(directory, job.media.pack(source, directory, KEY))
    unknown = directory / "unknown.tar.gz.enc"; unknown.write_bytes(b"must remain")
    broken = directory / f"training-media-{'f'*12}-20000101T000000Z-abcdef.tar.gz.enc"; broken.write_bytes(b"not authentic")
    os.utime(broken, (1000, 1000))
    other = make_old(directory, job.media.pack(source, directory, "other-key-" + "z" * 40), stamp="20000102T000000Z")
    own_hash = job.media.sha256_of(other)
    before = {path.name: (path.stat().st_size, path.stat().st_mtime_ns, job.media.sha256_of(path)) for path in source.iterdir()}
    assert job.run(env) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["verified_after_write"] and result["files"] == 32 and result["lessons"] == 16
    assert result["expired_owned_archives_pruned"] == 1 and not old.exists()
    assert result["expired_unverified_archives_preserved"] == 2
    fresh = directory / result["archive"]
    assert fresh.exists() and time.time() - fresh.stat().st_mtime < 60
    assert unknown.read_bytes() == b"must remain" and job.media.sha256_of(other) == own_hash
    assert broken.read_bytes() == b"not authentic"
    assert before == {path.name: (path.stat().st_size, path.stat().st_mtime_ns, job.media.sha256_of(path)) for path in source.iterdir()}
    assert job.run(env) == 0  # exactly one fresh new archive per invocation
    result2 = json.loads(capsys.readouterr().out)
    assert result2["archive"] != result["archive"] and fresh.exists()


def test_failed_copy_does_not_prune_and_alert_payload_never_contains_details(tmp_path, monkeypatch, capsys):
    env = environment(tmp_path); directory = Path(env["WB_SAAS_BACKUP_DIR"]) / "training-media"
    old = make_old(directory, job.media.pack(Path(env["WB_SAAS_TRAINING_MEDIA_SOURCE_DIR"]), directory, KEY))
    checksum = job.media.sha256_of(old); alerts = []
    def fail(*args, **kwargs):raise OSError("synthetic-secret-must-not-be-logged")
    monkeypatch.setattr(job.media, "pack", fail)
    monkeypatch.setattr(job, "alert", lambda event, incoming: alerts.append(event) or "accepted")
    assert job.run(env) == 1
    printed = capsys.readouterr().out
    assert "synthetic-secret-must-not-be-logged" not in printed and KEY not in printed
    assert json.loads(printed)["error"] == "local_backup_failed"
    assert job.media.sha256_of(old) == checksum
    assert alerts[0]["code"] == "local_backup_failed" and set(alerts[0]) == {"kind", "code", "at"}


def test_rotated_keyring_can_authenticate_an_expired_owned_archive(tmp_path, capsys):
    env = environment(tmp_path); directory = Path(env["WB_SAAS_BACKUP_DIR"]) / "training-media"
    old = make_old(directory, job.media.pack(Path(env["WB_SAAS_TRAINING_MEDIA_SOURCE_DIR"]), directory, KEY, key_id="old-key"))
    env.update(WB_SAAS_BACKUP_ENCRYPTION_KEY="new-key-" + "n" * 40, WB_SAAS_BACKUP_ENCRYPTION_KEY_ID="new-key",
               WB_SAAS_BACKUP_ENCRYPTION_KEYRING=json.dumps({"old-key": KEY}))
    assert job.run(env) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["expired_owned_archives_pruned"] == 1 and not old.exists()
    fresh = directory / result["archive"]
    with __import__("tempfile").TemporaryDirectory() as scratch:
        checked, _ = job.media.read_archive(fresh, env["WB_SAAS_BACKUP_ENCRYPTION_KEY"], Path(scratch), key_id="new-key")
    assert checked["lessons"] == 16


@pytest.mark.parametrize("keep", ["0", "1000000", "-1", "2.5", "garbage"])
def test_invalid_retention_refuses_before_any_source_pack(tmp_path, keep, monkeypatch, capsys):
    env = environment(tmp_path); env["WB_SAAS_BACKUP_KEEP_DAYS"] = keep
    monkeypatch.setattr(job.media, "pack", lambda *a, **kw: pytest.fail("pack must not run"))
    assert job.run(env) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "local_backup_failed"


def test_recent_mtime_or_future_name_prevents_expired_cleanup(tmp_path):
    source = build_pack(tmp_path / "source"); directory = tmp_path / "backups"
    old = make_old(directory, job.media.pack(source, directory, KEY))
    os.utime(old, None)
    assert job.prune_verified(directory, directory / "not-current", KEY, "legacy", {}, time.time() - 14 * 86400)["expired_owned_archives_pruned"] == 0
    assert old.exists()


def test_valid_archive_from_another_tool_is_not_inferred_owned_from_name_only(tmp_path):
    from scripts.tests.test_training_media_backup import approved_archive_members, encrypted_tar
    source = build_pack(tmp_path / "source"); directory = tmp_path / "backups"; directory.mkdir()
    inventory, members = approved_archive_members(source)
    inventory.update(tool="another-tool", format=1)
    members[0] = (job.media.INVENTORY_NAME, json.dumps(inventory).encode())
    crafted = encrypted_tar(tmp_path, members)
    unknown = directory / f"training-media-{inventory['manifest_sha256'][:12]}-20000101T000000Z-abcdef.tar.gz.enc"
    crafted.rename(unknown); os.utime(unknown, (1000, 1000))
    checksum = job.media.sha256_of(unknown)
    result = job.prune_verified(directory, directory / "new-copy", KEY, "legacy", {}, time.time() - 14 * 86400)
    assert result == {"expired_owned_archives_pruned": 0, "expired_unverified_archives_preserved": 1}
    assert job.media.sha256_of(unknown) == checksum
