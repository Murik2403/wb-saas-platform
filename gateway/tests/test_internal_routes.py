"""Tests for internal_routes.py: the shared-secret-authenticated endpoint
tenant containers call to have a scheduled report emailed to the account
owner. HTTP wiring (Form/UploadFile extraction) isn't exercised here --
this codebase's convention is to test route logic directly rather than via
a FastAPI TestClient (see test_billing_routes_webhook.py's docstring) --
so this hits _secret_is_valid() and deliver_report_email() as plain
functions.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

import config  # noqa: E402
import internal_routes  # noqa: E402
import mailer  # noqa: E402
import telegram_bot  # noqa: E402
from logic import accounts  # noqa: E402

from .base import GatewayDbTestCase  # noqa: E402


class SecretIsValidTests(unittest.TestCase):
    def test_correct_secret_is_valid(self) -> None:
        with mock.patch.object(config, "INTERNAL_API_SECRET", "s3cr3t"):
            self.assertTrue(internal_routes._secret_is_valid("s3cr3t"))

    def test_wrong_secret_is_invalid(self) -> None:
        with mock.patch.object(config, "INTERNAL_API_SECRET", "s3cr3t"):
            self.assertFalse(internal_routes._secret_is_valid("wrong"))

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
            code = internal_routes.create_telegram_link_code(self.slug)
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
            return {}

        with mock.patch.object(internal_routes, "control_db", mock.Mock(connect=self.connect)), \
             mock.patch.object(telegram_bot, "send_document", fake_send_document):
            internal_routes.deliver_report_telegram(self.slug, "Отчёт", b"%PDF-1.4...", "report.pdf")

        self.assertEqual(captured["chat_id"], "555")
        self.assertEqual(captured["filename"], "report.pdf")
        self.assertIn("Отчёт", captured["caption"])

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


if __name__ == "__main__":
    unittest.main()
