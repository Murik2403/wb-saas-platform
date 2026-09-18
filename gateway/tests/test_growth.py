"""Growth measurements are lifecycle facts, never inferred from account status."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest import mock

import config
from logic import accounts, billing, db as control_db, growth

from .base import GatewayDbTestCase


NOW = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)


class GrowthTests(GatewayDbTestCase):
    def setUp(self):
        super().setUp()
        self.init_db()

    def account(self, conn, suffix="seller", *, at=NOW, consent=True, source=None):
        account_id = accounts.create_account(conn, f"{suffix}@example.com", "password1234")
        with mock.patch.object(growth, "_now", return_value=at):
            growth.capture_registration_source(
                conn, account_id, source or {"utm_source": "yandex", "utm_medium": "cpc"},
                consent=consent, captured_at=at,
            )
        return account_id

    def payment(self, conn, account_id, name, at, *, kind="initial", is_test=False):
        with mock.patch.object(billing, "_now", return_value=at):
            billing.record_payment_attempt(conn, account_id, name, kind, 2990)
            billing.apply_successful_payment(conn, account_id, name, is_test=is_test)

    def test_attribution_rejects_urls_emails_tokens_and_unapproved_fields(self):
        clean = growth.sanitize_attribution({
            "utm_source": "Yandex", "utm_medium": "CPC", "utm_campaign": "wb_production_1",
            "utm_content": "https://secret.test/?token=x", "utm_term": "user@example.com",
            "token": "do-not-store", "email": "secret@example.com", "referrer": "https://private.test/",
        })
        self.assertEqual(clean, {"utm_source": "yandex", "utm_medium": "cpc", "utm_campaign": "wb_production_1"})

    def test_no_consent_means_unknown_and_capture_does_not_overwrite(self):
        with self.connect() as conn:
            account_id = self.account(conn, consent=False)
            growth.capture_registration_source(conn, account_id, {"utm_source": "replacement"}, consent=True)
            row = conn.execute("SELECT * FROM growth_sources WHERE account_id=?", (account_id,)).fetchone()
            self.assertEqual(json.loads(row["attribution_json"]), {})
            self.assertEqual(row["touch_at"], "")
            count = conn.execute("SELECT COUNT(*) AS n FROM growth_events WHERE event_name='account_created'").fetchone()["n"]
            self.assertEqual(count, 1)

    def test_touch_time_does_not_backdate_registration_and_trial_source_expires(self):
        with self.connect() as conn:
            account_id = accounts.create_account(conn, "touch@example.com", "password1234")
            touch = NOW - timedelta(days=29)
            with mock.patch.object(growth, "_now", return_value=NOW):
                growth.capture_registration_source(conn, account_id, {"utm_source": "yandex"}, consent=True, captured_at=touch)
            event = conn.execute("SELECT occurred_at FROM growth_events WHERE account_id=?", (account_id,)).fetchone()
            self.assertEqual(datetime.fromisoformat(event["occurred_at"]), NOW)
            growth.record_trial_started(conn, account_id, occurred_at=NOW + timedelta(days=2))
            source = conn.execute("SELECT * FROM growth_sources WHERE account_id=?", (account_id,)).fetchone()
            self.assertEqual(json.loads(source["trial_attribution_json"]), {})
            self.assertEqual(source["attribution_status"], "outside_window")

    def test_event_allowlist_and_first_occurrence_are_idempotent(self):
        with self.connect() as conn:
            account_id = self.account(conn)
            self.assertTrue(growth.record_event(conn, account_id, "email_verified", occurred_at=NOW))
            self.assertFalse(growth.record_event(conn, account_id, "email_verified", occurred_at=NOW + timedelta(days=1)))
            with self.assertRaises(ValueError):
                growth.record_event(conn, account_id, "arbitrary_customer_data", metadata={"token": "x"})
            growth.record_event(conn, account_id, "first_sync_succeeded", metadata={"email": "secret", "scenario": "financial"})
            row = conn.execute("SELECT metadata_json FROM growth_events WHERE event_name='first_sync_succeeded'").fetchone()
            self.assertEqual(json.loads(row["metadata_json"]), {"scenario": "financial"})

    def test_only_mature_trials_enter_a7_and_p30_denominators(self):
        with self.connect() as conn:
            mature = self.account(conn, "mature", at=NOW - timedelta(days=40))
            young = self.account(conn, "young", at=NOW - timedelta(days=2))
            for account_id, age in ((mature, 40), (young, 2)):
                trial_at = NOW - timedelta(days=age)
                growth.record_trial_started(conn, account_id, occurred_at=trial_at)
                growth.record_activation(conn, account_id, "financial", occurred_at=trial_at + timedelta(days=1))
                self.payment(conn, account_id, f"pay-{age}", trial_at + timedelta(days=1))
            summary = growth.growth_summary(conn, now=NOW)
            self.assertEqual(summary["trials"], 2)
            self.assertEqual(summary["a7"], {"eligible": 1, "converted": 1, "pending": 1, "rate": 1.0})
            self.assertEqual(summary["p30"], {"eligible": 1, "converted": 1, "pending": 1, "rate": 1.0})
            self.assertIsNone(summary["cac"])

    def test_registration_and_readiness_are_not_trial_or_activation(self):
        with self.connect() as conn:
            account_id = self.account(conn, at=NOW - timedelta(days=40))
            growth.record_event(conn, account_id, "first_sync_succeeded", occurred_at=NOW - timedelta(days=39))
            growth.record_event(conn, account_id, "cost_setup_completed", occurred_at=NOW - timedelta(days=39))
            summary = growth.growth_summary(conn, now=NOW)
            self.assertEqual(summary["registered"], 1)
            self.assertEqual(summary["trials"], 0)
            self.assertIsNone(summary["a7"]["rate"])
            self.assertIsNone(summary["p30"]["rate"])
            growth.record_trial_started(conn, account_id, occurred_at=NOW - timedelta(days=35))
            self.assertEqual(growth.growth_summary(conn, now=NOW)["a7"]["converted"], 0)

    def test_trial_lifecycle_records_once_without_backdating_from_registration(self):
        with self.connect() as conn:
            account_id = self.account(conn, at=NOW - timedelta(days=20))
            with mock.patch.object(billing, "_now", return_value=NOW):
                billing.start_trial(conn, account_id)
                billing.start_trial(conn, account_id)
            rows = conn.execute("SELECT occurred_at FROM growth_events WHERE event_name='trial_started'").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(datetime.fromisoformat(rows[0]["occurred_at"]), NOW)

    def test_real_renewal_requires_two_distinct_positive_non_test_payments(self):
        with self.connect() as conn:
            account_id = self.account(conn, at=NOW - timedelta(days=40))
            growth.record_trial_started(conn, account_id, occurred_at=NOW - timedelta(days=40))
            self.payment(conn, account_id, "test-payment", NOW - timedelta(days=30), is_test=True)
            self.payment(conn, account_id, "first-real", NOW - timedelta(days=25), kind="recurring")
            with mock.patch.object(billing, "_now", return_value=NOW - timedelta(days=25)):
                billing.apply_successful_payment(conn, account_id, "first-real")
                billing.admin_extend_period(conn, account_id, 30)
            self.assertEqual(growth.growth_summary(conn, now=NOW)["renewed"], 0)
            self.payment(conn, account_id, "second-real", NOW - timedelta(days=1), kind="manual")
            summary = growth.growth_summary(conn, now=NOW)
            self.assertEqual(summary["paid"], 1)
            self.assertEqual(summary["renewed"], 1)
            self.assertEqual(summary["p30"]["converted"], 1)
            events = conn.execute("SELECT event_name FROM growth_events WHERE event_name IN ('payment_succeeded','renewal_succeeded')").fetchall()
            self.assertEqual([r["event_name"] for r in events].count("payment_succeeded"), 2)
            self.assertEqual([r["event_name"] for r in events].count("renewal_succeeded"), 1)

    def test_refunds_remove_payment_conversion_without_rewriting_history(self):
        with self.connect() as conn:
            account_id = self.account(conn, at=NOW - timedelta(days=40))
            growth.record_trial_started(conn, account_id, occurred_at=NOW - timedelta(days=40))
            self.payment(conn, account_id, "refund-me", NOW - timedelta(days=20))
            with mock.patch.object(billing, "_now", return_value=NOW - timedelta(days=1)):
                billing.apply_refund(conn, account_id, "refund-me", 2990, "refund-1")
            summary = growth.growth_summary(conn, now=NOW)
            self.assertEqual(summary["p30"]["converted"], 0)
            self.assertEqual(summary["refunds"], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM growth_events WHERE event_name='payment_succeeded'").fetchone()["n"], 1)

    def test_late_test_flag_excludes_previous_success_without_double_extension(self):
        with self.connect() as conn:
            account_id = self.account(conn, at=NOW - timedelta(days=40))
            growth.record_trial_started(conn, account_id, occurred_at=NOW - timedelta(days=40))
            self.payment(conn, account_id, "later-confirmed-test", NOW - timedelta(days=20))
            self.assertEqual(growth.growth_summary(conn, now=NOW)["p30"]["converted"], 1)
            before = accounts.get_account_by_id(conn, account_id)["current_period_end"]
            billing.apply_successful_payment(conn, account_id, "later-confirmed-test", is_test=True)
            billing.apply_successful_payment(conn, account_id, "later-confirmed-test", is_test=False)
            self.assertEqual(accounts.get_account_by_id(conn, account_id)["current_period_end"], before)
            self.assertEqual(growth.growth_summary(conn, now=NOW)["p30"]["converted"], 0)
            self.assertEqual(growth.growth_summary(conn, now=NOW)["paid"], 0)

    def test_zero_and_negative_payment_do_not_create_a_paid_conversion(self):
        with self.connect() as conn:
            account_id = self.account(conn, at=NOW - timedelta(days=40))
            growth.record_trial_started(conn, account_id, occurred_at=NOW - timedelta(days=40))
            for amount in (0, -1):
                name = f"invalid-amount-{amount}"
                with mock.patch.object(billing, "_now", return_value=NOW - timedelta(days=20)):
                    billing.record_payment_attempt(conn, account_id, name, "manual", amount)
                    billing.apply_successful_payment(conn, account_id, name)
            self.assertEqual(growth.growth_summary(conn, now=NOW)["paid"], 0)

    def test_expired_activation_and_late_payment_are_not_early_conversions(self):
        with self.connect() as conn:
            account_id = self.account(conn, at=NOW - timedelta(days=40))
            trial_at = NOW - timedelta(days=40)
            growth.record_trial_started(conn, account_id, occurred_at=trial_at)
            growth.record_activation(conn, account_id, "financial", occurred_at=trial_at + timedelta(days=7))
            self.payment(conn, account_id, "late", trial_at + timedelta(days=31))
            summary = growth.growth_summary(conn, now=NOW)
            self.assertEqual(summary["a7"]["converted"], 0)
            self.assertEqual(summary["p30"]["converted"], 0)
            self.assertEqual(summary["paid"], 1)

    def test_future_touch_is_not_saved_and_explicit_touch_alias_works(self):
        with self.connect() as conn:
            future = accounts.create_account(conn, "future@example.com", "password1234")
            valid = accounts.create_account(conn, "valid@example.com", "password1234")
            with mock.patch.object(growth, "_now", return_value=NOW):
                growth.capture_registration_source(conn, future, {"utm_source": "yandex"}, consent=True, touch_at=NOW + timedelta(seconds=1))
                growth.capture_registration_source(conn, valid, {"utm_source": "vk"}, consent=True, touch_at=NOW - timedelta(days=1))
            growth.record_trial_started(conn, valid, occurred_at=NOW)
            source = conn.execute("SELECT trial_attribution_json FROM growth_sources WHERE account_id=?", (valid,)).fetchone()
            self.assertEqual(json.loads(source["trial_attribution_json"]), {"utm_source": "vk"})
            source = conn.execute("SELECT attribution_json,touch_at FROM growth_sources WHERE account_id=?", (future,)).fetchone()
            self.assertEqual((source["attribution_json"], source["touch_at"]), ("{}", ""))

    def test_explicit_exclusions_and_admins_do_not_enter_any_denominator(self):
        with self.connect() as conn:
            for suffix, reason in (("test", "test"), ("staff", "employee"), ("robot", "bot"), ("owner", "")):
                account_id = self.account(conn, suffix, at=NOW - timedelta(days=40))
                growth.record_trial_started(conn, account_id, occurred_at=NOW - timedelta(days=40))
                if reason:
                    growth.set_account_exclusion(conn, account_id, reason)
            with mock.patch.object(config, "ADMIN_EMAILS", ["owner@example.com"]):
                summary = growth.growth_summary(conn, now=NOW)
            self.assertEqual(summary["registered"], 0)
            self.assertEqual(summary["excluded_accounts"], 4)
            self.assertIsNone(summary["a7"]["rate"])

    def test_upgrade_preserves_old_accounts_without_inventing_lifecycle_events(self):
        with self.connect() as conn:
            old = accounts.create_account(conn, "legacy@example.com", "password1234")
            for table in ("growth_payment_exclusions", "growth_events", "growth_sources"):
                conn.execute(f"DROP TABLE {table}")
            control_db.init_db(conn)
            control_db.init_db(conn)
            self.assertIsNotNone(accounts.get_account_by_id(conn, old))
            summary = growth.growth_summary(conn, now=NOW)
            self.assertEqual(summary["registered"], 0)
            self.assertEqual(summary["untracked_accounts"], 1)

    def test_account_deletion_cascades_growth_personal_data(self):
        with self.connect() as conn:
            account_id = self.account(conn)
            accounts.delete_account(conn, account_id)
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM growth_sources").fetchone()["n"], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM growth_events").fetchone()["n"], 0)
