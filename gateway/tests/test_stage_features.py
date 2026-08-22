from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from tests.base import GatewayDbTestCase
from logic import accounts
from rate_limiter import RateLimiter
from telegram_notifier import send_telegram_alert


class TestRateLimiter(unittest.TestCase):
    def test_rate_limiter_allows_and_blocks(self):
        limiter = RateLimiter(requests_per_window=3, window_seconds=60)
        key = "test_ip"
        self.assertTrue(limiter.is_allowed(key))
        self.assertTrue(limiter.is_allowed(key))
        self.assertTrue(limiter.is_allowed(key))
        # 4th request should be rate-limited
        self.assertFalse(limiter.is_allowed(key))


class TestTelegramNotifier(unittest.TestCase):
    def test_alert_returns_false_when_unconfigured(self):
        # With default empty credentials in test environment, it returns False safely without error
        res = send_telegram_alert("Test alert")
        self.assertFalse(res)


class TestIdleTenantsAndAdmin(GatewayDbTestCase):
    def test_idle_tenants_and_activity_touch(self):
        self.init_db()
        with self.connect() as conn:
            acc_id = accounts.create_account(conn, "user1@example.com", "Password123!")
            tenant_id = accounts.create_tenant_instance(conn, acc_id, "user1")
            accounts.mark_tenant_provisioned(conn, tenant_id, acc_id)

            # Set last_active_at to 1 hour ago
            old_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
            conn.execute("UPDATE tenant_instances SET last_active_at=? WHERE id=?", (old_time, tenant_id))

            idle_list = accounts.get_idle_tenants(conn, idle_minutes=30)
            self.assertEqual(len(idle_list), 1)
            self.assertEqual(idle_list[0]["slug"], "user1")

            # Touch activity
            accounts.touch_tenant_activity(conn, tenant_id)
            idle_list_after = accounts.get_idle_tenants(conn, idle_minutes=30)
            self.assertEqual(len(idle_list_after), 0)

    def test_is_admin_account(self):
        account = {"email": "admin@example.com"}
        import config
        config.ADMIN_EMAILS = ["admin@example.com"]
        self.assertTrue(accounts.is_admin_account(account))

        non_admin = {"email": "user@example.com"}
        self.assertFalse(accounts.is_admin_account(non_admin))
