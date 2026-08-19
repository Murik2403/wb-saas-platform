from __future__ import annotations

from datetime import datetime, timedelta, timezone

from logic import accounts, billing

from .base import GatewayDbTestCase


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


class TrialTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "seller@shop.ru", "password1234")

    def test_full_provisioning_then_trial_start_grants_access(self) -> None:
        """Mirrors provisioning.py's real call sequence:
        accounts.mark_tenant_provisioned() then billing.start_trial()."""
        with self.connect() as conn:
            slug = accounts.generate_unique_slug(conn, "seller@shop.ru")
            tenant_id = accounts.create_tenant_instance(conn, self.account_id, slug)
            accounts.mark_tenant_provisioned(conn, tenant_id, self.account_id)
            billing.start_trial(conn, self.account_id, trial_days=14)
            account = accounts.get_account_by_id(conn, self.account_id)
            tenant = accounts.get_tenant_for_account(conn, self.account_id)
        self.assertEqual(tenant["status"], "running")
        self.assertEqual(account["status"], "trialing")
        self.assertTrue(accounts.account_has_access(account))

    def test_start_trial_sets_status_and_period_end(self) -> None:
        with self.connect() as conn:
            billing.start_trial(conn, self.account_id, trial_days=14)
            account = accounts.get_account_by_id(conn, self.account_id)
        self.assertEqual(account["status"], "trialing")
        self.assertTrue(accounts.account_has_access(account))
        period_end = datetime.fromisoformat(account["current_period_end"])
        expected = datetime.now(timezone.utc) + timedelta(days=14)
        self.assertLess(abs((period_end - expected).total_seconds()), 5)


class SuccessfulPaymentTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "seller@shop.ru", "password1234")
            billing.start_trial(conn, self.account_id, trial_days=14)

    def test_successful_payment_activates_and_saves_payment_method(self) -> None:
        with self.connect() as conn:
            billing.record_payment_attempt(conn, self.account_id, "yk-1", "initial", 2990.0)
            billing.apply_successful_payment(conn, self.account_id, "yk-1", payment_method_id="pm-abc", period_days=30)
            account = accounts.get_account_by_id(conn, self.account_id)
            payment = conn.execute("SELECT * FROM payments WHERE yookassa_payment_id='yk-1'").fetchone()
        self.assertEqual(account["status"], "active")
        self.assertEqual(account["payment_method_id"], "pm-abc")
        self.assertEqual(account["past_due_since"], "")
        self.assertEqual(payment["status"], "succeeded")

    def test_successful_payment_extends_from_current_period_end_not_from_now(self) -> None:
        # trial still has 14 days left -- paying now should extend from the
        # trial's end, not reset the clock to now+30.
        with self.connect() as conn:
            account_before = accounts.get_account_by_id(conn, self.account_id)
            period_end_before = datetime.fromisoformat(account_before["current_period_end"])

            billing.record_payment_attempt(conn, self.account_id, "yk-1", "initial", 2990.0)
            billing.apply_successful_payment(conn, self.account_id, "yk-1", payment_method_id="pm-abc", period_days=30)

            account_after = accounts.get_account_by_id(conn, self.account_id)
        period_end_after = datetime.fromisoformat(account_after["current_period_end"])
        expected = period_end_before + timedelta(days=30)
        self.assertLess(abs((period_end_after - expected).total_seconds()), 5)

    def test_late_payment_extends_from_now_not_from_stale_period_end(self) -> None:
        with self.connect() as conn:
            # simulate a trial that already lapsed 10 days ago
            stale_end = _iso(datetime.now(timezone.utc) - timedelta(days=10))
            conn.execute("UPDATE accounts SET current_period_end=? WHERE id=?", (stale_end, self.account_id))

            billing.record_payment_attempt(conn, self.account_id, "yk-1", "manual", 2990.0)
            billing.apply_successful_payment(conn, self.account_id, "yk-1", payment_method_id="pm-abc", period_days=30)

            account_after = accounts.get_account_by_id(conn, self.account_id)
        period_end_after = datetime.fromisoformat(account_after["current_period_end"])
        expected = datetime.now(timezone.utc) + timedelta(days=30)
        self.assertLess(abs((period_end_after - expected).total_seconds()), 5)

    def test_reapplying_same_payment_id_does_not_duplicate_row(self) -> None:
        with self.connect() as conn:
            billing.record_payment_attempt(conn, self.account_id, "yk-1", "initial", 2990.0)
            billing.record_payment_attempt(conn, self.account_id, "yk-1", "initial", 2990.0, status="succeeded")
            count = conn.execute("SELECT COUNT(*) AS n FROM payments WHERE yookassa_payment_id='yk-1'").fetchone()["n"]
        self.assertEqual(count, 1)


class FailedPaymentAndGraceTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "seller@shop.ru", "password1234")
            billing.start_trial(conn, self.account_id, trial_days=14)

    def test_failed_payment_moves_account_to_past_due_but_keeps_access(self) -> None:
        with self.connect() as conn:
            billing.record_payment_attempt(conn, self.account_id, "yk-1", "recurring", 2990.0)
            billing.apply_failed_payment(conn, self.account_id, "yk-1", error="card_declined")
            account = accounts.get_account_by_id(conn, self.account_id)
        self.assertEqual(account["status"], "past_due")
        self.assertTrue(accounts.account_has_access(account))  # grace period -- still has access
        self.assertNotEqual(account["past_due_since"], "")

    def test_repeated_failed_payments_do_not_reset_grace_period_clock(self) -> None:
        with self.connect() as conn:
            billing.record_payment_attempt(conn, self.account_id, "yk-1", "recurring", 2990.0)
            billing.apply_failed_payment(conn, self.account_id, "yk-1", error="card_declined")
            account_first = accounts.get_account_by_id(conn, self.account_id)
            first_past_due_since = account_first["past_due_since"]

            # a second, later retry fails again
            billing.record_payment_attempt(conn, self.account_id, "yk-2", "recurring", 2990.0)
            billing.apply_failed_payment(conn, self.account_id, "yk-2", error="card_declined")
            account_second = accounts.get_account_by_id(conn, self.account_id)
        self.assertEqual(account_second["past_due_since"], first_past_due_since)

    def test_successful_payment_after_past_due_clears_grace_period(self) -> None:
        with self.connect() as conn:
            billing.record_payment_attempt(conn, self.account_id, "yk-1", "recurring", 2990.0)
            billing.apply_failed_payment(conn, self.account_id, "yk-1", error="card_declined")

            billing.record_payment_attempt(conn, self.account_id, "yk-2", "manual", 2990.0)
            billing.apply_successful_payment(conn, self.account_id, "yk-2", payment_method_id="pm-new", period_days=30)

            account = accounts.get_account_by_id(conn, self.account_id)
        self.assertEqual(account["status"], "active")
        self.assertEqual(account["past_due_since"], "")

    def test_accounts_past_grace_period_and_cancel(self) -> None:
        with self.connect() as conn:
            billing.record_payment_attempt(conn, self.account_id, "yk-1", "recurring", 2990.0)
            billing.apply_failed_payment(conn, self.account_id, "yk-1", error="card_declined")
            # backdate past_due_since so the account looks like it's been
            # failing for 10 days already
            stale = _iso(datetime.now(timezone.utc) - timedelta(days=10))
            conn.execute("UPDATE accounts SET past_due_since=? WHERE id=?", (stale, self.account_id))

            overdue = billing.accounts_past_grace_period(conn, grace_days=5)
            self.assertEqual(len(overdue), 1)
            self.assertEqual(overdue[0]["id"], self.account_id)

            billing.cancel_account(conn, self.account_id)
            account = accounts.get_account_by_id(conn, self.account_id)
        self.assertEqual(account["status"], "canceled")
        self.assertFalse(accounts.account_has_access(account))

    def test_accounts_not_yet_past_grace_period_excluded(self) -> None:
        with self.connect() as conn:
            billing.record_payment_attempt(conn, self.account_id, "yk-1", "recurring", 2990.0)
            billing.apply_failed_payment(conn, self.account_id, "yk-1", error="card_declined")
            overdue = billing.accounts_past_grace_period(conn, grace_days=5)
        self.assertEqual(overdue, [])  # past_due_since is "now", 5-day grace period hasn't elapsed

    def test_days_left_in_grace_period(self) -> None:
        with self.connect() as conn:
            billing.record_payment_attempt(conn, self.account_id, "yk-1", "recurring", 2990.0)
            billing.apply_failed_payment(conn, self.account_id, "yk-1", error="card_declined")
            stale = _iso(datetime.now(timezone.utc) - timedelta(days=2))
            conn.execute("UPDATE accounts SET past_due_since=? WHERE id=?", (stale, self.account_id))
            account = accounts.get_account_by_id(conn, self.account_id)
        days_left = billing.days_left_in_grace_period(account, grace_days=5)
        self.assertEqual(days_left, 3)


class DailyBillingCycleQueryTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()

    def _make_account(self, email: str) -> int:
        with self.connect() as conn:
            account_id = accounts.create_account(conn, email, "password1234")
            billing.start_trial(conn, account_id, trial_days=14)
        return account_id

    def test_due_for_recurring_charge_requires_saved_payment_method(self) -> None:
        account_id = self._make_account("with-card@shop.ru")
        with self.connect() as conn:
            # backdate the trial to have already ended, and simulate a saved card
            stale = _iso(datetime.now(timezone.utc) - timedelta(days=1))
            conn.execute(
                "UPDATE accounts SET current_period_end=?, payment_method_id='pm-1' WHERE id=?",
                (stale, account_id),
            )
            due = billing.accounts_due_for_recurring_charge(conn)
        self.assertEqual([r["id"] for r in due], [account_id])

    def test_trial_expiring_without_card_is_not_in_recurring_charge_list(self) -> None:
        account_id = self._make_account("no-card@shop.ru")
        with self.connect() as conn:
            stale = _iso(datetime.now(timezone.utc) - timedelta(days=1))
            conn.execute("UPDATE accounts SET current_period_end=? WHERE id=?", (stale, account_id))
            due_for_charge = billing.accounts_due_for_recurring_charge(conn)
            expiring = billing.trials_expiring_without_payment(conn)
        self.assertEqual(due_for_charge, [])
        self.assertEqual([r["id"] for r in expiring], [account_id])

    def test_expire_trial_without_payment_starts_grace_period(self) -> None:
        account_id = self._make_account("no-card@shop.ru")
        with self.connect() as conn:
            billing.expire_trial_without_payment(conn, account_id)
            account = accounts.get_account_by_id(conn, account_id)
        self.assertEqual(account["status"], "past_due")
        self.assertNotEqual(account["past_due_since"], "")
        self.assertTrue(accounts.account_has_access(account))  # still in grace period

    def test_not_yet_due_accounts_excluded_from_both_queries(self) -> None:
        self._make_account("fresh-trial@shop.ru")  # current_period_end is 14 days out
        with self.connect() as conn:
            self.assertEqual(billing.accounts_due_for_recurring_charge(conn), [])
            self.assertEqual(billing.trials_expiring_without_payment(conn), [])


if __name__ == "__main__":
    import unittest
    unittest.main()
