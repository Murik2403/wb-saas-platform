"""Growth reporting and exclusions stay behind the existing admin boundary."""
from __future__ import annotations

from unittest import mock

from fastapi import Request

import admin_routes
from logic import accounts, db as control_db, growth

from .base import GatewayDbTestCase

REAL_CONNECT = control_db.connect


def request():
    return Request({"type": "http", "headers": [], "method": "POST", "path": "/admin", "client": ("127.0.0.1", 1234)})


class GrowthAdminTests(GatewayDbTestCase):
    def setUp(self):
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "seller@example.com", "password1234")
            growth.capture_registration_source(conn, self.account_id)
            self.account_row = accounts.get_account_by_id(conn, self.account_id)

    def _connection(self):
        return REAL_CONNECT(self.db_path)

    def test_non_admin_cannot_read_report_or_change_exclusions(self):
        with mock.patch.object(admin_routes, "_require_admin", return_value=None):
            read = admin_routes.admin_dashboard(request())
            change = admin_routes.admin_growth_exclusion(request(), self.account_id, "test", "valid")
        self.assertEqual(read.headers["location"], "/login?next=%2Fadmin")
        self.assertEqual(change.headers["location"], "/login")
        with self.connect() as conn:
            self.assertEqual(growth.growth_summary(conn)["excluded_accounts"], 0)

    def test_admin_needs_csrf_and_allowlisted_reason(self):
        with mock.patch.object(admin_routes, "_require_admin", return_value=self.account_row), mock.patch.object(admin_routes.csrf, "verify", return_value=False):
            admin_routes.admin_growth_exclusion(request(), self.account_id, "test", "bad")
        with self.connect() as conn:
            self.assertEqual(growth.growth_summary(conn)["excluded_accounts"], 0)
        with mock.patch.object(admin_routes, "_require_admin", return_value=self.account_row), mock.patch.object(admin_routes.csrf, "verify", return_value=True):
            admin_routes.admin_growth_exclusion(request(), self.account_id, "arbitrary personal note", "valid")
        with self.connect() as conn:
            self.assertEqual(growth.growth_summary(conn)["excluded_accounts"], 0)

    def test_valid_exclusion_changes_summary_and_can_be_removed(self):
        with mock.patch.object(admin_routes, "_require_admin", return_value=self.account_row), mock.patch.object(admin_routes.csrf, "verify", return_value=True), mock.patch.object(admin_routes.control_db, "connect", side_effect=self._connection):
            admin_routes.admin_growth_exclusion(request(), self.account_id, "test", "valid")
            with self._connection() as conn:
                self.assertEqual(growth.growth_summary(conn)["excluded_accounts"], 1)
            admin_routes.admin_growth_exclusion(request(), self.account_id, "", "valid")
        with self.connect() as conn:
            self.assertEqual(growth.growth_summary(conn)["registered"], 1)

    def test_empty_metrics_render_as_unknown_not_zero_percent(self):
        with mock.patch.object(admin_routes, "_require_admin", return_value=self.account_row), mock.patch.object(admin_routes.control_db, "connect", side_effect=self._connection):
            response = admin_routes.admin_dashboard(request())
        html = response.body.decode("utf-8")
        self.assertIn("нет созревших проб", html)
        self.assertIn("Не рассчитана", html)
        self.assertNotIn("(0.0%)", html)
