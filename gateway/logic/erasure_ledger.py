"""Deletion intent outside the restorable gateway database.

Enable with WB_SAAS_ERASURE_LEDGER_PATH on a separately mounted durable
volume. Operators explicitly initialize it once; runtime never creates an
empty replacement. Entries contain opaque identity hashes, not email/chat IDs.
This is a resurrection guard, not evidence that every backup was erased.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
from datetime import datetime, timezone
from contextlib import closing


class LedgerUnavailable(RuntimeError):
    """Generic error deliberately excludes paths and identities."""


def identity_key(account):
    row = dict(account)
    try:
        account_id = int(row["id"])
        created = row["created_at"]
        if account_id <= 0 or not isinstance(created, str) or not created.strip():
            raise ValueError()
        datetime.fromisoformat(created.replace("Z", "+00:00"))
    except (KeyError, ValueError, TypeError, OverflowError):
        raise LedgerUnavailable("Erasure ledger identity is invalid") from None
    # created_at is immutable, including on financial tombstones. An id alone
    # would incorrectly block a different account created after a restore.
    return hashlib.sha256(json.dumps([account_id, created], separators=(",", ":")).encode()).hexdigest()


def configured_path():
    value = os.environ.get("WB_SAAS_ERASURE_LEDGER_PATH", "").strip()
    if not value and os.environ.get("WB_SAAS_ERASURE_LEDGER_REQUIRED", "0") == "1":
        raise LedgerUnavailable("Required erasure ledger is not configured")
    return _safe_path(value) if value else None


def _safe_path(value):
    path = Path(value)
    if not path.is_absolute():
        raise LedgerUnavailable("Erasure ledger requires an independent absolute path")
    # Reject symlinks, including parent directories, before resolving paths.
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise LedgerUnavailable("Erasure ledger path is unsafe")
    path = path.resolve()
    gateway_data = (Path(__file__).resolve().parents[1] / "data").resolve()
    if path == gateway_data or gateway_data in path.parents:
        raise LedgerUnavailable("Erasure ledger must be outside gateway snapshots")
    return path


def initialize(path):
    """Operator-only bootstrap. Existing files are validated, never reset."""
    path = _safe_path(path)
    if path.exists():
        validate_path(path)
        return
    # Parent mount must already exist. Exclusive creation prevents overwriting.
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(path, timeout=5)) as conn, conn:
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("CREATE TABLE metadata(version INTEGER NOT NULL CHECK(version=1))")
            conn.execute("INSERT INTO metadata VALUES (1)")
            conn.execute("""CREATE TABLE erasures (
                identity TEXT PRIMARY KEY CHECK(length(identity)=64),
                requested_at TEXT NOT NULL,
                finalization_requested_at TEXT NOT NULL DEFAULT '')""")
    except (OSError, sqlite3.Error):
        # A failed bootstrap is intentionally not removed/recreated on retry.
        raise LedgerUnavailable("Erasure ledger initialization failed") from None
    validate_path(path)


def _connect(path, *, write=False):
    path = _safe_path(path)
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise OSError()
        if os.name != "nt" and info.st_mode & 0o077:
            raise OSError()
        conn = sqlite3.connect(path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=5)
        conn.execute("PRAGMA synchronous=FULL")
        if conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise sqlite3.DatabaseError()
        if conn.execute("SELECT version FROM metadata").fetchall() != [(1,)]:
            raise sqlite3.DatabaseError()
        columns = [row[1] for row in conn.execute("PRAGMA table_info(erasures)")]
        if columns != ["identity", "requested_at", "finalization_requested_at"]:
            raise sqlite3.DatabaseError()
        for key, requested, finalized in conn.execute("SELECT identity,requested_at,finalization_requested_at FROM erasures"):
            if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
                raise sqlite3.DatabaseError()
            try:
                datetime.fromisoformat(requested)
                if finalized:
                    datetime.fromisoformat(finalized)
            except (ValueError, TypeError):
                raise sqlite3.DatabaseError() from None
        return conn
    except (OSError, sqlite3.Error):
        if "conn" in locals():
            conn.close()
        raise LedgerUnavailable("Erasure ledger is unavailable or invalid") from None


def validate_path(path):
    conn = _connect(path)
    conn.close()


def validate_configured():
    path = configured_path()
    if path is not None:
        validate_path(path)


def is_erased(account):
    path = configured_path()
    if path is None:
        return False
    key = identity_key(account)
    conn = _connect(path)
    try:
        return conn.execute("SELECT 1 FROM erasures WHERE identity=?", (key,)).fetchone() is not None
    finally:
        conn.close()


def record(account, *, finalized=False, requested_at=None, finalization_requested_at=None):
    # finalized=True records finalization intent before destructive mutation.
    # It is deliberately NOT a certificate of completed database/archive purge.
    path = configured_path()
    if path is None:
        return
    key = identity_key(account)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    requested_stamp = requested_at or stamp
    finalization_stamp = (finalization_requested_at or stamp) if finalized else ""
    try:
        datetime.fromisoformat(requested_stamp)
        if finalization_stamp:
            datetime.fromisoformat(finalization_stamp)
    except (ValueError, TypeError):
        raise LedgerUnavailable("Erasure ledger event timestamp is invalid") from None
    conn = _connect(path, write=True)
    try:
        with conn:
            conn.execute("""INSERT INTO erasures(identity,requested_at,finalization_requested_at) VALUES (?,?,?)
                ON CONFLICT(identity) DO UPDATE SET finalization_requested_at=CASE
                WHEN erasures.finalization_requested_at='' AND excluded.finalization_requested_at!=''
                THEN excluded.finalization_requested_at ELSE erasures.finalization_requested_at END""",
                (key, requested_stamp, finalization_stamp))
    except sqlite3.Error:
        raise LedgerUnavailable("Erasure ledger write failed") from None
    finally:
        conn.close()


def reconcile(conn):
    """Block restored identities and invalidate access pointers before traffic.

    Does not purge tenant volumes or impersonate completed archive deletion.
    Caller commits gateway transaction; ledger remains independent of rollback.
    """
    path = configured_path()
    if path is None:
        return 0
    ledger = _connect(path)
    try:
        entries = dict(ledger.execute("SELECT identity,requested_at FROM erasures"))
    finally:
        ledger.close()
    matched = 0
    for row in conn.execute("SELECT * FROM accounts").fetchall():
        stamp = entries.get(identity_key(row))
        if stamp is None:
            continue
        conn.execute("UPDATE accounts SET deletion_requested_at=CASE WHEN deletion_requested_at='' THEN ? ELSE deletion_requested_at END,status='canceled' WHERE id=?", (stamp, row["id"]))
        for table in ("sessions", "telegram_link_codes", "password_reset_tokens", "email_verification_tokens", "email_change_tokens"):
            conn.execute(f"DELETE FROM {table} WHERE account_id=?", (row["id"],))
        conn.execute("UPDATE tenant_instances SET telegram_chat_id='' WHERE account_id=?", (row["id"],))
        matched += 1
    return matched


def seed_existing_deletions(conn):
    """Capture pre-upgrade deletion intents without purging any account.

    Original created_at is retained on financial tombstones. This can only
    protect identities still present in the inspected database; it cannot
    discover historical accounts absent from it.
    """
    if configured_path() is None:
        return 0
    count = 0
    for row in conn.execute("SELECT * FROM accounts WHERE deletion_requested_at!='' OR deleted_at!=''").fetchall():
        data = dict(row)
        record(row, finalized=bool(data.get("deleted_at")),
               requested_at=data.get("deletion_requested_at") or data.get("deleted_at"),
               finalization_requested_at=data.get("deleted_at"))
        count += 1
    return count
