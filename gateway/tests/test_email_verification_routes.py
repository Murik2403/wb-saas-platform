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
        self.original_growth_cookie_secret = app.config.GROWTH_COOKIE_SECRET

    def tearDown(self) -> None:
        app.config.GROWTH_COOKIE_SECRET = self.original_growth_cookie_secret
        super().tearDown()

    def _app_connect(self, *args, **kwargs):
        return REAL_CONNECT(self.db_path)

    def test_registration_sends_verification_and_does_not_start_provisioning(self) -> None:
        app.auth_rate_limiter.clear()
        app.config.GROWTH_COOKIE_SECRET = "test-growth-signing-key"
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
        self.assertIn("mh_registration_goal=", response.headers.get("set-cookie", ""))
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

    def test_rejected_registration_does_not_mark_signup_goal(self) -> None:
        app.auth_rate_limiter.clear()
        background = BackgroundTasks()
        with mock.patch.object(app.csrf, "verify", return_value=False):
            response = app.register_submit(
                _request("/register"), background, "seller@example.test", "safe-password-123", "yes", "csrf"
            )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("mh_registration_goal=", " ".join(response.headers.getlist("set-cookie")))

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

    def test_verification_link_renders_confirmation_without_consuming_the_token(self) -> None:
        """A mail scanner prefetching the link must not burn it.

        The GET used to consume the token, create a session and queue
        provisioning, so a corporate scanner clicking first left the real user
        with an already-used link.
        """
        with self.connect() as conn:
            account_id = accounts.create_account(
                conn, "seller@example.test", "safe-password-123", email_verified=False
            )
            token = accounts.create_email_verification_token(conn, account_id)
        with mock.patch.object(app.control_db, "connect", side_effect=self._app_connect):
            response = app.verify_email_form(_request("/verify-email", "GET"), token)

        self.assertEqual(response.status_code, 200)
        body = response.body.decode("utf-8")
        self.assertIn('method="post"', body)
        self.assertIn('name="csrf_token"', body)
        with self.connect() as conn:
            row = conn.execute("SELECT consumed_at FROM email_verification_tokens").fetchone()
            self.assertFalse(row["consumed_at"])
            self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"], 0)
            self.assertFalse(accounts.is_email_verified(accounts.get_account_by_id(conn, account_id)))

    def test_verification_submit_consumes_token_sets_session_and_queues_provisioning(self) -> None:
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
            mock.patch.object(app.csrf, "verify", return_value=True),
            mock.patch("provisioning.provision_tenant_background"),
        ):
            response = app.verify_email_submit(_request("/verify-email"), background, token, "", "csrf")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/provisioning")
        self.assertIn(f"{app.config.SESSION_COOKIE_NAME}=", response.headers.get("set-cookie", ""))
        self.assertEqual(len(background.tasks), 1)
        with self.connect() as conn:
            account = accounts.get_account_by_id(conn, account_id)
            self.assertTrue(accounts.is_email_verified(account))
            self.assertEqual(accounts.get_tenant_for_account(conn, account_id)["id"], tenant_id)

    def test_verification_submit_without_a_csrf_token_is_rejected(self) -> None:
        with self.connect() as conn:
            account_id = accounts.create_account(
                conn, "seller@example.test", "safe-password-123", email_verified=False
            )
            token = accounts.create_email_verification_token(conn, account_id)
        background = BackgroundTasks()
        with (
            mock.patch.object(app.control_db, "connect", side_effect=self._app_connect),
            mock.patch.object(app.csrf, "verify", return_value=False),
        ):
            response = app.verify_email_submit(_request("/verify-email"), background, token, "", "")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(background.tasks), 0)
        with self.connect() as conn:
            row = conn.execute("SELECT consumed_at FROM email_verification_tokens").fetchone()
            self.assertFalse(row["consumed_at"])

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
        self.assertNotIn("signup", response.body.decode("utf-8"))
        self.assertNotIn("account_created", response.body.decode("utf-8"))
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
        self.assertNotIn("signup", response.body.decode("utf-8"))
        self.assertNotIn("account_created", response.body.decode("utf-8"))
        self.assertIn("Если аккаунт существует", response.body.decode("utf-8"))
        self.assertEqual(len(background.tasks), 0)

    def test_confirmation_page_counts_registration_only_with_valid_marker_and_analytics_consent(self) -> None:
        app.config.GROWTH_COOKIE_SECRET = "test-growth-signing-key"
        marker = app.growth_web._sign({"registration": True}, "registration-goal", 300)
        consent = app.growth_web._sign({"choice": "analytics"}, "consent", 180 * 86400)
        request = _request(
            "/verify-email-sent", "GET",
            headers=[(b"cookie", f"mh_registration_goal={marker}; mh_analytics_consent={consent}".encode())],
        )
        response = app.verify_email_sent(request)
        body = response.body.decode("utf-8")
        self.assertIn('data-goals="account_created,signup"', body)
        set_cookies = response.headers.getlist("set-cookie")
        self.assertTrue(any("mh_registration_goal=" in value for value in set_cookies))
        self.assertTrue(any("Max-Age=0" in value for value in set_cookies))

    def test_confirmation_page_does_not_count_direct_visit_or_registration_without_consent(self) -> None:
        app.config.GROWTH_COOKIE_SECRET = "test-growth-signing-key"
        marker = app.growth_web._sign({"registration": True}, "registration-goal", 300)
        consent = app.growth_web._sign({"choice": "essential"}, "consent", 180 * 86400)
        requests = [
            _request("/verify-email-sent", "GET"),
            _request("/verify-email-sent", "GET", headers=[(b"cookie", f"mh_registration_goal={marker}; mh_analytics_consent={consent}".encode())]),
        ]
        for request in requests:
            response = app.verify_email_sent(request)
            body = response.body.decode("utf-8")
            self.assertNotIn("signup", body)
            self.assertNotIn("account_created", body)

        tampered = _request(
            "/verify-email-sent", "GET",
            headers=[(b"cookie", b"mh_registration_goal=invalid.signature")],
        )
        response = app.verify_email_sent(tampered)
        self.assertNotIn("signup", response.body.decode("utf-8"))
