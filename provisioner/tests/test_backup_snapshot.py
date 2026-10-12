"""Consistent volume snapshots: concurrent writes, encrypted round trip, hardening, retention, failure cleanup.

Isolated temp directories and synthetic data only: no Docker daemon, no tenant data.
"""
import json
import os
import sqlite3
import tarfile
import threading
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

import app
import backup_snapshot as snap

KEY = "backup-secret-that-is-longer-than-thirty-two-characters"


def make_database(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, isolation_level=None, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE ledger(id INTEGER PRIMARY KEY, amount INTEGER NOT NULL)")
    db.execute("CREATE TABLE totals(id INTEGER PRIMARY KEY CHECK(id=1), balance INTEGER NOT NULL)")
    db.execute("INSERT INTO totals VALUES(1, 0)")
    return db


def extract(archive: Path, name: str, target: Path) -> Path:
    with tarfile.open(archive, "r:gz") as tar:
        target.write_bytes(tar.extractfile(name).read())
    return target


class Writer(threading.Thread):
    """Moves money in one transaction: ledger rows and the balance must always agree in any snapshot."""

    def __init__(self, path: Path):
        super().__init__(daemon=True)
        self.path, self.stop, self.committed = path, threading.Event(), 0

    def run(self):
        db = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA wal_autocheckpoint=50")  # checkpoints happen under the snapshot, like a busy tenant
        amount = 0
        while not self.stop.is_set():
            amount += 1
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO ledger(amount) VALUES(?)", (amount,))
            db.execute("UPDATE totals SET balance = balance + ? WHERE id=1", (amount,))
            db.execute("COMMIT")
            self.committed += 1
        db.close()


def check_snapshot(archive: Path, workdir: Path, committed_before: int) -> int:
    restored = extract(archive, "./wb_dashboard.sqlite3", workdir / "restored.sqlite3")
    db = sqlite3.connect(restored)
    try:
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        rows, total = db.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM ledger").fetchone()
        balance = db.execute("SELECT balance FROM totals").fetchone()[0]
    finally:
        db.close()
    assert balance == total, "snapshot mixed two moments in time"
    assert rows >= committed_before, "snapshot lost a transaction that was committed before it started"
    return rows


def test_snapshots_taken_during_heavy_writes_are_whole_and_complete(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    make_database(data / "wb_dashboard.sqlite3").close()
    writer = Writer(data / "wb_dashboard.sqlite3")
    writer.start()
    sizes = []
    try:
        time.sleep(0.2)
        for i in range(25):
            before = writer.committed
            out = tmp_path / f"snap{i}.tar.gz"
            summary = snap.build(str(data), str(out), str(tmp_path / f"stage{i}"))
            assert summary["databases"] == 1
            sizes.append(check_snapshot(out, tmp_path, before))
    finally:
        writer.stop.set()
        writer.join(timeout=10)
    assert sizes == sorted(sizes) and sizes[-1] > sizes[0]  # the writer really ran between snapshots
    assert not list(tmp_path.glob("stage*"))


def test_naive_file_copy_is_not_good_enough(tmp_path):
    """Control: the old approach (copy the files) loses committed rows or mixes moments; the property test above is sensitive."""
    data = tmp_path / "data"
    data.mkdir()
    make_database(data / "wb_dashboard.sqlite3").close()
    writer = Writer(data / "wb_dashboard.sqlite3")
    writer.start()
    bad = 0
    try:
        time.sleep(0.2)
        for i in range(25):
            before = writer.committed
            copy = tmp_path / f"naive{i}.sqlite3"
            copy.write_bytes((data / "wb_dashboard.sqlite3").read_bytes())  # main file only, as an archive of a live volume can end up
            try:
                db = sqlite3.connect(copy)
                rows = db.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
                db.close()
                bad += rows < before
            except sqlite3.DatabaseError:
                bad += 1
    finally:
        writer.stop.set()
        writer.join(timeout=10)
    assert bad > 0


def seed_volume(data: Path) -> dict:
    data.mkdir()
    (data / "reports").mkdir()
    make_database(data / "wb_dashboard.sqlite3").close()
    db = sqlite3.connect(data / "wb_dashboard.sqlite3", isolation_level=None)
    db.execute("INSERT INTO ledger(amount) VALUES(5)")
    db.execute("UPDATE totals SET balance=5")
    live = sqlite3.connect(data / "other.db")  # a second database with an open WAL: -wal/-shm exist next to it
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("CREATE TABLE t(x)")
    live.execute("INSERT INTO t VALUES(1)")
    live.commit()
    files = {"settings.json": b'{"a": 1}', ".wb_token": b"fallback-token", "reports/r.pdf": b"%PDF-fixture" * 100}
    for name, body in files.items():
        (data / name).write_bytes(body)
    os.chmod(data / ".wb_token", 0o600)
    symlinked = True
    try:
        os.symlink("settings.json", data / "link.json")
    except OSError:  # Windows without the symlink privilege; the Linux server path is covered in CI
        symlinked = False
    return {"files": files, "keepalive": (db, live), "symlinked": symlinked}


def fake_docker(monkeypatch, tmp_path, data, calls):
    """Run backup_snapshot.main for the helper container; /data and /backup map to temp directories."""
    def run(image, **kwargs):
        calls.append((image, kwargs))
        helper = Mock()
        if "entrypoint" not in kwargs:  # namespace preparation helper
            helper.wait.return_value = {"StatusCode": 0}
            return helper
        source, output, stage = kwargs["command"]
        mapped = [str(data) if source == "/data" else source, output.replace("/backup", str(tmp_path), 1),
                  stage.replace("/backup", str(tmp_path), 1)]
        helper.wait.return_value = {"StatusCode": snap.main(mapped)}
        helper.logs.return_value = b"snapshot failed: test"
        return helper
    client = Mock()
    client.containers.run.side_effect = run
    monkeypatch.setattr(app, "BACKUP_MOUNT", tmp_path)
    monkeypatch.setattr(app, "BACKUP_DIR", "/host/backups")
    monkeypatch.setattr(app, "BACKUP_ENCRYPTION_KEY", KEY)
    monkeypatch.setattr(app, "TENANT_IMAGE", "tenant-image:test")
    return client


def test_encrypted_round_trip_keeps_content_ownership_and_format(tmp_path, monkeypatch):
    data = tmp_path / "volume"
    seeded = seed_volume(data)
    calls = []
    client = fake_docker(monkeypatch, tmp_path, data, calls)
    result = app._backup_volume("wb-tenant-data-shop", "shop", 14, client)

    archive = tmp_path / "shop" / result["archive"]
    assert archive.read_bytes().startswith(b"WBSAASBK2")  # encryption format unchanged
    plain = tmp_path / "restored.tar.gz"
    app._decrypt_backup(archive, plain)
    with tarfile.open(plain, "r:gz") as tar:
        names = tar.getnames()
        members = {m.name: m for m in tar.getmembers()}
        assert "./wb_dashboard.sqlite3" in names and "./other.db" in names
        assert not any(n.endswith(("-wal", "-shm", "-journal")) for n in names)  # their content is in the snapshot
        for name, body in seeded["files"].items():
            assert tar.extractfile("./" + name).read() == body
        if seeded["symlinked"]:
            assert members["./link.json"].issym() and members["./link.json"].linkname == "settings.json"
        assert members["./reports"].isdir()
        for name in ("./settings.json", "./.wb_token", "./wb_dashboard.sqlite3"):
            original = os.stat(data / name[2:])
            assert (members[name].uid, members[name].gid, members[name].mode) == (original.st_uid, original.st_gid, stat_mode(original))
        restored = extract(plain, "./wb_dashboard.sqlite3", tmp_path / "db.sqlite3")
        other = extract(plain, "./other.db", tmp_path / "other.sqlite3")
    assert sqlite3.connect(restored).execute("SELECT balance FROM totals").fetchone() == (5,)
    assert sqlite3.connect(other).execute("SELECT x FROM t").fetchall() == [(1,)]
    # the container is the hardened helper: tenant UID, no network, no capabilities, read-only root, no tenant entrypoint
    image, kwargs = calls[-1]
    assert image == "tenant-image:test"
    assert kwargs["user"] == "10001:10001" and kwargs["network_disabled"] and kwargs["cap_drop"] == ["ALL"]
    assert kwargs["read_only"] and kwargs["entrypoint"][:3] == ["python", "-I", "-B"]
    assert kwargs["volumes"]["wb-tenant-data-shop"] == {"bind": "/data", "mode": "rw"}
    assert not [p for p in (tmp_path / "shop").iterdir() if p.name.startswith(".")]  # no partial, no stage


def stat_mode(st):
    return st.st_mode & 0o7777


def test_legacy_v1_archives_are_still_readable_and_untouched(tmp_path, monkeypatch):
    """WBSAASBK1 files written by older code stay in place and decrypt; retention rules are unchanged."""
    import backup_crypto
    data = tmp_path / "volume"
    seed_volume(data)
    client = fake_docker(monkeypatch, tmp_path, data, [])
    group = tmp_path / "shop"
    group.mkdir()
    plain = tmp_path / "old.tar.gz"
    with tarfile.open(plain, "w:gz") as tar:
        tar.add(data / "settings.json", arcname="./settings.json")
    v2 = group / "old.tar.gz.enc"
    backup_crypto.encrypt_file(plain, v2, KEY)
    legacy = group / "legacy.tar.gz.enc"
    legacy.write_bytes(b"WBSAASBK1" + b"\x00" * 64)  # shape only: it is never opened by backup or retention
    unknown = [group / "notes.txt", group / "x.tar.gz.enc.bak", group / ".keep", group / ".unrelated.stage"]
    for item in unknown:
        item.write_bytes(b"keep me") if item.name != ".unrelated.stage" else item.mkdir()
    stale = time.time() - 40 * 86400
    os.utime(v2, (stale, stale))
    os.utime(legacy, (stale, stale))
    for item in unknown:
        os.utime(item, (stale, stale))
    result = app._backup_volume("v", "shop", 14, client)
    assert result["pruned"] == 2  # only the two expired *.tar.gz.enc archives
    assert not v2.exists() and not legacy.exists()
    for item in unknown:
        assert item.exists(), f"{item.name} is not ours to delete"
    assert (group / result["archive"]).exists()


def test_failed_helper_removes_everything_and_reports_502(tmp_path, monkeypatch):
    data = tmp_path / "volume"
    data.mkdir()
    (data / "wb_dashboard.sqlite3").write_bytes(snap.SQLITE_MAGIC + b"\x00" * 4096)  # claims to be SQLite, is not
    client = fake_docker(monkeypatch, tmp_path, data, [])
    with pytest.raises(HTTPException) as failure:
        app._backup_volume("v", "shop", 14, client)
    assert failure.value.status_code == 502 and "snapshot" not in failure.value.detail
    assert not list((tmp_path / "shop").iterdir())  # no partial, no stage, no archive


def test_stage_directory_left_by_a_killed_helper_is_removed_by_the_provisioner(tmp_path, monkeypatch):
    data = tmp_path / "volume"
    data.mkdir()
    client = fake_docker(monkeypatch, tmp_path, data, [])
    inner = client.containers.run.side_effect

    def killed(image, **kwargs):
        helper = inner(image, **kwargs)
        if "entrypoint" in kwargs:
            stage = Path(kwargs["command"][2].replace("/backup", str(tmp_path), 1))
            stage.mkdir()
            (stage / "snapshot.sqlite3").write_bytes(b"half a database")
            helper.wait.return_value = {"StatusCode": 137}
        return helper
    client.containers.run.side_effect = killed
    with pytest.raises(HTTPException):
        app._backup_volume("v", "shop", 14, client)
    assert list((tmp_path / "shop").iterdir()) == []


def test_a_copy_that_fails_quick_check_is_never_archived(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    make_database(data / "wb_dashboard.sqlite3").close()
    real = sqlite3.connect

    class Damaged(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql == "PRAGMA quick_check":
                return real(":memory:").execute("SELECT 'row 3 missing from index t_v'")
            return super().execute(sql, *args)
    monkeypatch.setattr(snap.sqlite3, "connect", lambda *a, **k: real(*a, **k) if k.get("uri") else real(*a, factory=Damaged, **k))
    with pytest.raises(snap.SnapshotError, match="integrity"):
        snap.build(str(data), str(tmp_path / "x.tar.gz"), str(tmp_path / "stage"))
    assert not (tmp_path / "stage").exists()


def test_a_damaged_database_fails_the_backup_instead_of_being_archived(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    path = data / "wb_dashboard.sqlite3"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)")
    db.execute("CREATE INDEX t_v ON t(v)")
    db.executemany("INSERT INTO t(v) VALUES(?)", [(f"value-{i:06d}" * 3,) for i in range(4000)])
    db.commit()
    db.close()
    raw = bytearray(path.read_bytes())
    for offset in range(len(raw) // 2, len(raw) // 2 + 4096 * 3, 7):  # scribble over a few pages
        raw[offset] ^= 0xFF
    path.write_bytes(bytes(raw))
    with pytest.raises((snap.SnapshotError, sqlite3.DatabaseError)):
        snap.build(str(data), str(tmp_path / "x.tar.gz"), str(tmp_path / "stage"))
    assert not (tmp_path / "stage").exists()


def test_large_file_that_changes_while_streamed_fails_the_backup(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    big = data / "export.bin"
    big.write_bytes(b"x" * 4096)
    monkeypatch.setattr(snap, "SMALL_FILE_LIMIT", 0)
    original = snap._member

    def touch_then_member(tar, path, arcname, size=None):
        info = original(tar, path, arcname, size)
        big.write_bytes(b"y" * 5000)  # rewritten while it is being archived
        return info
    monkeypatch.setattr(snap, "_member", touch_then_member)
    with pytest.raises(snap.SnapshotError):
        snap.build(str(data), str(tmp_path / "x.tar.gz"), str(tmp_path / "stage"))


def test_small_file_rewritten_while_read_is_retried_until_stable(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.json").write_text("v0")
    real_fstat, calls = os.fstat, []

    class Changed:
        def __init__(self, inner):
            self.st_size, self.st_mtime_ns = inner.st_size, inner.st_mtime_ns + 1

    def fstat(fd):
        calls.append(1)
        inner = real_fstat(fd)
        return Changed(inner) if len(calls) == 2 else inner  # the first attempt sees the file change under its read
    monkeypatch.setattr(snap.os, "fstat", fstat)
    out = tmp_path / "x.tar.gz"
    snap.build(str(data), str(out), str(tmp_path / "stage"))
    monkeypatch.undo()
    with tarfile.open(out, "r:gz") as tar:
        assert tar.extractfile("./settings.json").read() == b"v0"
    assert len(calls) >= 4  # a second attempt was made

    always = lambda fd: Changed(real_fstat(fd)) if False else type("S", (), {"st_size": 2, "st_mtime_ns": next(counter)})()
    counter = iter(range(10**6))
    monkeypatch.setattr(snap.os, "fstat", always)
    with pytest.raises(snap.SnapshotError, match="kept changing"):
        snap.build(str(data), str(tmp_path / "y.tar.gz"), str(tmp_path / "stage2"))


def test_stage_cleanup_touches_only_our_own_stale_directories(tmp_path):
    group = tmp_path / "shop"
    group.mkdir()
    ours = group / ".shop-20261015_010203_123456-0123456789ab.stage"
    fresh = group / ".shop-20261015_010203_123456-ffffffffffff.stage"
    foreign = group / ".manual.stage"
    for directory in (ours, fresh, foreign):
        directory.mkdir()
        (directory / "f").write_bytes(b"x")
    old = time.time() - 7200
    for directory in (ours, foreign):
        os.utime(directory, (old, old))
    app._remove_stale_stage_dirs(group)
    assert not ours.exists() and fresh.exists() and foreign.exists()


def test_snapshot_helper_is_a_standalone_script_with_a_json_summary(tmp_path, capsys):
    data = tmp_path / "data"
    data.mkdir()
    make_database(data / "wb_dashboard.sqlite3").close()
    assert snap.main([str(data), str(tmp_path / "o.tar.gz"), str(tmp_path / "s")]) == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["databases"] == 1
    assert snap.main(["only-one"]) == 2


def test_snapshot_passes_the_demo_restore_verifier_taken_during_writes(tmp_path, monkeypatch):
    """The consumer that restores archives (verify, decrypt, SQLite check, tenant tables) accepts a snapshot of a live tenant."""
    data = tmp_path / "volume"
    data.mkdir()
    db = sqlite3.connect(data / "wb_dashboard.sqlite3", isolation_level=None, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    for table in ("sales", "procurement_orders", "products_catalog"):
        db.execute(f"CREATE TABLE {table}(id INTEGER PRIMARY KEY, v INTEGER)")
    db.close()
    stop, written = threading.Event(), []

    def churn():
        live = sqlite3.connect(data / "wb_dashboard.sqlite3", isolation_level=None, timeout=30)
        i = 0
        while not stop.is_set():
            i += 1
            live.execute("INSERT INTO sales(v) VALUES(?)", (i,))
            written.append(i)
        live.close()
    writer = threading.Thread(target=churn, daemon=True)
    writer.start()
    try:
        client = fake_docker(monkeypatch, tmp_path, data, [])
        result = app._backup_volume("v", "demo", 14, client)
        verified = app._verify_demo_backup(result["archive"], "demo")
    finally:
        stop.set()
        writer.join(timeout=10)
    assert verified.endswith(".verified.tar.gz") and (tmp_path / "demo" / verified).exists()
    assert written, "the writer never ran during the backup"


def test_helper_runs_exactly_as_the_container_starts_it(tmp_path):
    """`python -I -B -c <source> DATA OUT STAGE` is the entrypoint the provisioner passes; no imports from the working directory."""
    import subprocess
    import sys
    data = tmp_path / "data"
    data.mkdir()
    make_database(data / "wb_dashboard.sqlite3").close()
    (data / "settings.json").write_text("{}")
    source = app._BACKUP_SNAPSHOT_SOURCE.read_text(encoding="utf-8")
    done = subprocess.run([sys.executable, "-I", "-B", "-c", source, str(data), str(tmp_path / "o.tar.gz"), str(tmp_path / "s")],
                          capture_output=True, text=True, cwd=tmp_path, timeout=60)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout.strip().splitlines()[-1])["databases"] == 1
    assert not (tmp_path / "s").exists()
    failed = subprocess.run([sys.executable, "-I", "-B", "-c", source, str(tmp_path / "missing"), str(tmp_path / "o2.tar.gz"), str(tmp_path / "s2")],
                            capture_output=True, text=True, cwd=tmp_path, timeout=60)
    assert failed.returncode == 1 and "snapshot failed" in failed.stderr
