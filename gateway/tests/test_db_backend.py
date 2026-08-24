from __future__ import annotations

import config
from logic import accounts, db as control_db

from .base import GatewayDbTestCase


class PlaceholderTranslationTests(GatewayDbTestCase):
    """The '?' -> '%s' translation is what lets every caller's SQL run
    unchanged on both sqlite3 and psycopg -- see db.py's module docstring."""

    def test_translates_every_placeholder(self) -> None:
        sql = "SELECT * FROM accounts WHERE email=? AND status=?"
        self.assertEqual(
            control_db._translate_placeholders(sql),
            "SELECT * FROM accounts WHERE email=%s AND status=%s",
        )

    def test_leaves_sql_without_placeholders_untouched(self) -> None:
        sql = "SELECT 1 FROM information_schema.columns"
        self.assertEqual(control_db._translate_placeholders(sql), sql)


class PostgresBackendGuardTests(GatewayDbTestCase):
    """Without a live Postgres to test against, these confirm the backend
    switch fails loudly and immediately rather than silently touching the
    sqlite file -- see DEPLOY.md's "Switching the control-plane db to
    Postgres" section for the actual cutover steps."""

    def setUp(self) -> None:
        super().setUp()
        self._orig_backend = config.DB_BACKEND
        self._orig_url = config.DATABASE_URL

    def tearDown(self) -> None:
        config.DB_BACKEND = self._orig_backend
        config.DATABASE_URL = self._orig_url
        super().tearDown()

    def test_missing_database_url_raises(self) -> None:
        config.DB_BACKEND = "postgres"
        config.DATABASE_URL = ""
        with self.assertRaises(RuntimeError):
            with control_db.connect(self.db_path):
                pass

    def test_sqlite_backend_unaffected_by_postgres_settings_when_not_selected(self) -> None:
        # Sanity check: merely having DATABASE_URL set (e.g. left over in
        # .env) must not switch anything -- only DB_BACKEND does.
        config.DB_BACKEND = "sqlite"
        config.DATABASE_URL = "postgresql://unused/for-this-test"
        self.init_db()
        with self.connect() as conn:
            control_db.init_db(conn)
        with self.connect() as conn:
            account_id = accounts.create_account(conn, "user@example.com", "a long enough password")
        self.assertIsInstance(account_id, int)


class ReturningIdTests(GatewayDbTestCase):
    """create_account/create_tenant_instance get their new row's id via
    'RETURNING id' + fetchone()[0] rather than cursor.lastrowid, since
    RETURNING works identically on sqlite (3.35+) and Postgres while
    lastrowid does not exist on psycopg -- this pins that contract."""

    def test_create_account_returns_new_int_id(self) -> None:
        self.init_db()
        with self.connect() as conn:
            account_id = accounts.create_account(conn, "a@example.com", "a long enough password")
        self.assertIsInstance(account_id, int)
        self.assertGreater(account_id, 0)

    def test_create_tenant_instance_returns_new_int_id(self) -> None:
        self.init_db()
        with self.connect() as conn:
            account_id = accounts.create_account(conn, "b@example.com", "a long enough password")
            tenant_id = accounts.create_tenant_instance(conn, account_id, "b-slug")
        self.assertIsInstance(tenant_id, int)
        self.assertGreater(tenant_id, 0)
