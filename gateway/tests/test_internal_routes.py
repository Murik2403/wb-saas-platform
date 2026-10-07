"""Tests for internal_routes.py: the shared-secret-authenticated endpoint
tenant containers call to have a scheduled report emailed to the account
owner. HTTP wiring (Form/UploadFile extraction) isn't exercised here --
this codebase's convention is to test route logic directly rather than via
a FastAPI TestClient (see test_billing_routes_webhook.py's docstring) --
so this hits _secret_is_valid() and deliver_report_email() as plain
functions.
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

import config  # noqa: E402
import internal_routes  # noqa: E402
import mailer  # noqa: E402
import telegram_bot  # noqa: E402
from logic import accounts, erasure_ledger  # noqa: E402

from .base import GatewayDbTestCase  # noqa: E402


class SecretIsValidTests(unittest.TestCase):
    def test_correct_secret_is_valid(self) -> None:
        with mock.patch.object(config, "INTERNAL_API_SECRET", "s3cr3t"):
            from internal_auth import tenant_credential
            self.assertTrue(internal_routes._secret_is_valid(tenant_credential("s3cr3t", "shop"), "shop"))

    def test_wrong_secret_is_invalid(self) -> None:
        with mock.patch.object(config, "INTERNAL_API_SECRET", "s3cr3t"):
            self.assertFalse(internal_routes._secret_is_valid("wrong"))

    def test_secret_for_other_tenant_is_invalid(self) -> None:
        from internal_auth import tenant_credential

        with mock.patch.object(config, "INTERNAL_API_SECRET", "s3cr3t"):
            other_tenant_secret = tenant_credential("s3cr3t", "shop-b")
            self.assertFalse(internal_routes._secret_is_valid(other_tenant_secret, "shop-a"))

    def test_empty_configured_secret_rejects_everything(self) -> None:
        # An unconfigured deployment (INTERNAL_API_SECRET="") must not
        # accept an empty header as "matching" -- that would let anyone in.
        with mock.patch.object(config, "INTERNAL_API_SECRET", ""):
            self.assertFalse(internal_routes._secret_is_valid(""))
            self.assertFalse(internal_routes._secret_is_valid("anything"))


class DeliverReportEmailTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "owner@shop.ru", "password1234")
            self.slug = accounts.generate_unique_slug(conn, "owner@shop.ru")
            accounts.create_tenant_instance(conn, self.account_id, self.slug)

    def test_unknown_slug_raises_404(self) -> None:
        with mock.patch.object(internal_routes, "control_db", mock.Mock(connect=self.connect)):
            with self.assertRaises(HTTPException) as ctx:
                internal_routes.deliver_report_email("no-such-slug", "Отчёт", b"%PDF-1.4...", "report.pdf")
            self.assertEqual(ctx.exception.status_code, 404)

    def test_secret_for_other_tenant_is_rejected(self) -> None:
        from internal_auth import tenant_credential

        class Upload:
            filename = "report.pdf"
            read_was_called = False

            async def read(self, size: int = -1) -> bytes:
                self.read_was_called = True
                return b""

        upload = Upload()
        with mock.patch.object(internal_routes, "control_db", mock.Mock(connect=self.connect)), \
             mock.patch.object(config, "INTERNAL_API_SECRET", "s3cr3t"):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(internal_routes.send_report_email(
                    SimpleNamespace(headers={"x-internal-secret": tenant_credential("s3cr3t", "shop-b")}),
                    "shop-a",
                    "Отчёт",
                    upload,
                ))
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertFalse(upload.read_was_called)

    def test_known_slug_emails_the_account_owner(self) -> None:
        captured = {}

        def _fake_send(to_email, report_name, pdf_bytes, filename):
            captured["to"] = to_email
            captured["report_name"] = report_name
            captured["pdf_bytes"] = pdf_bytes
            captured["filename"] = filename
            return True

        with mock.patch.object(internal_routes, "control_db", mock.Mock(connect=self.connect)), \
             mock.patch.object(mailer, "send_report_email", _fake_send):
            internal_routes.deliver_report_email(self.slug, "Еженедельная сводка", b"%PDF-1.4...", "report.pdf")

        self.assertEqual(captured["to"], "owner@shop.ru")
        self.assertEqual(captured["report_name"], "Еженедельная сводка")
        self.assertEqual(captured["filename"], "report.pdf")

    def test_mailer_failure_raises_502(self) -> None:
        with mock.patch.object(internal_routes, "control_db", mock.Mock(connect=self.connect)), \
             mock.patch.object(mailer, "send_report_email", lambda *a, **k: False):
            with self.assertRaises(HTTPException) as ctx:
                internal_routes.deliver_report_email(self.slug, "Отчёт", b"%PDF-1.4...", "report.pdf")
            self.assertEqual(ctx.exception.status_code, 502)

    def test_oversized_email_report_is_rejected_before_delivery(self) -> None:
        class Upload:
            filename = "report.pdf"

            def __init__(self) -> None:
                self.read_sizes: list[int] = []

            async def read(self, size: int = -1) -> bytes:
                self.read_sizes.append(size)
                return b"x" * (internal_routes.MAX_REPORT_UPLOAD_BYTES + 1)

        upload = Upload()
        with mock.patch.object(internal_routes, "_secret_is_valid", return_value=True), \
             mock.patch.object(internal_routes, "deliver_report_email") as deliver:
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(internal_routes.send_report_email(
                    SimpleNamespace(headers={}), self.slug, "Отчёт", upload
                ))
        self.assertEqual(ctx.exception.status_code, 413)
        self.assertEqual(upload.read_sizes, [internal_routes.MAX_REPORT_UPLOAD_BYTES + 1])
        deliver.assert_not_called()


class RestoredErasedDeliveryTests(GatewayDbTestCase):
    """Active SQL rows from an old snapshot must not bypass the independent guard."""

    def setUp(self):
        super().setUp()
        self.init_db()
        ledger_path = Path(self._tmpdir.name) / "independent-erasures.sqlite3"
        erasure_ledger.initialize(ledger_path)
        self.environment = mock.patch.dict(os.environ, {
            "WB_SAAS_ERASURE_LEDGER_PATH": str(ledger_path),
            "WB_SAAS_ERASURE_LEDGER_REQUIRED": "1",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "restored@synthetic.invalid", "synthetic-password-123")
            accounts.set_account_status(conn, self.account_id, "active")
            self.slug = "restored-synthetic"
            tenant_id = accounts.create_tenant_instance(conn, self.account_id, self.slug)
            accounts.set_tenant_status(conn, tenant_id, "running")
            conn.execute("UPDATE tenant_instances SET telegram_chat_id='synthetic-chat' WHERE id=?", (tenant_id,))
            original = dict(accounts.get_account_by_id(conn, self.account_id))
        # Only the independent ledger changes; SQL represents the pre-deletion snapshot.
        erasure_ledger.record(original)

    def test_erased_owner_cannot_receive_email_even_with_active_snapshot_rows(self):
        with self.connect() as conn:
            row = accounts.get_account_by_id(conn, self.account_id)
            self.assertEqual(row["deletion_requested_at"], "")
            self.assertEqual(row["status"], "active")
        with mock.patch.object(internal_routes, "control_db", mock.Mock(connect=self.connect)), \
             mock.patch.object(mailer, "send_report_email") as send:
            with self.assertRaises(HTTPException) as error:
                internal_routes.deliver_report_email(self.slug, "Synthetic", b"%PDF-1.4", "synthetic.pdf")
            self.assertEqual(error.exception.status_code, 404)
        send.assert_not_called()

    def test_erased_owner_cannot_receive_telegram_document_or_text(self):
        with mock.patch.object(internal_routes, "control_db", mock.Mock(connect=self.connect)), \
             mock.patch.object(telegram_bot, "send_document") as document, \
             mock.patch.object(telegram_bot, "send_message") as message:
            with self.assertRaises(HTTPException) as pdf_error:
                internal_routes.deliver_report_telegram(self.slug, "Synthetic", b"%PDF-1.4", "synthetic.pdf")
            self.assertEqual(pdf_error.exception.status_code, 409)
            with self.assertRaises(HTTPException) as text_error:
                internal_routes.deliver_text_telegram(self.slug, "Synthetic")
            self.assertEqual(text_error.exception.status_code, 409)
        document.assert_not_called()
        message.assert_not_called()


class TelegramLinkCodeTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "owner@shop.ru", "password1234")
            self.slug = accounts.generate_unique_slug(conn, "owner@shop.ru")
            accounts.create_tenant_instance(conn, self.account_id, self.slug)

    def test_create_telegram_link_code_unknown_slug_raises_404(self) -> None:
        with mock.patch.object(internal_routes, "control_db", mock.Mock(connect=self.connect)):
            with self.assertRaises(HTTPException) as ctx:
                internal_routes.create_telegram_link_code("no-such-slug")
            self.assertEqual(ctx.exception.status_code, 404)

    def test_create_telegram_link_code_returns_usable_code(self) -> None:
        with mock.patch.object(internal_routes, "control_db", mock.Mock(connect=self.connect)):
            code = internal_routes.create_telegram_link_code(self.slug,str(self.account_id))
        self.assertTrue(code)
        with self.connect() as conn:
            ok = accounts.consume_telegram_link_code(conn, code, "555")
        self.assertTrue(ok)


class TelegramIsLinkedTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "owner@shop.ru", "password1234")
            self.slug = accounts.generate_unique_slug(conn, "owner@shop.ru")
            accounts.create_tenant_instance(conn, self.account_id, self.slug)

    def test_false_before_linking(self) -> None:
        with mock.patch.object(internal_routes, "control_db", mock.Mock(connect=self.connect)):
            self.assertFalse(internal_routes.telegram_is_linked(self.slug))

    def test_true_after_linking(self) -> None:
        with self.connect() as conn:
            code = accounts.create_telegram_link_code(conn, self.account_id)
            accounts.consume_telegram_link_code(conn, code, "555")
        with mock.patch.object(internal_routes, "control_db", mock.Mock(connect=self.connect)):
            self.assertTrue(internal_routes.telegram_is_linked(self.slug))


class DeliverReportTelegramTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "owner@shop.ru", "password1234")
            self.slug = accounts.generate_unique_slug(conn, "owner@shop.ru")
            accounts.create_tenant_instance(conn, self.account_id, self.slug)

    def test_not_linked_raises_409(self) -> None:
        with mock.patch.object(internal_routes, "control_db", mock.Mock(connect=self.connect)):
            with self.assertRaises(HTTPException) as ctx:
                internal_routes.deliver_report_telegram(self.slug, "Отчёт", b"%PDF-1.4...", "report.pdf")
            self.assertEqual(ctx.exception.status_code, 409)

    def test_linked_sends_document_to_the_right_chat(self) -> None:
        with self.connect() as conn:
            code = accounts.create_telegram_link_code(conn, self.account_id)
            accounts.consume_telegram_link_code(conn, code, "555")

        captured = {}

        def fake_send_document(chat_id, file_bytes, filename, caption=""):
            captured["chat_id"] = chat_id
            captured["file_bytes"] = file_bytes
            captured["filename"] = filename
            captured["caption"] = caption
            return {"ok": True, "result": {"message_id": 42}}

        with mock.patch.object(internal_routes, "control_db", mock.Mock(connect=self.connect)), \
             mock.patch.object(telegram_bot, "send_document", fake_send_document):
            internal_routes.deliver_report_telegram(self.slug, "Отчёт", b"%PDF-1.4...", "report.pdf")

        self.assertEqual(captured["chat_id"], "555")
        self.assertEqual(captured["filename"], "report.pdf")
        self.assertIn("Отчёт", captured["caption"])

    def test_telegram_http_200_with_api_error_raises_502(self) -> None:
        with self.connect() as conn:
            code = accounts.create_telegram_link_code(conn, self.account_id)
            accounts.consume_telegram_link_code(conn, code, "555")

        with mock.patch.object(internal_routes, "control_db", mock.Mock(connect=self.connect)), \
             mock.patch.object(telegram_bot, "send_document", return_value={"ok": False, "error_code": 400, "description": "chat not found"}):
            with self.assertRaises(HTTPException) as ctx:
                internal_routes.deliver_report_telegram(self.slug, "Отчёт", b"%PDF-1.4...", "report.pdf")
            self.assertEqual(ctx.exception.status_code, 502)

    def test_telegram_send_failure_raises_502(self) -> None:
        with self.connect() as conn:
            code = accounts.create_telegram_link_code(conn, self.account_id)
            accounts.consume_telegram_link_code(conn, code, "555")

        def raise_error(*a, **k):
            raise RuntimeError("telegram API error")

        with mock.patch.object(internal_routes, "control_db", mock.Mock(connect=self.connect)), \
             mock.patch.object(telegram_bot, "send_document", raise_error):
            with self.assertRaises(HTTPException) as ctx:
                internal_routes.deliver_report_telegram(self.slug, "Отчёт", b"%PDF-1.4...", "report.pdf")
            self.assertEqual(ctx.exception.status_code, 502)

    def test_oversized_telegram_report_is_rejected_before_delivery(self) -> None:
        class Upload:
            filename = "report.pdf"

            def __init__(self) -> None:
                self.read_sizes: list[int] = []

            async def read(self, size: int = -1) -> bytes:
                self.read_sizes.append(size)
                return b"x" * (internal_routes.MAX_REPORT_UPLOAD_BYTES + 1)

        upload = Upload()
        with mock.patch.object(internal_routes, "_secret_is_valid", return_value=True), \
             mock.patch.object(internal_routes, "deliver_report_telegram") as deliver:
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(internal_routes.send_report_telegram(
                    SimpleNamespace(headers={}), self.slug, "Отчёт", upload
                ))
        self.assertEqual(ctx.exception.status_code, 413)
        self.assertEqual(upload.read_sizes, [internal_routes.MAX_REPORT_UPLOAD_BYTES + 1])
        deliver.assert_not_called()


if __name__ == "__main__":
    unittest.main()
