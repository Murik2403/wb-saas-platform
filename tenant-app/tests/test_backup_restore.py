import io
import json
import sqlite3
import zipfile
import pytest
import backup_tools as backup
import sync_lock
from cryptography.fernet import Fernet


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "live.sqlite3"
    monkeypatch.setattr(backup, "DATA_DIR", tmp_path)
    monkeypatch.setattr(backup, "DB_PATH", path)
    monkeypatch.setattr(sync_lock, "DB_PATH", path)
    monkeypatch.setattr(backup, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path)
    c = sqlite3.connect(path)
    c.execute("PRAGMA journal_mode=WAL")
    for table in ("orders", "sales", "stocks", "costs", "product_pipeline"):
        c.execute(f"CREATE TABLE {table}(value INTEGER)")
    c.execute("INSERT INTO orders VALUES(1)")
    c.commit()
    yield c
    c.close()


def archive(tmp_path, settings=None):
    candidate = tmp_path / "candidate.sqlite3"
    c = sqlite3.connect(candidate)
    for table in ("orders", "sales", "stocks", "costs", "product_pipeline"):
        c.execute(f"CREATE TABLE {table}(value INTEGER)")
    c.execute("INSERT INTO orders VALUES(2)")
    c.commit()
    c.close()
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as z:
        z.write(candidate, "wb_dashboard.sqlite3")
        if settings is not None:
            z.writestr("settings.json", settings)
    return result.getvalue()


def test_restore_with_open_wal_connection(database, tmp_path):
    backup.restore_backup(archive(tmp_path))
    assert database.execute("SELECT value FROM orders").fetchone()[0] == 2
    assert database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_invalid_settings_leaves_database_untouched(database, tmp_path):
    with pytest.raises(ValueError):
        backup.restore_backup(archive(tmp_path, "not-json"))
    assert database.execute("SELECT value FROM orders").fetchone()[0] == 1


def test_settings_failure_rolls_back_database(database, tmp_path, monkeypatch):
    def fail(*args):
        raise OSError("simulated disk failure")
    monkeypatch.setattr(backup.os, "replace", fail)
    with pytest.raises(OSError):
        backup.restore_backup(archive(tmp_path, json.dumps({"sync_interval_minutes": 30})))
    assert database.execute("SELECT value FROM orders").fetchone()[0] == 1


def test_running_service_blocks_restore(database, tmp_path):
    with sync_lock.sync_lock(backup.DB_PATH.with_suffix(".api.lock")):
        with pytest.raises(RuntimeError, match="Остановите"):
            backup.restore_backup(archive(tmp_path))
    assert database.execute("SELECT value FROM orders").fetchone()[0] == 1


def test_encrypted_backup_can_be_created_and_restored(database, tmp_path, monkeypatch):
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(backup, "DATA_ENCRYPTION_KEY", key)
    database.execute("UPDATE orders SET value=9")
    database.commit()
    path = backup.create_backup(kind="manual", keep=3)
    payload = path.read_bytes()
    assert payload.startswith(backup.BACKUP_ENCRYPTION_PREFIX_V2)
    assert not zipfile.is_zipfile(payload)

    database.execute("UPDATE orders SET value=1")
    database.commit()
    backup.restore_backup(payload)
    assert database.execute("SELECT value FROM orders").fetchone()[0] == 9


def test_encrypted_backup_requires_key_to_restore(database, tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "DATA_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    database.execute("UPDATE orders SET value=7")
    database.commit()
    payload = backup.create_backup(kind="manual", keep=3).read_bytes()

    monkeypatch.setattr(backup, "DATA_ENCRYPTION_KEY", "")
    with pytest.raises(RuntimeError, match="Требуется WB_SAAS_DATA_ENCRYPTION_KEY"):
        backup.restore_backup(payload)


def test_encrypted_backup_survives_key_rotation(database, tmp_path, monkeypatch):
    old_key = Fernet.generate_key().decode("ascii")
    new_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(backup, "DATA_ENCRYPTION_KEY", old_key)
    monkeypatch.setattr(backup, "DATA_ENCRYPTION_KEY_ID", "2026-09-12")
    monkeypatch.setattr(backup, "DATA_ENCRYPTION_KEYRING", "")
    database.execute("UPDATE orders SET value=11")
    database.commit()
    payload = backup.create_backup(kind="manual", keep=3).read_bytes()

    monkeypatch.setattr(backup, "DATA_ENCRYPTION_KEY", new_key)
    monkeypatch.setattr(backup, "DATA_ENCRYPTION_KEY_ID", "2026-09-13")
    monkeypatch.setattr(backup, "DATA_ENCRYPTION_KEYRING", json.dumps({"2026-09-12": old_key}))
    database.execute("UPDATE orders SET value=1")
    database.commit()
    backup.restore_backup(payload)
    assert database.execute("SELECT value FROM orders").fetchone()[0] == 11
