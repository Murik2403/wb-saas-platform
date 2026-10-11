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


@pytest.mark.parametrize('magic', [b'WBSAASBK1', b'WBSAASBK2'])
def test_dry_run_keeps_newest_even_when_all_backups_are_old_and_never_recurses(tmp_path,magic):
    old = fixture_file(tmp_path, "old.tar.gz.enc", 30, magic+b'synthetic')
    newest = fixture_file(tmp_path, "newest.tar.gz.enc", 20, magic+b'synthetic')
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
    assert len(receipt["events"]) == 1
    assert receipt["events"][0]["state"] == "pending"


def test_legacy_namespace_floor_replacement_requires_lineage_proof(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    source = legacy / "only.sqlite3"
    source.write_bytes(b"SQLite format 3\x00" + b"x" * 100)
    os.utime(source, (NOW - 30 * 86400,) * 2)
    target = fixture_file(tmp_path, "current.tar.gz.enc", 1)
    default = module.plan_local(tmp_path, now=NOW)
    assert not default["candidates"]
    manifest = module.plan_local(tmp_path, now=NOW, namespace_replacements={"legacy": "synthetic"})
    assert [row["path"] for row in manifest["candidates"]] == ["legacy/only.sqlite3"]
    assert [row["namespace"] for row in manifest["retained"]] == ["synthetic"]
    evidence = proof(manifest)
    with pytest.raises(ValueError, match="unverified_namespace_replacement"):
        module.apply_local(manifest, acknowledgment=manifest["manifest_sha256"], proof=evidence,
                           receipt_path=tmp_path / "receipt.json", now=NOW)
    assert source.exists()
    evidence["verified_namespace_replacements"] = {"legacy": "synthetic"}
    report = module.apply_local(manifest, acknowledgment=manifest["manifest_sha256"], proof=evidence,
                                receipt_path=tmp_path / "receipt.json", now=NOW)
    assert report["deleted_count"] == 1 and not source.exists() and target.exists()
    assert legacy.is_dir()  # Exact-file operation never recursively removes it.


def test_remote_namespace_replacement_pins_real_target_not_fake_legacy_floor():
    source = remote_row("legacy/old.sqlite3", 30)
    source["format"] = "SQLite3"
    target = remote_row("offsite-v2/active/new.tar.gz.enc", 1, "2" * 64)
    manifest = module.plan_remote([source, target], now=NOW, namespace_replacements={"legacy": "active"})
    assert manifest["namespace_replacements"] == {"legacy": "active"}
    assert manifest["retained"][0]["path"] == target["path"]
    assert manifest["retained"][0]["namespace"] == "active"
    assert manifest["candidates"][0]["path"] == source["path"]
    evidence = proof(manifest)
    evidence["verified_namespace_replacements"] = {"legacy": "active"}
    module._authorize(manifest, manifest["manifest_sha256"], evidence, NOW)
    evidence["recovery_sha256_by_namespace"] = {"legacy": target["sha256"]}
    with pytest.raises(ValueError, match="recovery_proof_scope_mismatch"):
        module._authorize(manifest, manifest["manifest_sha256"], evidence, NOW)


@pytest.mark.parametrize("mapping", [
    {"legacy": "missing"}, {"legacy": "legacy"},
    {"legacy": "active", "active": "other"}, {"legacy": "active", "active": "legacy"},
    {"../legacy": "active"}, {"legacy": "active/child"}, {"legacy": "quarantine"},
    {"erasure-ledger": "active"},
])
def test_namespace_replacement_rejects_missing_chain_cycle_or_unsafe_target(mapping):
    source = remote_row("legacy/old.sqlite3", 30)
    source["format"] = "SQLite3"
    target = remote_row("active/new.tar.gz.enc", 1, "2" * 64)
    with pytest.raises(ValueError):
        module.plan_remote([source, target], now=NOW, namespace_replacements=mapping)


def test_namespace_replacement_requires_encrypted_floor_not_plaintext():
    source = remote_row("legacy/old.sqlite3", 30)
    source["format"] = "SQLite3"
    target = remote_row("active/new.sqlite3", 1, "2" * 64)
    target["format"] = "SQLite3"
    with pytest.raises(ValueError, match="replacement_requires_retained_encrypted_recovery"):
        module.plan_remote([source, target], now=NOW, namespace_replacements={"legacy": "active"})


def test_receipt_rewrite_failure_preserves_previous_durable_evidence(tmp_path, monkeypatch):
    path = tmp_path / "receipt.json"
    original = {"state": "started", "events": []}
    module._private_write(path, original, exclusive=True)
    def refuse_replace(*args):
        raise OSError("synthetic receipt failure")
    monkeypatch.setattr(module.os, "replace", refuse_replace)
    with pytest.raises(OSError):
        module._private_write(path, {"state": "pending"})
    assert json.loads(path.read_text()) == original
    assert list(tmp_path.iterdir()) == [path]


def test_copied_old_cipher_with_fresh_mtime_remains_expired(tmp_path):
    copied = fixture_file(tmp_path, "copied-old.tar.gz.enc", 0, b"WBSAASBK1" + b"old" * 50)
    fresh = fixture_file(tmp_path, "fresh.tar.gz.enc", 1, b"WBSAASBK1" + b"new" * 50)
    digest = hashlib.sha256(copied.read_bytes()).hexdigest()
    provenance = {digest: int((NOW - 30 * 86400) * 1e9)}
    default = module.plan_local(tmp_path, now=NOW)
    assert not default["candidates"]
    manifest = module.plan_local(tmp_path, now=NOW, creation_by_sha256=provenance)
    assert manifest["creation_by_sha256"] == provenance
    row = manifest["candidates"][0]
    assert row["path"] == "synthetic/copied-old.tar.gz.enc"
    assert row["mtime_ns"] == int(NOW * 1e9)
    assert row["created_ns"] == provenance[digest]
    assert row["creation_basis"] == "sha256_provenance"
    assert manifest["retained"][0]["path"] == "synthetic/fresh.tar.gz.enc"
    apply(manifest, tmp_path)
    assert not copied.exists() and fresh.exists()


def test_remote_wrapped_old_cipher_uses_manifest_bound_original_age(tmp_path):
    old = remote_row("synthetic/copied.tar.gz.enc", 0)
    fresh = remote_row("synthetic/fresh.tar.gz.enc", 1, "2" * 64)
    manifest = module.plan_remote([old, fresh], now=NOW, creation_by_sha256={old["sha256"]: int((NOW - 30 * 86400) * 1e9)})
    deleted = []
    module.apply_remote(manifest, acknowledgment=manifest["manifest_sha256"], proof=proof(manifest),
                        receipt_path=tmp_path / "receipt.json", read_metadata=lambda path: old if path == old["path"] else fresh,
                        delete_file=deleted.append, now=NOW)
    assert deleted == [old["path"]]


@pytest.mark.parametrize("catalog", [
    {"not-a-sha": 1}, {"1" * 64: True}, {"1" * 64: 1.5},
    {"1" * 64: 0}, {"1" * 64: -1}, {"1" * 64: int((NOW + 1) * 1e9)},
    [("1" * 64, 1)],
])
def test_creation_provenance_rejects_invalid_hash_timestamp_or_future(catalog, tmp_path):
    with pytest.raises(ValueError, match="invalid_creation_provenance"):
        module.plan_local(tmp_path, now=NOW, creation_by_sha256=catalog)


def test_changed_cipher_is_not_hidden_by_provenance_mapping(tmp_path):
    old = fixture_file(tmp_path, "copied.tar.gz.enc", 0, b"WBSAASBK1" + b"old" * 50)
    fixture_file(tmp_path, "fresh.tar.gz.enc", 1, b"WBSAASBK1" + b"new" * 50)
    digest = hashlib.sha256(old.read_bytes()).hexdigest()
    manifest = module.plan_local(tmp_path, now=NOW, creation_by_sha256={digest: int((NOW - 30 * 86400) * 1e9)})
    stamp = old.stat().st_mtime_ns
    old.write_bytes(b"WBSAASBK1" + b"bad" * 50)
    os.utime(old, ns=(stamp, stamp))
    with pytest.raises(ValueError, match="target_changed"):
        apply(manifest, tmp_path)
    assert old.exists()


def test_untrusted_remote_original_timestamp_is_not_silently_used():
    old = remote_row("synthetic/old.tar.gz.enc", 0)
    old["original_created_ns"] = int((NOW - 30 * 86400) * 1e9)
    fresh = remote_row("synthetic/fresh.tar.gz.enc", 1, "2" * 64)
    manifest = module.plan_remote([old, fresh], now=NOW)
    assert not manifest["candidates"]
    assert manifest["retained"][0]["created_ns"] == old["mtime_ns"]
    assert manifest["retained"][0]["creation_basis"] == "mtime_fallback"
