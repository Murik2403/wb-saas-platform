from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
from cryptography.fernet import Fernet, InvalidToken
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from config import (
    DATA_DIR,
    DATA_ENCRYPTION_KEY,
    DATA_ENCRYPTION_KEY_ID,
    DATA_ENCRYPTION_KEYRING,
    DB_PATH,
    SETTINGS_PATH,
)
from sync_lock import sync_lock, SyncBusyError
from contextlib import ExitStack

BACKUP_DIR = DATA_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _safe_sqlite_snapshot(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(DB_PATH, timeout=30)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
        check = destination.execute("PRAGMA integrity_check").fetchone()
        if not check or str(check[0]).lower() != "ok":
            raise RuntimeError("Проверка целостности резервной копии не пройдена.")
    finally:
        destination.close()
        source.close()


BACKUP_FILENAME_PREFIX = "marketshelper"
# Old backups created before the MARKETSHELPER rebrand are still named with
# this prefix. Discovery/pruning below matches both so existing backup
# history keeps showing up (and getting cleaned up) after the rename -- only
# the archive's own internal member name (see _validate_zip_names) has to
# stay "wb_dashboard.sqlite3" forever, since that's what makes every backup
# ever created, old or new, still restorable.
_LEGACY_BACKUP_FILENAME_PREFIX = "wb_dashboard"
BACKUP_ENCRYPTION_PREFIX = b"mh-backup-enc:v1:"
BACKUP_ENCRYPTION_PREFIX_V2 = b"mh-backup-enc:v2:"


def _backup_keyring() -> dict[str, str]:
    """Return current and retained Fernet keys indexed by stable key id."""
    keys: dict[str, str] = {}
    if DATA_ENCRYPTION_KEY:
        keys[DATA_ENCRYPTION_KEY_ID or "current"] = DATA_ENCRYPTION_KEY
    if DATA_ENCRYPTION_KEYRING:
        try:
            value = json.loads(DATA_ENCRYPTION_KEYRING)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Некорректный формат WB_SAAS_DATA_ENCRYPTION_KEYRING") from exc
        if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
            raise RuntimeError("WB_SAAS_DATA_ENCRYPTION_KEYRING должен быть JSON-объектом ключей")
        keys.update(value)
    return keys


def _fernet_for_key(key: str) -> Fernet:
    try:
        return Fernet(key.encode("ascii"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Некорректный формат ключа шифрования backup") from exc


def _backup_fernet() -> Fernet | None:
    if not DATA_ENCRYPTION_KEY:
        return None
    try:
        return _fernet_for_key(DATA_ENCRYPTION_KEY)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Некорректный формат WB_SAAS_DATA_ENCRYPTION_KEY") from exc


def _maybe_encrypt_backup(payload: bytes) -> bytes:
    fernet = _backup_fernet()
    if fernet is None:
        return payload
    key_id = (DATA_ENCRYPTION_KEY_ID or "current").encode("utf-8")
    if not key_id or b":" in key_id or len(key_id) > 128:
        raise RuntimeError("Некорректный WB_SAAS_DATA_ENCRYPTION_KEY_ID")
    return BACKUP_ENCRYPTION_PREFIX_V2 + key_id + b":" + fernet.encrypt(payload)


def _maybe_decrypt_backup(payload: bytes) -> bytes:
    if payload.startswith(BACKUP_ENCRYPTION_PREFIX_V2):
        remainder = payload[len(BACKUP_ENCRYPTION_PREFIX_V2):]
        key_id_raw, separator, encrypted = remainder.partition(b":")
        if not separator or not key_id_raw:
            raise RuntimeError("Зашифрованный backup не содержит идентификатор ключа")
        key_id = key_id_raw.decode("utf-8", errors="strict")
        key = _backup_keyring().get(key_id)
        if not key:
            if not DATA_ENCRYPTION_KEY and not DATA_ENCRYPTION_KEYRING:
                raise RuntimeError("Требуется WB_SAAS_DATA_ENCRYPTION_KEY для чтения зашифрованного бэкапа")
            raise RuntimeError(f"Не найден ключ шифрования backup: {key_id}")
        fernet = _fernet_for_key(key)
        try:
            return fernet.decrypt(encrypted)
        except InvalidToken as exc:
            raise RuntimeError("Не удалось расшифровать бэкап: неверный ключ") from exc
    if not payload.startswith(BACKUP_ENCRYPTION_PREFIX):
        return payload
    fernet = _backup_fernet()
    if fernet is None:
        raise RuntimeError("Требуется WB_SAAS_DATA_ENCRYPTION_KEY для чтения зашифрованного бэкапа")
    try:
        return fernet.decrypt(payload[len(BACKUP_ENCRYPTION_PREFIX):])
    except InvalidToken as exc:
        raise RuntimeError("Не удалось расшифровать бэкап: неверный ключ") from exc


def _backup_glob_patterns() -> list[str]:
    return [f"{BACKUP_FILENAME_PREFIX}_*.zip", f"{_LEGACY_BACKUP_FILENAME_PREFIX}_*.zip"]


def _all_backup_paths() -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for pattern in _backup_glob_patterns():
        for path in BACKUP_DIR.glob(pattern):
            if path.name not in seen:
                seen.add(path.name)
                paths.append(path)
    return paths


def create_backup(kind: str = "manual", keep: int = 14) -> Path:
    """Create a verified ZIP backup without the WB API token."""
    kind = "auto" if kind == "auto" else "manual"
    stamp = _timestamp()
    final_path = BACKUP_DIR / f"{BACKUP_FILENAME_PREFIX}_{kind}_{stamp}.zip"

    with tempfile.TemporaryDirectory(prefix="wb_backup_") as tmp_name:
        tmp = Path(tmp_name)
        snapshot = tmp / "wb_dashboard.sqlite3"
        _safe_sqlite_snapshot(snapshot)

        manifest: dict[str, Any] = {
            "format": 1,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "kind": kind,
            "contains_token": False,
            "database": "wb_dashboard.sqlite3",
            "settings": SETTINGS_PATH.name if SETTINGS_PATH.exists() else None,
        }
        (tmp / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if SETTINGS_PATH.exists():
            shutil.copy2(SETTINGS_PATH, tmp / "settings.json")

        with tempfile.TemporaryDirectory(prefix="wb_backup_zip_") as zip_tmp:
            zip_path = Path(zip_tmp) / "backup.zip"
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.write(snapshot, arcname="wb_dashboard.sqlite3")
                zf.write(tmp / "manifest.json", arcname="manifest.json")
                settings_copy = tmp / "settings.json"
                if settings_copy.exists():
                    zf.write(settings_copy, arcname="settings.json")
            payload = zip_path.read_bytes()

    final_path.write_bytes(_maybe_encrypt_backup(payload))
    _prune_backups(max(1, int(keep)))
    return final_path


def ensure_daily_backup(keep: int = 14) -> Path | None:
    """Create at most one automatic backup per local calendar day."""
    today_suffix = f"_auto_{datetime.now():%Y%m%d}_"
    if any(today_suffix in path.name for path in _all_backup_paths()):
        return None
    if not DB_PATH.exists():
        return None
    return create_backup(kind="auto", keep=keep)


def _prune_backups(keep: int) -> None:
    backups = sorted(_all_backup_paths(), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in backups[keep:]:
        try:
            path.unlink()
        except OSError:
            pass


def list_backups(limit: int = 30) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(_all_backup_paths(), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        stat = path.stat()
        rows.append({
            "name": path.name,
            "path": str(path),
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime),
            "kind": "Автоматическая" if "_auto_" in path.name else "Ручная",
        })
    return rows


def latest_backup() -> dict[str, Any] | None:
    rows = list_backups(limit=1)
    return rows[0] if rows else None


def backup_bytes(path: str | Path) -> bytes:
    return Path(path).read_bytes()


def _validate_zip_names(zf: zipfile.ZipFile) -> None:
    allowed = {"wb_dashboard.sqlite3", "settings.json", "manifest.json"}
    names = set(zf.namelist())
    if len(names) != len(zf.namelist()):
        raise ValueError("Архив содержит повторяющиеся файлы.")
    if sum(item.file_size for item in zf.infolist()) > 2 * 1024 ** 3:
        raise ValueError("Распакованный архив превышает 2 ГБ.")
    if "wb_dashboard.sqlite3" not in names:
        raise ValueError("В архиве нет файла базы данных.")
    unexpected = names - allowed
    if unexpected:
        raise ValueError(f"В архиве есть неподдерживаемые файлы: {', '.join(sorted(unexpected))}")
    for name in names:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Архив содержит небезопасные пути.")


def inspect_backup(payload: bytes) -> dict[str, Any]:
    payload = _maybe_decrypt_backup(payload)
    with zipfile.ZipFile(io.BytesIO(payload), "r") as zf:
        _validate_zip_names(zf)
        manifest: dict[str, Any] = {}
        if "manifest.json" in zf.namelist():
            try:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                manifest = {}
        return {
            "created_at": manifest.get("created_at", "Не указано"),
            "kind": manifest.get("kind", "unknown"),
            "contains_settings": "settings.json" in zf.namelist(),
            "contains_token": bool(manifest.get("contains_token", False)),
        }


def restore_backup(payload: bytes, restore_settings: bool = True) -> dict[str, Any]:
    """Use SQLite's transactional backup API; never unlink an active WAL."""
    try:
        payload = _maybe_decrypt_backup(payload)
        with ExitStack() as guards:
            for name in ("api", "streamlit", "jobs", "agents", "reports"):
                guards.enter_context(sync_lock(DB_PATH.with_suffix(f".{name}.lock")))
            guards.enter_context(sync_lock())
            return _restore_backup_locked(payload, restore_settings)
    except SyncBusyError as exc:
        raise RuntimeError("Остановите API, интерфейс и фоновые задачи перед восстановлением. Используйте scripts/restore_local.py после остановки приложения.") from exc


def _restore_backup_locked(payload: bytes, restore_settings: bool) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="wb_restore_") as tmp_name:
        tmp = Path(tmp_name)
        with zipfile.ZipFile(io.BytesIO(payload), "r") as zf:
            _validate_zip_names(zf)
            zf.extract("wb_dashboard.sqlite3", path=tmp)
            if restore_settings and "settings.json" in zf.namelist():
                zf.extract("settings.json", path=tmp)

        candidate = tmp / "wb_dashboard.sqlite3"
        settings_candidate = tmp / "settings.json"
        if settings_candidate.exists():
            settings_value = json.loads(settings_candidate.read_text(encoding="utf-8"))
            if not isinstance(settings_value, dict):
                raise ValueError("Настройки в архиве должны быть JSON-объектом.")
        connection = sqlite3.connect(candidate)
        try:
            check = connection.execute("PRAGMA integrity_check").fetchone()
            if not check or str(check[0]).lower() != "ok":
                raise RuntimeError("Загруженная база повреждена: integrity_check не вернул OK.")
            # Basic schema validation protects against uploading an unrelated SQLite DB.
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required = {"orders", "sales", "stocks", "costs", "product_pipeline"}
            missing = required - tables
            if missing:
                raise RuntimeError(f"Это не резервная копия MARKETSHELPER. Нет таблиц: {', '.join(sorted(missing))}")
        finally:
            connection.close()

        safety = create_backup(kind="manual")
        rollback_db = tmp / "before.sqlite3"
        _safe_sqlite_snapshot(rollback_db)
        previous_settings = SETTINGS_PATH.read_bytes() if SETTINGS_PATH.exists() else None

        def copy_database(source_path):
            # SQLite retains one file identity for existing readers, manages
            # WAL itself and commits the destination as one transaction.
            import time
            deadline = time.monotonic() + 30
            def progress(status, remaining, total):
                if time.monotonic() > deadline:
                    raise TimeoutError("База занята. Повторите восстановление после остановки операций.")
            source = sqlite3.connect(source_path)
            target = sqlite3.connect(DB_PATH, timeout=30)
            try:
                source.backup(target, pages=256, progress=progress, sleep=0.05)
            finally:
                target.close()
                source.close()

        copy_database(candidate)
        try:
            if restore_settings and settings_candidate.exists():
                replacement_settings = DATA_DIR / f".restore_settings_{_timestamp()}.json"
                shutil.copy2(settings_candidate, replacement_settings)
                os.replace(replacement_settings, SETTINGS_PATH)
        except Exception:
            copy_database(rollback_db)
            if previous_settings is not None:
                SETTINGS_PATH.write_bytes(previous_settings)
            raise

    return {
        "ok": True,
        "safety_backup": str(safety),
        "message": "Резервная копия восстановлена. Токен API не изменён.",
    }
