"""Synthetic restore/rollback and independent-ledger failure acceptance."""
import os
from pathlib import Path
import sqlite3
from unittest import mock
from contextlib import closing

from logic import accounts, account_lifecycle as lifecycle, erasure_ledger as ledger
from .base import GatewayDbTestCase


class ErasureLedgerTests(GatewayDbTestCase):
    def setUp(self):
        super().setUp()
        self.ledger_path = Path(self._tmpdir.name) / "independent" / "erasures.sqlite3"
        self.ledger_path.parent.mkdir()
        ledger.initialize(self.ledger_path)
        self.environment = mock.patch.dict(os.environ, {"WB_SAAS_ERASURE_LEDGER_PATH": str(self.ledger_path)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "restore@example.test", "synthetic-password-123", email_verified=True)
            accounts.set_account_status(conn, self.account_id, "active")
            self.session = accounts.create_session(conn, self.account_id)
            self.original = dict(accounts.get_account_by_id(conn, self.account_id))

    def test_restore_of_pre_deletion_snapshot_never_restores_login_or_session(self):
        snapshot = Path(self._tmpdir.name) / "before.sqlite3"
        with self.connect() as source, closing(sqlite3.connect(snapshot)) as target:
            source.backup(target)
        with self.connect() as conn:
            self.assertEqual(lifecycle.request_deletion(conn, self.account_id), "ready")
            accounts.delete_account(conn, self.account_id)
        # Separate ledger survives replacement with an old gateway database.
        with closing(sqlite3.connect(snapshot)) as source, self.connect() as target:
            source.backup(target)
        with self.connect() as conn:
            restored = accounts.get_account_by_id(conn, self.account_id)
            self.assertEqual(restored["email"], self.original["email"])
            self.assertFalse(accounts.account_has_access(restored))
            self.assertIsNone(accounts.authenticate_account(conn, self.original["email"], "synthetic-password-123"))
            self.assertIsNone(accounts.resolve_session(conn, self.session))
            with self.assertRaises(ValueError):
                accounts.create_session(conn, self.account_id)
            with self.assertRaises(lifecycle.LifecycleConflict):
                lifecycle.begin_payment(conn, self.account_id, "must-not-charge")
            self.assertEqual(ledger.reconcile(conn), 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)
            self.assertEqual(accounts.get_account_by_id(conn, self.account_id)["status"], "canceled")

    def test_ledger_survives_gateway_transaction_rollback(self):
        with self.assertRaisesRegex(RuntimeError, "synthetic rollback"):
            with self.connect() as conn:
                lifecycle.request_deletion(conn, self.account_id)
                raise RuntimeError("synthetic rollback")
        with self.connect() as conn:
            restored = accounts.get_account_by_id(conn, self.account_id)
            self.assertEqual(restored["deletion_requested_at"], "")
            self.assertTrue(ledger.is_erased(restored))
            self.assertFalse(accounts.account_has_access(restored))
            # Retries are idempotent at lifecycle level, even after the guard.
            self.assertEqual(lifecycle.request_deletion(conn, self.account_id), "ready")

    def test_account_id_reuse_with_different_creation_date_is_not_erased(self):
        ledger.record(self.original)
        reused = {**self.original, "created_at": "2027-01-01T00:00:00+00:00"}
        self.assertFalse(ledger.is_erased(reused))
        self.assertTrue(accounts.account_has_access(reused))

    def test_missing_ledger_never_silently_recreates_and_blocks_mutation(self):
        self.ledger_path.unlink()
        with self.assertRaises(ledger.LedgerUnavailable):
            ledger.validate_configured()
        self.assertFalse(accounts.account_has_access(self.original))
        with self.connect() as conn:
            with self.assertRaises(lifecycle.LifecycleConflict):
                lifecycle.request_deletion(conn, self.account_id)
            self.assertEqual(accounts.get_account_by_id(conn, self.account_id)["deletion_requested_at"], "")
        self.assertFalse(self.ledger_path.exists())

    def test_corrupt_ledger_blocks_all_accounts_and_bootstrap_does_not_reset_it(self):
        self.ledger_path.write_bytes(b"synthetic corruption")
        with self.assertRaises(ledger.LedgerUnavailable):
            ledger.initialize(self.ledger_path)
        self.assertEqual(self.ledger_path.read_bytes(), b"synthetic corruption")
        self.assertFalse(accounts.account_has_access(self.original))

    def test_wrong_schema_and_malformed_rows_fail_closed(self):
        with closing(sqlite3.connect(self.ledger_path)) as conn, conn:
            conn.execute("INSERT INTO erasures VALUES (?, ?, '')", ("g" * 64, "not-a-time"))
        with self.assertRaises(ledger.LedgerUnavailable):
            ledger.validate_configured()
        self.assertFalse(accounts.account_has_access(self.original))

    def test_ledger_stores_no_email_or_chat_or_plain_identity(self):
        ledger.record(self.original)
        with closing(sqlite3.connect(self.ledger_path)) as conn:
            rows = conn.execute("SELECT * FROM erasures").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], ledger.identity_key(self.original))
        self.assertNotIn(self.original["email"], self.ledger_path.read_bytes().decode("latin1"))

    def test_finalization_marker_is_monotonic_and_request_retry_preserves_timestamp(self):
        ledger.record(self.original)
        ledger.record(self.original, finalized=True)
        with closing(sqlite3.connect(self.ledger_path)) as conn:
            before = conn.execute("SELECT * FROM erasures").fetchone()
        ledger.record(self.original)
        with closing(sqlite3.connect(self.ledger_path)) as conn:
            self.assertEqual(conn.execute("SELECT * FROM erasures").fetchone(), before)
        self.assertTrue(before[1] and before[2])

    def test_ledger_cannot_be_inside_gateway_snapshot_or_relative(self):
        from logic import db
        for value in ("relative.sqlite3", str(db.DEFAULT_DB_PATH.parent / "erasures.sqlite3")):
            with self.subTest(path=value), mock.patch.dict(os.environ, {"WB_SAAS_ERASURE_LEDGER_PATH": value}):
                with self.assertRaises(ledger.LedgerUnavailable):
                    ledger.validate_configured()

    def test_no_ledger_configuration_preserves_legacy_behavior(self):
        with mock.patch.dict(os.environ, {"WB_SAAS_ERASURE_LEDGER_PATH": "", "WB_SAAS_ERASURE_LEDGER_REQUIRED": "0"}):
            ledger.validate_configured()
            self.assertFalse(ledger.is_erased(self.original))
            self.assertTrue(accounts.account_has_access(self.original))

    def test_required_missing_configuration_denies_instead_of_legacy_mode(self):
        with mock.patch.dict(os.environ, {"WB_SAAS_ERASURE_LEDGER_PATH": "", "WB_SAAS_ERASURE_LEDGER_REQUIRED": "1"}):
            with self.assertRaises(ledger.LedgerUnavailable):
                ledger.validate_configured()
            self.assertFalse(accounts.account_has_access(self.original))

    def test_seed_pre_upgrade_deletion_blocks_original_snapshot_identity(self):
        with mock.patch.dict(os.environ, {"WB_SAAS_ERASURE_LEDGER_PATH": "", "WB_SAAS_ERASURE_LEDGER_REQUIRED": "0"}):
            with self.connect() as conn:
                lifecycle.request_deletion(conn, self.account_id)
                accounts.delete_account(conn, self.account_id)
        self.assertFalse(ledger.is_erased(self.original))
        with self.connect() as conn:
            self.assertEqual(ledger.seed_existing_deletions(conn), 1)
            self.assertEqual(ledger.seed_existing_deletions(conn), 1)
            expected = accounts.get_account_by_id(conn, self.account_id, include_deleted=True)["deletion_requested_at"]
        with closing(sqlite3.connect(self.ledger_path)) as conn:
            self.assertEqual(conn.execute("SELECT requested_at FROM erasures").fetchone()[0], expected)
        self.assertTrue(ledger.is_erased(self.original))

    def test_missing_identity_is_denied_when_enabled(self):
        with self.assertRaises(ledger.LedgerUnavailable):
            ledger.identity_key({"id": self.account_id})
        self.assertFalse(accounts.account_has_access({"id": self.account_id, "status": "active"}))

    def test_deletion_retry_session_is_narrow_and_does_not_restore_regular_access(self):
        with self.connect() as conn:
            self.assertIsNone(accounts.resolve_deletion_session(conn, self.session))
            lifecycle.request_deletion(conn, self.account_id)
            self.assertIsNone(accounts.resolve_session(conn, self.session))
            retry = accounts.resolve_deletion_session(conn, self.session)
            self.assertEqual(retry["id"], self.account_id)
            self.assertFalse(accounts.account_has_access(retry))
            self.assertIsNone(accounts.authenticate_account(conn, self.original["email"], "synthetic-password-123"))
            accounts.delete_account(conn, self.account_id)
            self.assertIsNone(accounts.resolve_deletion_session(conn, self.session))

    def test_deletion_retry_rejects_expired_session_and_unavailable_ledger(self):
        with self.connect() as conn:
            lifecycle.request_deletion(conn, self.account_id)
            conn.execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00+00:00'")
            self.assertIsNone(accounts.resolve_deletion_session(conn, self.session))
            conn.execute("UPDATE sessions SET expires_at='2099-01-01T00:00:00+00:00'")
            self.ledger_path.unlink()
            self.assertIsNone(accounts.resolve_deletion_session(conn, self.session))
