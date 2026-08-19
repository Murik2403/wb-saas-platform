"""Unit tests for mailer.py -- only the parts that don't need a real SMTP
server: message construction, and the "no SMTP configured" fallback path
(which must never raise, since a mail outage must not turn into a 500 for
someone requesting a password reset -- see logic/password_reset.py).

Actually connecting to smtplib.SMTP is not exercised here (no network in
this sandbox) -- verify on a real host with real SMTP credentials before
relying on password reset emails in production, see DEPLOY.md.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

import config  # noqa: E402
import mailer  # noqa: E402


class BuildMessageTests(unittest.TestCase):
    def test_message_has_expected_headers_and_body(self) -> None:
        msg = mailer.build_message("client@example.com", "Subject line", "Body text")
        self.assertEqual(msg["To"], "client@example.com")
        self.assertEqual(msg["Subject"], "Subject line")
        self.assertEqual(msg["From"], config.SMTP_FROM_EMAIL)
        self.assertEqual(msg.get_content().strip(), "Body text")


class SendEmailWithoutSmtpConfiguredTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_host = config.SMTP_HOST
        config.SMTP_HOST = ""  # force the "not configured" fallback path

    def tearDown(self) -> None:
        config.SMTP_HOST = self._original_host

    def test_returns_true_and_does_not_raise_when_smtp_not_configured(self) -> None:
        ok = mailer.send_email("client@example.com", "Subject", "Body")
        self.assertTrue(ok)


class SendPasswordResetEmailTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_host = config.SMTP_HOST
        config.SMTP_HOST = ""

    def tearDown(self) -> None:
        config.SMTP_HOST = self._original_host

    def test_includes_reset_url_and_ttl_in_the_body(self) -> None:
        captured = {}
        original_send = mailer.send_email

        def _capture(to_email, subject, body_text):
            captured["to"] = to_email
            captured["subject"] = subject
            captured["body"] = body_text
            return True

        mailer.send_email = _capture
        try:
            ok = mailer.send_password_reset_email("client@example.com", "https://wbsaas.ru/reset-password?token=abc", 60)
        finally:
            mailer.send_email = original_send
        self.assertTrue(ok)
        self.assertEqual(captured["to"], "client@example.com")
        self.assertIn("https://wbsaas.ru/reset-password?token=abc", captured["body"])
        self.assertIn("60", captured["body"])


if __name__ == "__main__":
    unittest.main()
