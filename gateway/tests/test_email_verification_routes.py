from __future__ import annotations

from unittest import mock

from fastapi import BackgroundTasks, Request

import app
from logic import accounts, db as control_db

from .base import GatewayDbTestCase


REAL_CONNECT = control_db.connect


def _request(path: str, method: str = "POST", headers=None) -> Request:
    return Request({
        "type": "http",
        "headers": headers or [],
        "method": method,
        "path": path,
        "client": ("198.51.100.10", 12345),
    })


class EmailVerificationRouteTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()

    def _app_connect(self, *args, **kwargs):
        return REAL_CONNECT(self.db_path)

    def test_registration_sends_verification_and_does_not_start_provisioning(self) -> None:
        app.auth_rate_limiter.clear()
        background = BackgroundTasks()
        with (
            mock.patch.object(app.control_db, "connect", side_effect=self._app_connect),
            mock.patch.object(app.csrf, "verify", return_value=True),
            mock.patch.object(app, "_password_policy_error", return_value=None),
            mock.patch.object(app.mailer, "send_email_verification_email", return_value=True),
        ):
            response = app.register_submit(
                _request("/register"), background, "seller@example.test", "safe-password-123", "yes", "csrf"
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/verify-email-sent")
        self.assertEqual(len(background.tasks), 1)
        with self.connect() as conn:
            account = accounts.get_account_by_email(conn, "seller@example.test")
            tenant = accounts.get_tenant_for_account(conn, int(account["id"]))
            self.assertFalse(accounts.is_email_verified(account))
            self.assertEqual(account["marketing_consent_at"], "")
            self.assertEqual(tenant["status"], "provisioning")
            self.assertIsNotNone(conn.execute("SELECT 1 FROM email_verification_tokens").fetchone())

    def test_registration_records_optional_marketing_consent_separately(self) -> None:
        app.auth_rate_limiter.clear()
        background = BackgroundTasks()
        with (
            mock.patch.object(app.control_db, "connect", side_effect=self._app_connect),
            mock.patch.object(app.csrf, "verify", return_value=True),
            mock.patch.object(app, "_password_policy_error", return_value=None),
            mock.patch.object(app.mailer, "send_email_verification_email", return_value=True),
        ):
            response = app.register_submit(
                _request("/register"), background, "optin@example.test", "safe-password-123",
                "yes", "csrf", marketing_consent="yes",
            )
        self.assertEqual(response.status_code, 303)
        with self.connect() as conn:
            account = accounts.get_account_by_email(conn, "optin@example.test")
            self.assertTrue(account["pdn_consent_at"])
            self.assertTrue(account["marketing_consent_at"])

    def test_unverified_login_is_rejected(self) -> None:
        with self.connect() as conn:
            accounts.create_account(conn, "seller@example.test", "safe-password-123", email_verified=False)
        app.auth_rate_limiter.clear()
        with (
            mock.patch.object(app.control_db, "connect", side_effect=self._app_connect),
            mock.patch.object(app.csrf, "verify", return_value=True),
        ):
            response = app.login_submit(
                _request("/login"), "seller@example.test", "safe-password-123", "", "csrf"
            )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("marketshelper_session=", response.headers.get("set-cookie", ""))

    def test_verification_consumes_token_sets_session_and_queues_provisioning(self) -> None:
        with self.connect() as conn:
            account_id = accounts.create_account(
                conn, "seller@example.test", "safe-password-123", email_verified=False
            )
            slug = accounts.generate_unique_slug(conn, "seller@example.test")
            tenant_id = accounts.create_tenant_instance(conn, account_id, slug)
            token = accounts.create_email_verification_token(conn, account_id)
        background = BackgroundTasks()
        with (
            mock.patch.object(app.control_db, "connect", side_effect=self._app_connect),
            mock.patch("provisioning.provision_tenant_background"),
        ):
            response = app.verify_email(_request("/verify-email", "GET"), background, token)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/provisioning")
        self.assertIn(f"{app.config.SESSION_COOKIE_NAME}=", response.headers.get("set-cookie", ""))
        self.assertEqual(len(background.tasks), 1)
        with self.connect() as conn:
            account = accounts.get_account_by_id(conn, account_id)
            self.assertTrue(accounts.is_email_verified(account))
            self.assertEqual(accounts.get_tenant_for_account(conn, account_id)["id"], tenant_id)

    def test_resend_verification_queues_new_token_without_enumeration(self) -> None:
        with self.connect() as conn:
            account_id = accounts.create_account(
                conn, "seller@example.test", "safe-password-123", email_verified=False
            )
            old_token = accounts.create_email_verification_token(conn, account_id)
        background = BackgroundTasks()
        with (
            mock.patch.object(app.control_db, "connect", side_effect=self._app_connect),
            mock.patch.object(app.csrf, "verify", return_value=True),
            mock.patch.object(app.verification_resend_ip_limiter, "is_allowed", return_value=True),
            mock.patch.object(app.verification_resend_email_limiter, "is_allowed", return_value=True),
        ):
            response = app.resend_verification(
                _request("/resend-verification", headers=[(b"cookie", b"wb_saas_csrf=csrf")]),
                background,
                "seller@example.test",
                "csrf",
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Если аккаунт существует", response.body.decode("utf-8"))
        self.assertEqual(len(background.tasks), 1)
        with self.connect() as conn:
            rows = conn.execute("SELECT token_hash FROM email_verification_tokens").fetchall()
            self.assertEqual(len(rows), 1)
            with self.assertRaises(ValueError):
                accounts.consume_email_verification_token(conn, old_token)

    def test_resend_verification_unknown_email_returns_same_neutral_screen(self) -> None:
        background = BackgroundTasks()
        with (
            mock.patch.object(app.control_db, "connect", side_effect=self._app_connect),
            mock.patch.object(app.csrf, "verify", return_value=True),
            mock.patch.object(app.verification_resend_ip_limiter, "is_allowed", return_value=True),
            mock.patch.object(app.verification_resend_email_limiter, "is_allowed", return_value=True),
        ):
            response = app.resend_verification(
                _request("/resend-verification", headers=[(b"cookie", b"wb_saas_csrf=csrf")]),
                background,
                "missing@example.test",
                "csrf",
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Если аккаунт существует", response.body.decode("utf-8"))
        self.assertEqual(len(background.tasks), 0)
