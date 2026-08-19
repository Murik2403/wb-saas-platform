"""Control-plane schema: accounts / sessions / tenant_instances.

Deliberately separate from every tenant's own data (which lives in that
tenant's own container/volume, running the unmodified WB Dashboard app).
This is the gateway's own bookkeeping: who has an account, what their
subscription status is, which container serves them, and what session
cookies are currently valid.

SQLite is used here the same way the tenant app uses it -- it's not shared
across tenants (there is exactly one control-plane database, owned by the
gateway process), so there's no multi-tenant-in-one-table risk to worry
about. It can be swapped for Postgres later without touching tenant
isolation, since tenant isolation is structural (separate containers), not
a property of this database.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path


DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "gateway.sqlite3"


@contextmanager
def connect(db_path: Path | str = DEFAULT_DB_PATH):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            -- Billing (see logic/billing.py). current_period_end is the
            -- trial end date until the first successful payment, then the
            -- paid-through date. payment_method_id is YooKassa's saved
            -- payment method, used for unattended recurring charges -- we
            -- never store card details ourselves. past_due_since starts a
            -- grace-period countdown when a charge fails or a trial lapses
            -- unpaid.
            current_period_end TEXT DEFAULT '',
            payment_method_id TEXT DEFAULT '',
            past_due_since TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        -- Audit log of every payment attempt (initial, recurring, manual
        -- recovery) -- kept even for failures, since "why was this client
        -- locked out" is exactly the kind of question support will ask.
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            yookassa_payment_id TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL DEFAULT 'recurring',
            amount_rub REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            period_start TEXT DEFAULT '',
            period_end TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_payments_account ON payments(account_id, created_at);

        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            account_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_account ON sessions(account_id);

        CREATE TABLE IF NOT EXISTS tenant_instances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL UNIQUE,
            slug TEXT NOT NULL UNIQUE,
            container_name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'provisioning',
            note TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_tenant_instances_status ON tenant_instances(status);

        -- "Forgot password" links. Only the SHA-256 hash of the raw token is
        -- stored (same pattern as sessions.token_hash) -- a leak of this
        -- table alone can't be used to reset anyone's password. Single-use:
        -- consumed_at is set the moment a token is successfully used, and a
        -- second attempt with the same raw token is rejected even before
        -- expires_at.
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            token_hash TEXT PRIMARY KEY,
            account_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            consumed_at TEXT,
            FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_account ON password_reset_tokens(account_id);
        """
    )
