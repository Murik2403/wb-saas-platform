from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from config import DATA_DIR, DB_PATH, SETTINGS_PATH

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


def create_backup(kind: str = "manual", keep: int = 14) -> Path:
    """Create a verified ZIP backup without the WB API token."""
    kind = "auto" if kind == "auto" else "manual"
    stamp = _timestamp()
    final_path = BACKUP_DIR / f"wb_dashboard_{kind}_{stamp}.zip"

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

        with zipfile.ZipFile(final_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(snapshot, arcname="wb_dashboard.sqlite3")
            zf.write(tmp / "manifest.json", arcname="manifest.json")
            settings_copy = tmp / "settings.json"
            if settings_copy.exists():
                zf.write(settings_copy, arcname="settings.json")

    _prune_backups(max(1, int(keep)))
    return final_path


def ensure_daily_backup(keep: int = 14) -> Path | None:
    """Create at most one automatic backup per local calendar day."""
    today_prefix = f"wb_dashboard_auto_{datetime.now():%Y%m%d}_"
    if any(path.name.startswith(today_prefix) for path in BACKUP_DIR.glob("*.zip")):
        return None
    if not DB_PATH.exists():
        return None
    return create_backup(kind="auto", keep=keep)


def _prune_backups(keep: int) -> None:
    backups = sorted(BACKUP_DIR.glob("wb_dashboard_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in backups[keep:]:
        try:
            path.unlink()
        except OSError:
            pass


def list_backups(limit: int = 30) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(BACKUP_DIR.glob("wb_dashboard_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
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
    if "wb_dashboard.sqlite3" not in names:
        raise ValueError("В архиве нет файла базы wb_dashboard.sqlite3.")
    unexpected = names - allowed
    if unexpected:
        raise ValueError(f"В архиве есть неподдерживаемые файлы: {', '.join(sorted(unexpected))}")
    for name in names:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Архив содержит небезопасные пути.")


def inspect_backup(payload: bytes) -> dict[str, Any]:
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
    """Verify and restore database. A safety backup is created first."""
    safety = create_backup(kind="manual")
    with tempfile.TemporaryDirectory(prefix="wb_restore_") as tmp_name:
        tmp = Path(tmp_name)
        with zipfile.ZipFile(io.BytesIO(payload), "r") as zf:
            _validate_zip_names(zf)
            zf.extract("wb_dashboard.sqlite3", path=tmp)
            if restore_settings and "settings.json" in zf.namelist():
                zf.extract("settings.json", path=tmp)

        candidate = tmp / "wb_dashboard.sqlite3"
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
                raise RuntimeError(f"Это не резервная копия WB Dashboard. Нет таблиц: {', '.join(sorted(missing))}")
        finally:
            connection.close()

        # Remove WAL sidecars before atomic replacement. They will be recreated by SQLite.
        for suffix in ("-wal", "-shm"):
            try:
                Path(str(DB_PATH) + suffix).unlink(missing_ok=True)
            except OSError:
                pass
        replacement = DATA_DIR / f".restore_{_timestamp()}.sqlite3"
        shutil.copy2(candidate, replacement)
        os.replace(replacement, DB_PATH)

        settings_candidate = tmp / "settings.json"
        if restore_settings and settings_candidate.exists():
            replacement_settings = DATA_DIR / f".restore_settings_{_timestamp()}.json"
            shutil.copy2(settings_candidate, replacement_settings)
            os.replace(replacement_settings, SETTINGS_PATH)

    return {
        "ok": True,
        "safety_backup": str(safety),
        "message": "Резервная копия восстановлена. Токен API не изменён.",
    }
