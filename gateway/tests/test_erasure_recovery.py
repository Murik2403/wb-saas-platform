import asyncio
import os
from pathlib import Path
from unittest import mock

import app
from logic import erasure_ledger, erasure_recovery
from .base import GatewayDbTestCase


class ErasureRecoveryTests(GatewayDbTestCase):
    def test_required_missing_ledger_stops_before_database_open(self):
        with mock.patch.dict(os.environ, {"WB_SAAS_ERASURE_LEDGER_REQUIRED": "1", "WB_SAAS_ERASURE_LEDGER_PATH": ""}), mock.patch.object(erasure_recovery.db, "connect") as connect:
            with self.assertRaises(erasure_ledger.LedgerUnavailable):
                erasure_recovery.check()
        connect.assert_not_called()

    def test_configured_missing_file_is_not_recreated(self):
        path = Path(self._tmpdir.name) / "missing.sqlite3"
        with mock.patch.dict(os.environ, {"WB_SAAS_ERASURE_LEDGER_PATH": str(path)}):
            self.assertEqual(erasure_recovery.main(["--check"]), 1)
        self.assertFalse(path.exists())

    def test_offline_gate_rejects_missing_database(self):
        ledger = Path(self._tmpdir.name) / "ledger.sqlite3"
        erasure_ledger.initialize(ledger)
        missing = Path(self._tmpdir.name) / "absent.sqlite3"
        with mock.patch.dict(os.environ, {"WB_SAAS_ERASURE_LEDGER_PATH": str(ledger)}):
            with self.assertRaises(erasure_ledger.LedgerUnavailable):
                erasure_recovery.check(database=missing)
        self.assertFalse(missing.exists())

    def test_asgi_lifespan_does_not_serve_when_gate_fails(self):
        async def start():
            async with app._lifespan(app.app):
                self.fail("Serving must not start")
        with mock.patch.object(app.erasure_recovery, "check", side_effect=erasure_ledger.LedgerUnavailable("blocked")):
            with self.assertRaises(erasure_ledger.LedgerUnavailable):
                asyncio.run(start())

    def test_shell_gate_runs_before_telegram_and_http(self):
        text = (Path(__file__).parents[1] / "docker-entrypoint.sh").read_text()
        self.assertLess(text.index("logic.erasure_recovery --check"), text.index("python3 telegram_bot.py"))
        self.assertLess(text.index("logic.erasure_recovery --check"), text.index("exec uvicorn"))

    def test_operator_retry_requires_prior_request_and_never_purges_new_account(self):
        from logic import accounts
        ledger = Path(self._tmpdir.name) / "ledger.sqlite3"
        erasure_ledger.initialize(ledger)
        self.init_db()
        with self.connect() as conn:
            account_id = accounts.create_account(conn, "retry@example.test", "password-for-test")
            accounts.create_tenant_instance(conn, account_id, "retry")
        purge = mock.Mock()
        with mock.patch.dict(os.environ, {"WB_SAAS_ERASURE_LEDGER_PATH": str(ledger)}):
            with self.assertRaises(ValueError):
                erasure_recovery.resume_deletion(account_id, database=self.db_path, purge=purge)
        purge.assert_not_called()

    def test_operator_retry_finishes_requested_deletion_after_restored_session_is_removed(self):
        from logic import accounts, account_lifecycle
        ledger = Path(self._tmpdir.name) / "ledger.sqlite3"
        erasure_ledger.initialize(ledger)
        self.init_db()
        with self.connect() as conn:
            account_id = accounts.create_account(conn, "retry@example.test", "password-for-test")
            accounts.create_tenant_instance(conn, account_id, "retry")
        purge = mock.Mock()
        with mock.patch.dict(os.environ, {"WB_SAAS_ERASURE_LEDGER_PATH": str(ledger)}):
            with self.connect() as conn:
                account_lifecycle.request_deletion(conn, account_id)
            result = erasure_recovery.resume_deletion(account_id, database=self.db_path, purge=purge)
        purge.assert_called_once_with("retry", purge_volume=True)
        self.assertFalse(result["archives_deleted"])
        with self.connect() as conn:
            self.assertTrue(conn.execute("SELECT deleted_at FROM accounts WHERE id=?", (account_id,)).fetchone()[0])
