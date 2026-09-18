"""Provisioning smoke uses a disposable DB and no container/network calls."""
from unittest import mock

import provisioning
from logic import accounts, db as control_db, growth

from .base import GatewayDbTestCase

REAL_CONNECT = control_db.connect


class GrowthProvisioningSmokeTests(GatewayDbTestCase):
    def setUp(self):
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "smoke@example.test", "safe-password-123", email_verified=True)
            self.tenant_id = accounts.create_tenant_instance(conn, self.account_id, "growth-smoke")
            growth.capture_registration_source(conn, self.account_id, exclusion_reason="test")

    def _connection(self, *args, **kwargs):
        return REAL_CONNECT(self.db_path)

    def test_healthy_provisioning_records_ready_then_trial_once(self):
        with (
            mock.patch.object(provisioning.control_db, "connect", side_effect=self._connection),
            mock.patch.object(provisioning, "_run_tenant_container") as create,
            mock.patch.object(provisioning, "_wait_until_healthy", return_value=True),
            mock.patch.object(provisioning, "_wait_for_tenant_cert", return_value=True),
        ):
            provisioning.provision_tenant_background(self.account_id, self.tenant_id, "growth-smoke")
            provisioning.provision_tenant_background(self.account_id, self.tenant_id, "growth-smoke")
        assert create.call_count == 2
        with self.connect() as conn:
            assert accounts.get_tenant_by_id(conn, self.tenant_id)["status"] == "running"
            assert accounts.get_account_by_id(conn, self.account_id)["status"] == "trialing"
            events = conn.execute("SELECT event_name,occurred_at FROM growth_events WHERE account_id=? ORDER BY occurred_at", (self.account_id,)).fetchall()
            names = [row["event_name"] for row in events]
            assert names.count("tenant_ready") == 1
            assert names.count("trial_started") == 1
            times = {row["event_name"]: row["occurred_at"] for row in events}
            assert times["tenant_ready"] <= times["trial_started"]
            assert growth.growth_summary(conn)["trials"] == 0  # explicit test exclusion

    def test_unhealthy_provisioning_never_starts_a_trial_or_ready_event(self):
        with (
            mock.patch.object(provisioning.control_db, "connect", side_effect=self._connection),
            mock.patch.object(provisioning, "_run_tenant_container"),
            mock.patch.object(provisioning, "_wait_until_healthy", return_value=False),
            mock.patch.object(provisioning, "_cleanup_failed_tenant") as cleanup,
            mock.patch.object(provisioning, "_wait_for_tenant_cert") as certificate,
        ):
            provisioning.provision_tenant_background(self.account_id, self.tenant_id, "growth-smoke")
        cleanup.assert_called_once_with("growth-smoke")
        certificate.assert_not_called()
        with self.connect() as conn:
            assert accounts.get_tenant_by_id(conn, self.tenant_id)["status"] == "failed"
            assert accounts.get_account_by_id(conn, self.account_id)["status"] == "pending"
            assert conn.execute("SELECT event_name FROM growth_events WHERE event_name IN ('tenant_ready','trial_started')").fetchall() == []
