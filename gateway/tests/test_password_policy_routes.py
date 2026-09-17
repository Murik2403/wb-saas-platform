"""Route-level regression tests for compromised-password screening."""
from __future__ import annotations

from unittest import mock

from fastapi import BackgroundTasks, Request

import app
import password_breach
from logic import accounts, db as control_db, password_reset

from .base import GatewayDbTestCase


REAL_CONNECT = control_db.connect


def _request() -> Request:
    return Request({
        "type": "http",
        "headers": [],
        "method": "POST",
        "path": "/register",
        "client": ("198.51.100.10", 12345),
    })


class RegistrationPasswordPolicyTests(GatewayDbTestCase):
    def test_common_password_rejected_before_remote_check(self) -> None:
        app.auth_rate_limiter.clear()
        with (
            mock.patch.object(app.csrf, "verify", return_value=True),
            mock.patch.object(password_breach, "pwned_password_count") as pwned,
            mock.patch.object(app.accounts, "create_account") as create_account,
            mock.patch.object(app.accounts, "generate_unique_slug", return_value="seller"),
            mock.patch.object(app.accounts, "create_tenant_instance", return_value=1),
            mock.patch.object(app.accounts, "create_session", return_value="session"),
        ):
            response = app.register_submit(
                _request(), BackgroundTasks(), "seller@example.test", "password", "yes", "csrf"
            )
        self.assertEqual(response.status_code, 400)
        create_account.assert_not_called()
        pwned.assert_not_called()

    def test_compromised_password_rejects_before_account_creation(self) -> None:
        app.auth_rate_limiter.clear()
        with (
            mock.patch.object(app.csrf, "verify", return_value=True),
            mock.patch.object(password_breach, "pwned_password_count", return_value=4),
            mock.patch.object(app.accounts, "create_account") as create_account,
            mock.patch.object(app.accounts, "generate_unique_slug", return_value="seller"),
            mock.patch.object(app.accounts, "create_tenant_instance", return_value=1),
            mock.patch.object(app.accounts, "create_session", return_value="session"),
        ):
            response = app.register_submit(
                _request(), BackgroundTasks(), "seller@example.test", "safe-enough-password", "yes", "csrf"
            )
        self.assertEqual(response.status_code, 400)
        create_account.assert_not_called()


class ResetPasswordPolicyTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            account_id = accounts.create_account(conn, "seller@example.test", "old-password")
            self.token = password_reset.create_reset_token(conn, account_id)

    def _app_connect(self, *args, **kwargs):
        return REAL_CONNECT(self.db_path)

    def test_hibp_failure_returns_503_without_consuming_the_reset_token(self) -> None:
        with (
            mock.patch.object(app.control_db, "connect", side_effect=self._app_connect),
            mock.patch.object(password_breach, "pwned_password_count", side_effect=password_breach.PasswordBreachCheckError("offline")),
        ):
            response = app.reset_password_submit(_request(), self.token, "new-safe-password", "new-safe-password")
        self.assertEqual(response.status_code, 503)
        with self.connect() as conn:
            self.assertEqual(password_reset.consume_reset_token(conn, self.token, "new-safe-password"), 1)

    def test_common_password_rejected_without_consuming_reset_token(self) -> None:
        with (
            mock.patch.object(app.control_db, "connect", side_effect=self._app_connect),
            mock.patch.object(password_breach, "pwned_password_count") as pwned,
        ):
            response = app.reset_password_submit(_request(), self.token, "password", "password")
        self.assertEqual(response.status_code, 400)
        pwned.assert_not_called()
        with (
            mock.patch.object(app.control_db, "connect", side_effect=self._app_connect),
            mock.patch.object(password_breach, "pwned_password_count", return_value=0),
        ):
            response = app.reset_password_submit(
                _request(), self.token, "new-safe-password", "new-safe-password"
            )
        self.assertEqual(response.status_code, 303)

    def test_clear_password_check_allows_normal_reset(self) -> None:
        with (
            mock.patch.object(app.control_db, "connect", side_effect=self._app_connect),
            mock.patch.object(password_breach, "pwned_password_count", return_value=0),
        ):
            response = app.reset_password_submit(_request(), self.token, "new-safe-password", "new-safe-password")
        self.assertEqual(response.status_code, 303)
