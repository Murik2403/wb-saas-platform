"""Exercises logic/db.py's Postgres backend (_PGConnection, _init_db_postgres)
against a REAL, disposable Postgres container -- test_db_backend.py only
covers the sqlite path plus the postgres *guard clauses* (missing
DATABASE_URL, etc.), since it has no live Postgres to connect to. That left
the actual psycopg code path -- the "?" -> "%s" translation, dict_row
results, the Postgres DDL in _PG_SCHEMA_STATEMENTS, and "RETURNING id"
against a real server rather than sqlite -- entirely unverified until now.

pytest-style (not unittest.TestCase like the rest of this directory)
because it needs the `postgres_dsn` fixture from conftest.py. Requires
Docker; self-skips via that fixture if Docker isn't reachable (e.g. a CI
runner without Docker-in-Docker) rather than failing red.
"""
from __future__ import annotations

import config
from logic import accounts, db as control_db


def test_postgres_schema_and_crud_roundtrip(postgres_dsn):
    original_backend, original_url = config.DB_BACKEND, config.DATABASE_URL
    config.DB_BACKEND, config.DATABASE_URL = "postgres", postgres_dsn
    try:
        with control_db.connect() as conn:
            control_db.init_db(conn)

        # init_db() must be idempotent -- a second call (e.g. gateway restart
        # against an already-migrated Postgres) must not error on
        # already-existing tables/columns.
        with control_db.connect() as conn:
            control_db.init_db(conn)

        with control_db.connect() as conn:
            account_id = accounts.create_account(conn, "postgres-test@example.com", "a long enough password")
            assert isinstance(account_id, int)

            tenant_id = accounts.create_tenant_instance(conn, account_id, "postgres-test-slug")
            assert isinstance(tenant_id, int)

            fetched = accounts.get_account_by_id(conn, account_id)
            # dict_row (Postgres) vs sqlite3.Row -- both support column-name
            # access, which is all any caller in this codebase relies on.
            assert fetched["email"] == "postgres-test@example.com"

            tenant = accounts.get_tenant_by_slug(conn, "postgres-test-slug")
            assert tenant["account_id"] == account_id

        # FK enforcement: deleting the account must cascade to its tenant
        # row, same as ON DELETE CASCADE does on the sqlite side.
        with control_db.connect() as conn:
            conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        with control_db.connect() as conn:
            assert accounts.get_tenant_by_slug(conn, "postgres-test-slug") is None
    finally:
        config.DB_BACKEND, config.DATABASE_URL = original_backend, original_url
