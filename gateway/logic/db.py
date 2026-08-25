"""Control-plane schema: accounts / sessions / tenant_instances.

Deliberately separate from every tenant's own data (which lives in that
tenant's own container/volume, running the unmodified WB Dashboard app).
This is the gateway's own bookkeeping: who has an account, what their
subscription status is, which container serves them, and what session
cookies are currently valid.

SQLite is used here the same way the tenant app uses it -- it's not shared
across tenants (there is exactly one control-plane database, owned by the
gateway process), so there's no multi-tenant-in-one-table risk to worry
about.

Backend is switchable via config.DB_BACKEND ("sqlite", the default, or
"postgres") for when concurrent writers to this shared db actually start
contending -- see DEPLOY.md's "Switching the control-plane db to Postgres"
section. Every caller in accounts.py/billing.py/password_reset.py writes
plain SQL with "?" placeholders and reads rows by column name
(row["email"]) -- never by position -- specifically so that SQL keeps
working unchanged on both backends: connect() returns a sqlite3.Connection
on "sqlite", or a _PGConnection adapter (translating "?" to psycopg's "%s"
and returning dict-like rows) on "postgres". The two INSERTs that need the
new row's id use "RETURNING id" + fetchone()[0] rather than
cursor.lastrowid, since RETURNING works identically on modern SQLite
(3.35+) and Postgres, whereas lastrowid does not exist on psycopg.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import config

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "gateway.sqlite3"

_PLACEHOLDER_RE = re.compile(r"\?")


def _translate_placeholders(sql: str) -> str:
    """'?' (sqlite3 style) -> '%s' (psycopg style). Safe as a blind
    substitution because none of this project's SQL contains a literal
    '?' (no LIKE patterns, no '?' in string literals) -- verified by grep
    across logic/accounts.py, logic/billing.py, logic/password_reset.py."""
    return _PLACEHOLDER_RE.sub("%s", sql)


class _PGConnection:
    """Adapts a psycopg connection to the sqlite3.Connection subset every
    caller in this codebase actually uses: execute(sql, params) with '?'
    placeholders, returning a cursor with .fetchone()/.fetchall() giving
    dict-like rows. Nothing here does anything sqlite3-specific (no
    row_factory assignment, no PRAGMA), so callers don't need to know or
    care which backend they got.
    """

    def __init__(self, pg_conn):
        self._conn = pg_conn

    def execute(self, sql, params=()):
        return self._conn.execute(_translate_placeholders(sql), params)

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _connect_postgres() -> _PGConnection:
    if not config.DATABASE_URL:
        raise RuntimeError(
            "WB_SAAS_DB_BACKEND=postgres requires WB_SAAS_DATABASE_URL to be set."
        )
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        # psycopg is in requirements.txt (needed by tests/test_db_backend_postgres.py
        # regardless of which backend production runs on), so this only fires on an
        # environment that installed a stale/incomplete requirements.txt.
        raise RuntimeError(
            "WB_SAAS_DB_BACKEND=postgres requires the 'psycopg[binary]' package "
            "(pip install -r requirements.txt)."
        ) from exc
    return _PGConnection(psycopg.connect(config.DATABASE_URL, row_factory=dict_row))


@contextmanager
def connect(db_path: Path | str = DEFAULT_DB_PATH):
    if config.DB_BACKEND == "postgres":
        conn = _connect_postgres()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
        return

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


# Postgres DDL equivalent of the SQLite schema below. Kept as separate
# single statements (rather than one executescript-style blob) since
# psycopg's simple-query protocol is not guaranteed the same
# multi-statement-per-call behavior sqlite3.executescript() gives.
#
# Differences from the SQLite schema, and why:
# - INTEGER PRIMARY KEY AUTOINCREMENT -> GENERATED ALWAYS AS IDENTITY
#   (Postgres' modern equivalent; SERIAL is the older, discouraged spelling).
# - UNIQUE COLLATE NOCASE on accounts.email -> plain UNIQUE: every caller
#   (create_account, get_account_by_email) already does
#   email.strip().lower() in Python before touching the db, so case-folding
#   at the SQL layer would be redundant.
# - FOREIGN KEY(...) REFERENCES ... inlined into the column definition
#   instead of a trailing FOREIGN KEY(...) clause -- equivalent, just
#   Postgres' more common spelling.
_PG_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        password_salt TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        current_period_end TEXT DEFAULT '',
        payment_method_id TEXT DEFAULT '',
        past_due_since TEXT DEFAULT '',
        pdn_consent_at TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        yookassa_payment_id TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL DEFAULT 'recurring',
        amount_rub REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending',
        period_start TEXT DEFAULT '',
        period_end TEXT DEFAULT '',
        error_message TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_payments_account ON payments(account_id, created_at)",
    """
    CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sessions_account ON sessions(account_id)",
    """
    CREATE TABLE IF NOT EXISTS tenant_instances (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        account_id INTEGER NOT NULL UNIQUE REFERENCES accounts(id) ON DELETE CASCADE,
        slug TEXT NOT NULL UNIQUE,
        container_name TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'provisioning',
        note TEXT DEFAULT '',
        telegram_chat_id TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tenant_instances_status ON tenant_instances(status)",
    """
    CREATE TABLE IF NOT EXISTS telegram_link_codes (
        code TEXT PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        consumed_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_telegram_link_codes_account ON telegram_link_codes(account_id)",
    """
    CREATE TABLE IF NOT EXISTS password_reset_tokens (
        token_hash TEXT PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        consumed_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_account ON password_reset_tokens(account_id)",
]


def _pg_column_exists(conn: _PGConnection, table: str, column: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name=? AND column_name=?",
        (table, column),
    ).fetchone()
    return row is not None


def _init_db_postgres(conn: _PGConnection) -> None:
    for statement in _PG_SCHEMA_STATEMENTS:
        conn.execute(statement)
    # Postgres-equivalent of the SQLite ALTER-if-missing migrations below --
    # information_schema.columns instead of PRAGMA table_info.
    if not _pg_column_exists(conn, "accounts", "pdn_consent_at"):
        conn.execute("ALTER TABLE accounts ADD COLUMN pdn_consent_at TEXT DEFAULT ''")
    if not _pg_column_exists(conn, "tenant_instances", "telegram_chat_id"):
        conn.execute("ALTER TABLE tenant_instances ADD COLUMN telegram_chat_id TEXT DEFAULT ''")


def init_db(conn) -> None:
    if config.DB_BACKEND == "postgres":
        _init_db_postgres(conn)
        return

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
            -- 152-ФЗ: timestamp of explicit consent to personal-data
            -- processing, given via the required checkbox on the
            -- registration form (see gateway/app.py register_submit()).
            -- Empty for accounts created before this column existed.
            pdn_consent_at TEXT DEFAULT '',
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
            -- Telegram chat id linked via /link <code> in telegram_bot.py
            -- (see telegram_link_codes below). Empty = not linked; a report
            -- scheduled with Telegram delivery has nowhere to send until
            -- this is set.
            telegram_chat_id TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_tenant_instances_status ON tenant_instances(status);

        -- One-time codes a tenant's dashboard generates (see
        -- internal_routes.py's /internal/telegram-link-code) so its owner
        -- can prove "I am this account" to the Telegram bot by sending
        -- /link <code> -- the bot has no other way to associate a chat_id
        -- with an account, since Telegram chat ids carry no email/account
        -- identity. Single-use, short TTL (see logic/accounts.py's
        -- TELEGRAM_LINK_CODE_TTL_MINUTES), same shape as password_reset_tokens.
        CREATE TABLE IF NOT EXISTS telegram_link_codes (
            code TEXT PRIMARY KEY,
            account_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            consumed_at TEXT,
            FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_telegram_link_codes_account ON telegram_link_codes(account_id);

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
    # Migration for databases created before pdn_consent_at existed --
    # CREATE TABLE IF NOT EXISTS above is a no-op against an already-existing
    # accounts table, so a real ALTER TABLE is needed for upgrades in place.
    # Indexed access (not row["name"]) -- unlike the rest of this module,
    # init_db() isn't guaranteed to be called on a connection with
    # row_factory=sqlite3.Row set (see db.connect() vs. a bare sqlite3.connect()).
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(accounts)")}
    if "pdn_consent_at" not in existing_columns:
        conn.execute("ALTER TABLE accounts ADD COLUMN pdn_consent_at TEXT DEFAULT ''")

    tenant_columns = {row[1] for row in conn.execute("PRAGMA table_info(tenant_instances)")}
    if "telegram_chat_id" not in tenant_columns:
        conn.execute("ALTER TABLE tenant_instances ADD COLUMN telegram_chat_id TEXT DEFAULT ''")
