"""One daily verified course copy; existing offsite job runs AFTER this succeeds.

No upload, arbitrary cleanup, source write, gateway startup or provider key log.
Reads the same backup key/key ID/keyring as tenant archives. Nightly force avoids
deduplication making an unchanged course permanently fail the 26-hour freshness
gate. Retention deletes only expired, authenticated files bearing our exact name.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import training_media_backup as media
from scripts.backup_offsite import alert
from backup_crypto import parse_keyring, validate_key_id

OWN_NAME = re.compile(r"training-media-([0-9a-f]{12})-([0-9]{8}T[0-9]{6}Z)-[0-9a-f]{6}\.tar\.gz\.enc\Z")


def _no_links(path: Path):
    if any(parent.is_symlink() or getattr(parent, "is_junction", lambda: False)() for parent in (path, *path.parents)):
        raise ValueError("linked backup boundary")


def _identity(path: Path):
    stat = path.stat(follow_symlinks=False)
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def prune_verified(directory: Path, current: Path, key: str, key_id: str, ring: dict, cutoff: float) -> dict:
    _no_links(directory)
    pruned, unverified = 0, 0
    for path in directory.iterdir():
        match = OWN_NAME.fullmatch(path.name)
        if path == current or not match or path.is_symlink() or not path.is_file():
            continue
        try:
            named_at = datetime.strptime(match[2], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
            original = _identity(path)
            # Both creation-name and mtime must be expired. A changed or future
            # file is never inferred to be an old owned copy from one timestamp.
            if named_at >= cutoff or original[3] / 1e9 >= cutoff:
                continue
            with tempfile.TemporaryDirectory(dir=directory) as scratch:
                inventory, _ = media.read_archive(path, key, Path(scratch), key_id=key_id, keyring=ring)
            if inventory.get("tool") != "training_media_backup.py" or inventory.get("format") != 1 or inventory["manifest_sha256"][:12] != match[1] or _identity(path) != original or path.is_symlink():
                unverified += 1
                continue
        except (media.MediaError, OSError, ValueError):
            unverified += 1
            continue
        path.unlink()  # exact matching old file, authenticated AFTER a new copy
        pruned += 1
    return {"expired_owned_archives_pruned": pruned, "expired_unverified_archives_preserved": unverified}


def run(env=None) -> int:
    env = os.environ if env is None else env
    at = time.time()
    status = {"kind": "training_media_backup", "at": at, "ok": False, "error": None, "alert": "not_needed"}
    try:
        source_raw = env.get("WB_SAAS_TRAINING_MEDIA_SOURCE_DIR", "")
        key = env.get("WB_SAAS_BACKUP_ENCRYPTION_KEY", "")
        keep = env.get("WB_SAAS_BACKUP_KEEP_DAYS", "14")
        if not source_raw or len(key) < 32 or not isinstance(keep, str) or not keep.isascii() or not keep.isdigit() or not 1 <= int(keep) <= 3650:
            raise ValueError("invalid daily backup configuration")
        key_id = validate_key_id(env.get("WB_SAAS_BACKUP_ENCRYPTION_KEY_ID") or media.DEFAULT_KEY_ID)
        ring = parse_keyring(env.get("WB_SAAS_BACKUP_ENCRYPTION_KEYRING", ""))
        source = Path(source_raw)
        directory = Path(env.get("WB_SAAS_BACKUP_DIR", "/opt/wb-saas/backups")) / "training-media"
        _no_links(source); _no_links(directory)
        if not source.is_dir():
            raise ValueError("missing training source")
        packed = media.pack(source, directory, key, key_id=key_id, force=True)
        current = directory / packed["archive"]
        if packed.get("result") != "created" or packed.get("verified_after_write") is not True or not OWN_NAME.fullmatch(current.name):
            raise ValueError("new verified daily copy required")
        with tempfile.TemporaryDirectory(dir=directory) as scratch:
            inventory, _ = media.read_archive(current, key, Path(scratch), key_id=key_id, keyring=ring)
        if inventory["manifest_sha256"] != packed["manifest_sha256"]:
            raise ValueError("new copy identity differs")
        retention = prune_verified(directory, current, key, key_id, ring, at - int(keep) * 86400)
        status.update(ok=True, archive=current.name, lessons=inventory["lessons"], files=len(inventory["files"]),
                      verified_after_write=True, **retention)
    except Exception:
        status["error"] = "local_backup_failed"
        status["alert"] = alert({"kind": "backup_failure", "code": "local_backup_failed", "at": at}, env)
    print(json.dumps(status))
    return int(not status["ok"])


if __name__ == "__main__":
    raise SystemExit(run())
