"""Tests for the admin-panel logic (logic/accounts.py's is_admin_account /
list_all_tenants_overview, logic/billing.py's admin_extend_period).

is_admin_account is tested against a REAL sqlite3.Row from
get_account_by_id -- not a plain dict -- because indexing a Row the wrong
way (e.g. account.get("email"), which Row doesn't support) is exactly the
kind of bug a dict-only test would hide.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import mock

import config
from logic import accounts, billing

from .base import GatewayDbTestCase


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


class IsAdminAccountTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "owner@shop.ru", "password1234")

    def _get_row(self):
        with self.connect() as conn:
            return accounts.get_account_by_id(conn, self.account_id)

    def test_allowlisted_email_is_admin(self) -> None:
        row = self._get_row()  # a real sqlite3.Row, not a dict
        with mock.patch.object(config, "ADMIN_EMAILS", ["owner@shop.ru"]):
            self.assertTrue(accounts.is_admin_account(row))

    def test_matches_regardless_of_stored_email_casing(self) -> None:
        # config.ADMIN_EMAILS is always lowercased at parse time (see
        # config.py) -- the case-sensitivity risk is on the account's own
        # stored email, so that's what this varies.
        with self.connect() as conn:
            conn.execute("UPDATE accounts SET email='Owner@Shop.RU' WHERE id=?", (self.account_id,))
            row = accounts.get_account_by_id(conn, self.account_id)
        with mock.patch.object(config, "ADMIN_EMAILS", ["owner@shop.ru"]):
            self.assertTrue(accounts.is_admin_account(row))

    def test_non_allowlisted_email_is_not_admin(self) -> None:
        row = self._get_row()
        with mock.patch.object(config, "ADMIN_EMAILS", ["someone-else@shop.ru"]):
            self.assertFalse(accounts.is_admin_account(row))

    def test_empty_allowlist_means_nobody_is_admin(self) -> None:
        row = self._get_row()
        with mock.patch.object(config, "ADMIN_EMAILS", []):
            self.assertFalse(accounts.is_admin_account(row))

    def test_none_account_is_not_admin(self) -> None:
        with mock.patch.object(config, "ADMIN_EMAILS", ["owner@shop.ru"]):
            self.assertFalse(accounts.is_admin_account(None))


class ListAllTenantsOverviewTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()

    def test_joins_account_and_tenant_fields(self) -> None:
        with self.connect() as conn:
            account_id = accounts.create_account(conn, "seller@shop.ru", "password1234")
            billing.start_trial(conn, account_id, trial_days=14)
            tenant_id = accounts.create_tenant_instance(conn, account_id, "seller1")
            accounts.set_tenant_status(conn, tenant_id, "running")

            overview = accounts.list_all_tenants_overview(conn)

        self.assertEqual(len(overview), 1)
        row = overview[0]
        self.assertEqual(row["email"], "seller@shop.ru")
        self.assertEqual(row["account_status"], "trialing")
        self.assertEqual(row["slug"], "seller1")
        self.assertEqual(row["tenant_status"], "running")
        self.assertEqual(row["account_id"], account_id)
        self.assertEqual(row["tenant_id"], tenant_id)

    def test_empty_when_no_tenants_exist(self) -> None:
        with self.connect() as conn:
            self.assertEqual(accounts.list_all_tenants_overview(conn), [])

    def test_newest_tenant_first(self) -> None:
        with self.connect() as conn:
            first_account = accounts.create_account(conn, "first@shop.ru", "password1234")
            accounts.create_tenant_instance(conn, first_account, "first-slug")
            second_account = accounts.create_account(conn, "second@shop.ru", "password1234")
            accounts.create_tenant_instance(conn, second_account, "second-slug")
            # Force a distinguishable created_at ordering rather than relying
            # on same-second timestamps from two calls in a row.
            conn.execute("UPDATE tenant_instances SET created_at='2020-01-01T00:00:00' WHERE slug='first-slug'")
            conn.execute("UPDATE tenant_instances SET created_at='2030-01-01T00:00:00' WHERE slug='second-slug'")

            overview = accounts.list_all_tenants_overview(conn)
        self.assertEqual([row["slug"] for row in overview], ["second-slug", "first-slug"])


class AdminExtendPeriodTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "seller@shop.ru", "password1234")
            billing.start_trial(conn, self.account_id, trial_days=14)

    def test_extends_from_current_period_end_and_activates(self) -> None:
        with self.connect() as conn:
            before = accounts.get_account_by_id(conn, self.account_id)
            period_end_before = datetime.fromisoformat(before["current_period_end"])

            billing.admin_extend_period(conn, self.account_id, 30)

            after = accounts.get_account_by_id(conn, self.account_id)
        self.assertEqual(after["status"], "active")
        self.assertEqual(after["past_due_since"], "")
        period_end_after = datetime.fromisoformat(after["current_period_end"])
        expected = period_end_before + timedelta(days=30)
        self.assertLess(abs((period_end_after - expected).total_seconds()), 5)

    def test_extends_from_now_when_period_already_lapsed(self) -> None:
        with self.connect() as conn:
            stale_end = _iso(datetime.now(timezone.utc) - timedelta(days=10))
            conn.execute("UPDATE accounts SET current_period_end=? WHERE id=?", (stale_end, self.account_id))

            billing.admin_extend_period(conn, self.account_id, 30)

            after = accounts.get_account_by_id(conn, self.account_id)
        period_end_after = datetime.fromisoformat(after["current_period_end"])
        expected = datetime.now(timezone.utc) + timedelta(days=30)
        self.assertLess(abs((period_end_after - expected).total_seconds()), 5)

    def test_clears_past_due_state(self) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE accounts SET status='past_due', past_due_since=? WHERE id=?",
                (_iso(datetime.now(timezone.utc)), self.account_id),
            )
            billing.admin_extend_period(conn, self.account_id, 30)
            after = accounts.get_account_by_id(conn, self.account_id)
        self.assertEqual(after["status"], "active")
        self.assertEqual(after["past_due_since"], "")

    def test_unknown_account_raises(self) -> None:
        with self.connect() as conn:
            with self.assertRaises(ValueError):
                billing.admin_extend_period(conn, 999999, 30)


if __name__ == "__main__":
    import unittest
    unittest.main()
