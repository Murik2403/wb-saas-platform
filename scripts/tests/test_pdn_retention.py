import copy
import hashlib
import json
import os
from pathlib import Path
import pytest

from scripts import pdn_retention as module

NOW = 1791417600.0


def fixture_file(root, name, age, data=b"WBSAASBK1" + b"synthetic" * 10):
    path = root / "synthetic" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (NOW - age * 86400,) * 2)
    return path


def proof(manifest):
    return {"manifest_sha256": manifest["manifest_sha256"], "verified_at": NOW,
            "restore_ok": True, "erasure_guard_ok": True, "independent_copy": True,
            "recovery_sha256_by_namespace": {row["namespace"]: row["sha256"] for row in manifest["retained"]},
            "versions_checked": True, "object_lock_checked": True, "legal_holds_checked": True}


def apply(manifest, tmp_path, **kwargs):
    return module.apply_local(manifest, acknowledgment=manifest["manifest_sha256"], proof=proof(manifest),
                              receipt_path=tmp_path / "receipt.json", now=NOW, **kwargs)


def test_dry_run_keeps_newest_even_when_all_backups_are_old_and_never_recurses(tmp_path):
    old = fixture_file(tmp_path, "old.tar.gz.enc", 30)
    newest = fixture_file(tmp_path, "newest.tar.gz.enc", 20)
    lock = fixture_file(tmp_path, "cleanup.lock", 90, b"state")
    nested = tmp_path / "synthetic" / "unexpected"
    nested.mkdir()
    (nested / "private.sqlite3").write_bytes(b"SQLite format 3\x00" + b"x" * 50)
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    (quarantine / "old.tar.gz.enc").write_bytes(b"WBSAASBK1" + b"x" * 50)
    manifest = module.plan_local(tmp_path, now=NOW)
    assert [row["path"] for row in manifest["candidates"]] == ["synthetic/old.tar.gz.enc"]
    assert manifest["retained"][0]["path"] == "synthetic/newest.tar.gz.enc"
    assert old.exists() and newest.exists() and lock.exists()
    assert len(manifest["excluded"]) == 3


def test_single_old_recovery_is_never_candidate(tmp_path):
    fixture_file(tmp_path, "only.sqlite3", 60, b"SQLite format 3\x00" + b"x" * 50)
    manifest = module.plan_local(tmp_path, now=NOW)
    assert not manifest["candidates"]
    assert len(manifest["retained"]) == 1


def test_legal_holds_exclude_exact_path_or_whole_namespace(tmp_path):
    fixture_file(tmp_path, "old.tar.gz.enc", 30)
    fixture_file(tmp_path, "new.tar.gz.enc", 1)
    for hold in ("synthetic/old.tar.gz.enc", "synthetic"):
        manifest = module.plan_local(tmp_path, now=NOW, legal_holds=[hold])
        assert not manifest["candidates"]
        assert any(row["reason"] == "legal_hold" for row in manifest["excluded"])


def test_apply_exact_files_with_audit_and_repeat_refused(tmp_path):
    old = fixture_file(tmp_path, "old.tar.gz.enc", 30)
    newest = fixture_file(tmp_path, "new.tar.gz.enc", 1)
    digest = hashlib.sha256(old.read_bytes()).hexdigest()
    sidecar = fixture_file(tmp_path, "old.tar.gz.enc.sha256", 30, (digest + "\n").encode())
    manifest = module.plan_local(tmp_path, now=NOW)
    report = apply(manifest, tmp_path)
    assert report["state"] == "completed" and report["deleted_count"] == 2
    assert not old.exists() and not sidecar.exists() and newest.exists()
    assert len(report["events"]) == 2
    assert json.loads((tmp_path / "receipt.json").read_text())["manifest_sha256"] == manifest["manifest_sha256"]
    with pytest.raises((FileNotFoundError, FileExistsError, ValueError)):
        apply(manifest, tmp_path)


@pytest.mark.parametrize("change", ["size", "content_same_size", "mtime", "retained"])
def test_changed_target_or_recovery_aborts_before_any_delete(tmp_path, change):
    old = fixture_file(tmp_path, "old.tar.gz.enc", 30)
    newest = fixture_file(tmp_path, "new.tar.gz.enc", 1)
    manifest = module.plan_local(tmp_path, now=NOW)
    if change == "size":
        old.write_bytes(old.read_bytes() + b"changed")
    elif change == "content_same_size":
        stamp = old.stat().st_mtime_ns
        old.write_bytes(b"WBSAASBK1" + b"y" * (old.stat().st_size - 9))
        os.utime(old, ns=(stamp, stamp))
    elif change == "mtime":
        os.utime(old, (NOW, NOW))
    else:
        newest.write_bytes(b"broken")
    with pytest.raises(ValueError, match="target_changed"):
        apply(manifest, tmp_path)
    assert old.exists() and not (tmp_path / "receipt.json").exists()


@pytest.mark.parametrize("missing", ["restore_ok", "erasure_guard_ok", "independent_copy", "scope", "fresh", "manifest"])
def test_requires_bound_fresh_independent_restore_proof(tmp_path, missing):
    old = fixture_file(tmp_path, "old.tar.gz.enc", 30)
    fixture_file(tmp_path, "new.tar.gz.enc", 1)
    manifest = module.plan_local(tmp_path, now=NOW)
    evidence = proof(manifest)
    if missing == "scope":
        evidence["recovery_sha256_by_namespace"] = {}
    elif missing == "fresh":
        evidence["verified_at"] -= 86401
    elif missing == "manifest":
        evidence["manifest_sha256"] = "0" * 64
    else:
        evidence[missing] = False
    with pytest.raises(ValueError):
        module.apply_local(manifest, acknowledgment=manifest["manifest_sha256"], proof=evidence,
                           receipt_path=tmp_path / "receipt.json", now=NOW)
    assert old.exists()


def test_symlink_ancestor_and_candidate_rejected(tmp_path):
    root = tmp_path / "real"
    root.mkdir()
    fixture_file(root, "old.tar.gz.enc", 30)
    fixture_file(root, "new.tar.gz.enc", 1)
    link = tmp_path / "link"
    try:
        link.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("Host cannot create symlinks")
    with pytest.raises(ValueError, match="unsafe_root"):
        module.plan_local(link, now=NOW)
    (root / "synthetic" / "linked.sqlite3").symlink_to(root / "synthetic" / "old.tar.gz.enc")
    manifest = module.plan_local(root, now=NOW)
    assert all(row["path"] != "synthetic/linked.sqlite3" for row in manifest["candidates"])


def remote_row(path, age, digest="1" * 64):
    return {"path": path, "size": 200, "mtime_ns": int((NOW - age * 86400) * 1e9),
            "sha256": digest, "format": "MHOFFSITE2", "version_id": "synthetic-version"}


def test_remote_adapter_only_deletes_exact_verified_candidate(tmp_path):
    rows = [remote_row("offsite-v2/synthetic/old.tar.gz.enc", 30),
            remote_row("offsite-v2/synthetic/new.tar.gz.enc", 1, "2" * 64)]
    manifest = module.plan_remote(rows, now=NOW)
    reads, deletes = [], []
    def read(path):
        reads.append(path)
        return next(row for row in rows if row["path"] == path)
    result = module.apply_remote(manifest, acknowledgment=manifest["manifest_sha256"], proof=proof(manifest),
                                 receipt_path=tmp_path / "receipt.json", read_metadata=read,
                                 delete_file=deletes.append, now=NOW)
    assert deletes == [rows[0]["path"]]
    assert reads.count(rows[0]["path"]) == 2
    assert result["deleted_count"] == 1


@pytest.mark.parametrize("path", ["../synthetic/old.tar.gz.enc", "a//b", "offsite-v2/x/../b", "arbitrary/x/b.tar.gz.enc", "/x/b", "x\\b", "quarantine/x.tar.gz.enc", "x/erasure-ledger.sqlite3"])
def test_remote_paths_cannot_escape_expected_layout(path):
    with pytest.raises(ValueError, match="unsafe_path"):
        module.plan_remote([remote_row(path, 30)], now=NOW)


def test_remote_metadata_changes_or_unchecked_versions_never_delete(tmp_path):
    rows = [remote_row("synthetic/old.tar.gz.enc", 30), remote_row("synthetic/new.tar.gz.enc", 1, "2" * 64)]
    manifest = module.plan_remote(rows, now=NOW)
    deletes = []
    changed = copy.deepcopy(rows)
    changed[0]["sha256"] = "3" * 64
    with pytest.raises(ValueError, match="target_changed"):
        module.apply_remote(manifest, acknowledgment=manifest["manifest_sha256"], proof=proof(manifest),
                            receipt_path=tmp_path / "receipt.json", read_metadata=lambda path: next(row for row in changed if row["path"] == path), delete_file=deletes.append, now=NOW)
    bad_proof = proof(manifest)
    bad_proof["versions_checked"] = False
    with pytest.raises(ValueError, match="remote_policy_not_verified"):
        module.apply_remote(manifest, acknowledgment=manifest["manifest_sha256"], proof=bad_proof,
                            receipt_path=tmp_path / "receipt.json", read_metadata=lambda _: rows[0], delete_file=deletes.append, now=NOW)
    assert deletes == []


@pytest.mark.parametrize("days", [6, 3651, True, 14.0])
def test_policy_bounds(days, tmp_path):
    with pytest.raises(ValueError):
        module.plan_local(tmp_path, days=days, now=NOW)


def test_unknown_format_plain_archive_and_wrong_digest_never_candidates(tmp_path):
    fixture_file(tmp_path, "bad.tar.gz.enc", 30, b"not-an-encrypted-backup" * 10)
    fixture_file(tmp_path, "legacy.tar.gz", 30, b"plaintext")
    fixture_file(tmp_path, "old.tar.gz.enc", 30)
    fixture_file(tmp_path, "old.tar.gz.enc.sha256", 30, b"0" * 64)
    fixture_file(tmp_path, "new.tar.gz.enc", 1)
    manifest = module.plan_local(tmp_path, now=NOW)
    assert [row["path"] for row in manifest["candidates"]] == ["synthetic/old.tar.gz.enc"]


def test_remote_verified_sqlite_and_legacy_hash_envelope_are_supported():
    old_sqlite = remote_row("synthetic/old.sqlite3", 30)
    old_sqlite["format"] = "SQLite3"
    old_envelope = remote_row("synthetic/" + "a" * 64 + ".enc", 30)
    newest = remote_row("synthetic/new.tar.gz.enc", 1, "2" * 64)
    manifest = module.plan_remote([old_sqlite, old_envelope, newest], now=NOW)
    assert {row["kind"] for row in manifest["candidates"]} == {"sqlite", "encrypted"}
    assert len(manifest["candidates"]) == 2


def test_local_legacy_hash_envelope_and_unknown_short_enc_distinguished(tmp_path):
    fixture_file(tmp_path, "a" * 64 + ".enc", 30, b"MHOFFSITE2" + b"x" * 50)
    fixture_file(tmp_path, "unknown.enc", 30, b"MHOFFSITE2" + b"x" * 50)
    fixture_file(tmp_path, "new.tar.gz.enc", 1)
    manifest = module.plan_local(tmp_path, now=NOW)
    assert len(manifest["candidates"]) == 1
    assert manifest["candidates"][0]["path"].endswith("a" * 64 + ".enc")


def test_remote_failure_receipt_preserves_exact_confirmed_deletion_count(tmp_path):
    rows = [remote_row("synthetic/old.tar.gz.enc", 30), remote_row("synthetic/new.tar.gz.enc", 1, "2" * 64)]
    manifest = module.plan_remote(rows, now=NOW)
    def failed_delete(path):
        raise OSError("synthetic provider refusal")
    with pytest.raises(OSError):
        module.apply_remote(manifest, acknowledgment=manifest["manifest_sha256"], proof=proof(manifest),
                            receipt_path=tmp_path / "receipt.json", read_metadata=lambda path: next(row for row in rows if row["path"] == path), delete_file=failed_delete, now=NOW)
    receipt = json.loads((tmp_path / "receipt.json").read_text())
    assert receipt["state"] == "failed" and receipt["deleted_count"] == 0
    assert not receipt["events"]
