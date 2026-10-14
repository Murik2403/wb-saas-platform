"""Training media backup/restore on a synthetic 16-lesson pack: no production files, no real key."""
import hashlib
import io
import json
import os
import tarfile

import pytest

import scripts.training_media_backup as tool

KEY = "unit-test-key-" + "k" * 40


def lesson_slugs():
    return tool.catalog_slugs()


def build_pack(root, *, skip=(), captions=True, tamper=None):
    """A manifest the gateway would accept: <slug>.<sha256>.mp4 and .vtt for every catalog lesson."""
    root.mkdir(parents=True, exist_ok=True)
    videos = {}
    for index, slug in enumerate(lesson_slugs()):
        if slug in skip:
            continue
        mp4 = (f"MP4-{slug}-".encode() + bytes(range(256)) * (4 + index))
        digest = hashlib.sha256(mp4).hexdigest()
        (root / f"{slug}.{digest}.mp4").write_bytes(mp4)
        entry = {"filename": f"{slug}.{digest}.mp4", "sha256": digest, "bytes": len(mp4), "narrated": True}
        if captions:
            vtt = f"WEBVTT\n\n00:00.000 --> 00:02.000\n{slug}\n".encode()
            vdigest = hashlib.sha256(vtt).hexdigest()
            (root / f"{slug}.{vdigest}.vtt").write_bytes(vtt)
            entry["captions"] = {"filename": f"{slug}.{vdigest}.vtt", "sha256": vdigest, "bytes": len(vtt)}
        videos[slug] = entry
    (root / "manifest.json").write_text(json.dumps({"version": 1, "approved": True, "narrated": True, "videos": videos}), encoding="utf-8")
    return root


def test_a_complete_pack_verifies_and_reports_all_sixteen_lessons(tmp_path):
    inventory = tool.verify_source(build_pack(tmp_path / "pack"))
    assert inventory["lessons"] == 16 and len(inventory["files"]) == 32 and inventory["unlisted_files_in_source"] == []


@pytest.mark.parametrize("mutation,expected", [
    ("delete_mp4", "missing"), ("corrupt_mp4", "SHA-256 differs"), ("truncate_mp4", "size"), ("drop_lesson", "absent from the manifest"),
    ("drop_captions", "subtitles (VTT) absent"), ("rename", "invalid approved asset name"), ("unapproved", "approved version 1"),
    ("alien_slug", "not an approved lesson slug")])
def test_every_kind_of_damage_to_the_source_is_reported(tmp_path, mutation, expected):
    root = build_pack(tmp_path / "pack", captions=mutation != "drop_captions")
    manifest = json.loads((root / "manifest.json").read_text())
    first = manifest["videos"][lesson_slugs()[0]]
    mp4 = root / first["filename"]
    if mutation == "delete_mp4":
        mp4.unlink()
    elif mutation == "corrupt_mp4":
        data = bytearray(mp4.read_bytes())
        data[10] ^= 1
        mp4.write_bytes(bytes(data))
    elif mutation == "truncate_mp4":
        mp4.write_bytes(mp4.read_bytes()[:-1])
    elif mutation == "drop_lesson":
        del manifest["videos"][lesson_slugs()[3]]
        (root / "manifest.json").write_text(json.dumps(manifest))
    elif mutation == "rename":
        first["filename"] = "lesson-01-today.mp4"
        (root / "manifest.json").write_text(json.dumps(manifest))
    elif mutation == "unapproved":
        manifest["approved"] = False
        (root / "manifest.json").write_text(json.dumps(manifest))
    elif mutation == "alien_slug":
        manifest["videos"]["lesson-99-evil"] = first
        (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(tool.MediaError) as problem:
        tool.verify_source(root)
    assert any(expected in line for line in problem.value.problems), problem.value.problems


def test_pack_writes_one_new_encrypted_archive_and_never_touches_anything_else(tmp_path):
    source = build_pack(tmp_path / "pack")
    backups = tmp_path / "backups"
    backups.mkdir()
    older = {"training-media-000000000000-20200101T000000Z-aaaaaa.tar.gz.enc": b"older", "notes.txt": b"n", "unknown.tar.gz.enc": b"u", ".hidden": b"h"}
    for name, body in older.items():
        (backups / name).write_bytes(body)
    stat_before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in source.iterdir()}
    result = tool.pack(source, backups, KEY)
    assert result["result"] == "created" and result["lessons"] == 16 and result["files"] == 32 and result["verified_after_write"] is True
    archive = backups / result["archive"]
    assert archive.read_bytes().startswith(b"WBSAASBK2")  # the existing encryption, not a new format
    assert archive.name.endswith(".tar.gz.enc") and oct(archive.stat().st_mode & 0o777) in {"0o600", "0o666", "0o644"}
    for name, body in older.items():
        assert (backups / name).read_bytes() == body, f"{name} must stay"
    assert {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in source.iterdir()} == stat_before  # copy-only: source untouched
    assert not [p for p in backups.iterdir() if p.name.endswith((".partial", ".tmp"))]
    assert b"WEBVTT" not in archive.read_bytes() and b"MP4-" not in archive.read_bytes()  # really encrypted


def test_the_same_release_is_not_backed_up_twice_unless_forced(tmp_path):
    source, backups = build_pack(tmp_path / "pack"), tmp_path / "backups"
    first = tool.pack(source, backups, KEY)
    assert tool.pack(source, backups, KEY)["result"] == "already_backed_up"
    forced = tool.pack(source, backups, KEY, force=True)
    assert forced["result"] == "created" and forced["archive"] != first["archive"]
    assert len(list(backups.glob("*.tar.gz.enc"))) == 2


def test_restore_is_byte_identical_and_verified_as_a_gateway_media_root(tmp_path):
    source, backups = build_pack(tmp_path / "pack"), tmp_path / "backups"
    archive = backups / tool.pack(source, backups, KEY)["archive"]
    result = tool.restore(archive, tmp_path / "restored", KEY, read_only=True)
    assert result["files"] == 32 and result["lessons"] == 16
    for path in source.iterdir():
        assert (tmp_path / "restored" / path.name).read_bytes() == path.read_bytes()
    assert tool.verify_source(tmp_path / "restored")["manifest_sha256"] == tool.verify_source(source)["manifest_sha256"]
    assert not (tmp_path / "restored" / tool.INVENTORY_NAME).exists()  # the inventory is backup metadata, not media


def test_wrong_key_and_modified_archive_are_refused_and_leave_no_output(tmp_path):
    source, backups = build_pack(tmp_path / "pack"), tmp_path / "backups"
    archive = backups / tool.pack(source, backups, KEY)["archive"]
    with pytest.raises(tool.MediaError):
        tool.restore(archive, tmp_path / "wrong-key", "another-key-" + "z" * 40)
    modified = tmp_path / "modified.tar.gz.enc"
    raw = bytearray(archive.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    modified.write_bytes(bytes(raw))
    with pytest.raises(tool.MediaError):
        tool.restore(modified, tmp_path / "from-modified", KEY)
    for name in ("wrong-key", "from-modified"):
        assert not (tmp_path / name).exists() or not any((tmp_path / name).iterdir())


def test_restore_never_overwrites_a_non_empty_directory(tmp_path):
    source, backups = build_pack(tmp_path / "pack"), tmp_path / "backups"
    archive = backups / tool.pack(source, backups, KEY)["archive"]
    live = tmp_path / "live"
    live.mkdir()
    (live / "manifest.json").write_text("keep me")
    with pytest.raises(tool.MediaError):
        tool.restore(archive, live, KEY)
    assert (live / "manifest.json").read_text() == "keep me" and len(list(live.iterdir())) == 1


def encrypted_tar(tmp_path, members):
    plain, enc = tmp_path / "plain.tar.gz", tmp_path / "crafted.tar.gz.enc"
    with tarfile.open(plain, "w:gz") as tar:
        for name, body in members:
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    tool.encrypt_file(plain, enc, KEY)
    return enc


def test_unsafe_or_inconsistent_archives_are_refused(tmp_path):
    for label, members in {
        "path traversal": [("../escape", b"x"), (tool.INVENTORY_NAME, b"{}"), (tool.MANIFEST_NAME, b"{}")],
        "no inventory": [(tool.MANIFEST_NAME, b"{}")],
    }.items():
        with pytest.raises(tool.MediaError):
            tool.read_archive(encrypted_tar(tmp_path, members), KEY, tmp_path)
    source = build_pack(tmp_path / "pack")
    inventory = tool.verify_source(source)
    manifest = (source / "manifest.json").read_bytes()
    members = [(tool.INVENTORY_NAME, json.dumps(inventory).encode()), (tool.MANIFEST_NAME, manifest)] + [(i["name"], (source / i["name"]).read_bytes()) for i in inventory["files"]]
    with pytest.raises(tool.MediaError) as extra:
        tool.read_archive(encrypted_tar(tmp_path, members + [("surprise.mp4", b"not in the inventory")]), KEY, tmp_path)
    assert "differ from its inventory" in str(extra.value)
    swapped = [(n, (b"tampered" if n.endswith(".vtt") else b)) for n, b in members]
    with pytest.raises(tool.MediaError) as changed:
        tool.read_archive(encrypted_tar(tmp_path, swapped), KEY, tmp_path)
    assert "content differs" in str(changed.value)


def test_key_rotation_id_is_carried_in_the_archive(tmp_path):
    source, backups = build_pack(tmp_path / "pack"), tmp_path / "backups"
    archive = backups / tool.pack(source, backups, KEY, key_id="2026-10")["archive"]
    assert b"2026-10" in archive.read_bytes()[:40]
    with tempfile_dir(tmp_path) as scratch:
        inventory, _ = tool.read_archive(archive, KEY, scratch, key_id="2026-10")
    assert len(inventory["files"]) == 32


def tempfile_dir(tmp_path):
    import contextlib
    import tempfile

    @contextlib.contextmanager
    def make():
        with tempfile.TemporaryDirectory(dir=tmp_path) as scratch:
            yield __import__("pathlib").Path(scratch)
    return make()


def test_cli_round_trip_never_prints_the_key(tmp_path, monkeypatch, capsys):
    source, backups = build_pack(tmp_path / "pack"), tmp_path / "backups"
    monkeypatch.setenv("WB_SAAS_BACKUP_ENCRYPTION_KEY", KEY)
    assert tool.main(["pack", "--source", str(source), "--backup-dir", str(backups)]) == 0
    packed = json.loads(capsys.readouterr().out)
    archive = backups / packed["archive"]
    assert tool.main(["verify-archive", "--archive", str(archive)]) == 0
    assert tool.main(["restore", "--archive", str(archive), "--dest", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert KEY not in out and KEY not in capsys.readouterr().err
    monkeypatch.delenv("WB_SAAS_BACKUP_ENCRYPTION_KEY")
    assert tool.main(["pack", "--source", str(source), "--backup-dir", str(backups)]) == 1
    assert KEY not in capsys.readouterr().err


def test_the_full_acceptance_rehearsal_passes_on_a_synthetic_pack(tmp_path):
    act = tool.acceptance(build_pack(tmp_path / "pack"), tmp_path / "work", tmp_path / "act.json")
    assert act["verdict"] == "PASS", act["failed_checks"]
    gateway = act["steps"]["6_isolated_gateway"]
    assert gateway["training_page"] == {"videos": 16, "sources": 16, "subtitle_tracks": 16}
    assert gateway["files_full_get"] == 32 and gateway["range_middle"] == 32 and gateway["range_unsatisfiable_416"] == 32
    assert act["steps"]["4_negative_checks"] == {"modified_archive": "rejected", "wrong_key": "rejected", "restore_into_non_empty_directory": "refused"}
    assert json.loads((tmp_path / "act.json").read_text(encoding="utf-8"))["verdict"] == "PASS"


def test_pack_refuses_a_damaged_source_and_writes_nothing(tmp_path):
    source, backups = build_pack(tmp_path / "pack"), tmp_path / "backups"
    victim = next(p for p in source.iterdir() if p.suffix == ".mp4")
    victim.write_bytes(victim.read_bytes()[:-3])
    with pytest.raises(tool.MediaError):
        tool.pack(source, backups, KEY)
    assert not backups.exists() or list(backups.iterdir()) == []


def approved_archive_members(source):
    inventory = tool.verify_source(source)
    return inventory, [(tool.INVENTORY_NAME, json.dumps(inventory).encode()),
                       (tool.MANIFEST_NAME, (source / tool.MANIFEST_NAME).read_bytes()),
                       *[(item["name"], (source / item["name"]).read_bytes()) for item in inventory["files"]]]


@pytest.mark.parametrize("change", ["unapproved", "missing_lesson", "invented_filename", "inventory_kind", "duplicate_inventory"])
def test_authenticated_archive_must_match_independent_approved_catalog(tmp_path, change):
    source = build_pack(tmp_path / "source")
    inventory, members = approved_archive_members(source)
    manifest = json.loads((source / tool.MANIFEST_NAME).read_bytes())
    if change == "unapproved":
        manifest["approved"] = False
    elif change == "missing_lesson":
        manifest["videos"].pop(lesson_slugs()[0])
    elif change == "invented_filename":
        manifest["videos"][lesson_slugs()[0]]["filename"] = "surprise.mp4"
    elif change == "inventory_kind":
        inventory["files"][0]["kind"] = "invented-kind"
    else:
        inventory["files"].append(dict(inventory["files"][0]))
    manifest_bytes = json.dumps(manifest).encode()
    inventory["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    members[0] = (tool.INVENTORY_NAME, json.dumps(inventory).encode())
    members[1] = (tool.MANIFEST_NAME, manifest_bytes)
    archive = encrypted_tar(tmp_path, members)
    with pytest.raises(tool.MediaError):
        tool.read_archive(archive, KEY, tmp_path)
    assert not list(tmp_path.glob("verify-*.tar.gz"))


def test_duplicate_tar_members_are_refused_even_when_last_member_matches_inventory(tmp_path):
    source = build_pack(tmp_path / "source")
    _, members = approved_archive_members(source)
    duplicated = members[2]
    archive = encrypted_tar(tmp_path, [*members[:2], (duplicated[0], b"hidden-invalid-first-copy"), *members[2:]])
    with pytest.raises(tool.MediaError, match="duplicate"):
        tool.read_archive(archive, KEY, tmp_path)
    assert not list(tmp_path.glob("verify-*.tar.gz"))


@pytest.mark.parametrize("bound", ["encrypted", "member_count", "metadata", "expanded"])
def test_archive_size_and_count_bounds_fail_closed_without_plain_residue(tmp_path, monkeypatch, bound):
    source = build_pack(tmp_path / "source")
    _, members = approved_archive_members(source)
    archive = encrypted_tar(tmp_path, members)
    if bound == "encrypted":
        monkeypatch.setattr(tool, "MAX_ARCHIVE_BYTES", archive.stat().st_size - 1)
    elif bound == "member_count":
        monkeypatch.setattr(tool, "MAX_MEMBERS", len(members) - 1)
    elif bound == "metadata":
        monkeypatch.setattr(tool, "MAX_METADATA_BYTES", 16)
    else:
        monkeypatch.setattr(tool, "MAX_MEDIA_BYTES", 100)
    with pytest.raises(tool.MediaError):
        tool.read_archive(archive, KEY, tmp_path)
    assert not list(tmp_path.glob("verify-*.tar.gz"))


def test_gateway_rehearsal_does_not_inherit_real_database_or_external_provider_environment(tmp_path, monkeypatch):
    poisons = {
        "WB_SAAS_DB_BACKEND": "postgres", "WB_SAAS_DATABASE_URL": "postgresql://invalid.invalid/never-connect",
        "WB_SAAS_SENTRY_DSN": "not-a-valid-live-dsn", "WB_SAAS_SMTP_HOST": "never-contact.invalid",
        "WB_SAAS_SMTP_PASSWORD": "synthetic-secret-marker", "WB_SAAS_TELEGRAM_BOT_TOKEN": "synthetic-bot-marker",
        "WB_SAAS_YOOKASSA_SECRET_KEY": "synthetic-payment-marker", "WB_SAAS_ERASURE_LEDGER_PATH": "never-open-original",
        "HTTP_PROXY": "http://never-contact.invalid:8765", "PYTHONPATH": "never-import-parent-code",
    }
    for name, value in poisons.items():
        monkeypatch.setenv(name, value)
    source = build_pack(tmp_path / "source")
    inventory = tool.verify_source(source)
    work = tmp_path / "work"
    work.mkdir()
    result = tool.check_isolated_gateway(source, inventory, work)
    assert result["files_full_get"] == 32 and result["range_first_byte"] == 32
    # SQLite is actually created in the fresh copied gateway, not merely claimed
    # by inspecting the environment builder's dictionary.
    import sqlite3
    with sqlite3.connect(work / "gateway" / "data" / "gateway.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
    log = (work / "gateway.log").read_text(encoding="utf8")
    assert "synthetic-secret-marker" not in log and "synthetic-bot-marker" not in log


def test_reused_manifest_archive_and_unknown_old_files_are_preserved(tmp_path):
    source, backups = build_pack(tmp_path / "source"), tmp_path / "backups"
    first = tool.pack(source, backups, KEY)
    original = backups / first["archive"]
    os.utime(original, (1000, 1000))
    old_hash = tool.sha256_of(original)
    unknown = backups / "unknown-from-another-tool.tar.gz.enc"
    unknown.write_bytes(b"preserve unknown old archive")
    result = tool.pack(source, backups, KEY)
    assert result["result"] == "already_backed_up"
    assert original.stat().st_mtime == 1000 and tool.sha256_of(original) == old_hash
    assert unknown.read_bytes() == b"preserve unknown old archive"
    # Nightly freshness needs explicit force; deduplication must not touch an
    # old archive timestamp just to make monitoring appear fresh.
    forced = tool.pack(source, backups, KEY, force=True)
    assert forced["result"] == "created" and original.stat().st_mtime == 1000


def test_final_name_race_never_overwrites_existing_archive(tmp_path, monkeypatch):
    source, backups = build_pack(tmp_path / "source"), tmp_path / "backups"
    original_link = tool.os.link
    created = []
    def raced_link(staged, final):
        final.write_bytes(b"another writer must stay")
        created.append(final)
        original_link(staged, final)  # FileExistsError instead of replacement
    monkeypatch.setattr(tool.os, "link", raced_link)
    with pytest.raises(tool.MediaError, match="collided"):
        tool.pack(source, backups, KEY)
    assert len(created) == 1 and created[0].read_bytes() == b"another writer must stay"
    assert list(backups.iterdir()) == created
