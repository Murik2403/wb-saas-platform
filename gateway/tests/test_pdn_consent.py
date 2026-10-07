"""The personal-data consent is its own document and its own checkbox, separate from the public offer."""
from __future__ import annotations

import re
from unittest import mock

from fastapi import BackgroundTasks, Request
from fastapi.testclient import TestClient

import app
from logic import accounts, db as control_db

from .base import GatewayDbTestCase

REAL_CONNECT = control_db.connect


def _request(path: str = "/register") -> Request:
    return Request({"type": "http", "headers": [], "method": "POST", "path": path, "client": ("198.51.100.20", 12345)})


class ConsentPageTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        self.client = TestClient(app.app)

    def test_consent_page_is_public_and_names_operator_purposes_term_and_withdrawal(self) -> None:
        response = self.client.get("/consent")
        self.assertEqual(response.status_code, 200)
        text = re.sub(r"<[^>]+>", " ", response.text)
        for expected in ("071513433550", "Для чего обрабатываются данные", "Какие данные обрабатываются",
                         "Срок действия согласия", "Как отозвать согласие", accounts.PDN_POLICY_VERSION):
            self.assertIn(expected, text)

    def test_register_form_has_separate_offer_and_consent_checkboxes(self) -> None:
        html = self.client.get("/register").text
        offer = re.search(r'<label[^>]*>\s*<input[^>]*name="offer_accept"[^>]*>(.*?)</label>', html, re.S)
        consent = re.search(r'<label[^>]*>\s*<input[^>]*name="pdn_consent"[^>]*>(.*?)</label>', html, re.S)
        self.assertIsNotNone(offer)
        self.assertIsNotNone(consent)
        self.assertIn("required", html[html.index('name="offer_accept"') - 80:html.index('name="offer_accept"') + 80])
        self.assertIn('href="/offer"', offer.group(1))
        self.assertNotIn("персональных данных", offer.group(1))
        self.assertIn('href="/consent"', consent.group(1))
        self.assertNotIn("оферт", consent.group(1).replace("отдельно от оферты", ""))

    def test_policy_and_offer_no_longer_treat_use_of_the_service_as_consent(self) -> None:
        privacy = re.sub(r"<[^>]+>", " ", self.client.get("/privacy").text)
        offer = re.sub(r"<[^>]+>", " ", self.client.get("/offer").text)
        self.assertNotIn("Используя Сервис и/или отмечая согласие", privacy)
        self.assertNotIn("Регистрируясь в Сервисе, Пользователь даёт согласие", offer)
        self.assertIn('href="/consent"', self.client.get("/privacy").text)
        self.assertIn('href="/consent"', self.client.get("/offer").text)


class RegistrationConsentTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()

    def _connect(self, *args, **kwargs):
        return REAL_CONNECT(self.db_path)

    def _register(self, email: str, *, pdn_consent, offer_accept):
        app.auth_rate_limiter.clear()
        with (
            mock.patch.object(app.control_db, "connect", side_effect=self._connect),
            mock.patch.object(app.csrf, "verify", return_value=True),
            mock.patch.object(app, "_password_policy_error", return_value=None),
            mock.patch.object(app.mailer, "send_email_verification_email", return_value=True),
        ):
            return app.register_submit(
                _request(), BackgroundTasks(), email, "safe-password-123", pdn_consent, "csrf", offer_accept=offer_accept
            )

    def _account(self, email: str):
        with self.connect() as conn:
            return accounts.get_account_by_email(conn, email)

    def test_registration_without_offer_acceptance_is_rejected_and_creates_nothing(self) -> None:
        response = self._register("nooffer@example.test", pdn_consent="yes", offer_accept=None)
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self._account("nooffer@example.test"))

    def test_registration_without_personal_data_consent_is_rejected_and_creates_nothing(self) -> None:
        response = self._register("noconsent@example.test", pdn_consent=None, offer_accept="on")
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self._account("noconsent@example.test"))

    def test_registration_with_both_checkboxes_records_consent_and_version(self) -> None:
        response = self._register("both@example.test", pdn_consent="on", offer_accept="on")
        self.assertEqual(response.status_code, 303)
        account = self._account("both@example.test")
        self.assertTrue(account["pdn_consent_at"])
        self.assertEqual(account["pdn_policy_version"], accounts.PDN_POLICY_VERSION)
