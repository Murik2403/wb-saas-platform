from __future__ import annotations

import importlib
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import config
from logic import accounts, billing, db as control_db, growth, trial_emails

from .base import GATEWAY_ROOT, GatewayDbTestCase

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _account(**overrides) -> dict:
    account = {
        "id": 1,
        "email": "seller@shop.ru",
        "status": "trialing",
        "email_verified_at": _iso(NOW - timedelta(days=20)),
        "current_period_end": _iso(NOW + timedelta(days=10)),
        "past_due_since": "",
    }
    account.update(overrides)
    return account


def _events(started_days_ago: float = 0, **extra) -> dict:
    events = {"trial_started": _iso(NOW - timedelta(days=started_days_ago))}
    events.update(extra)
    return events


class SelectionTests(unittest.TestCase):
    """The conditions of docs/content/02-trial-email-sequence.md, one by one."""

    def due(self, account=None, events=None, **kwargs):
        return trial_emails.due_email_key(
            account or _account(), events if events is not None else _events(), NOW, **kwargs
        )

    def test_email_1_on_day_zero(self) -> None:
        self.assertEqual(self.due(), "1")

    def test_email_2_on_day_two_without_report(self) -> None:
        self.assertEqual(self.due(events=_events(2), sent={"1": _iso(NOW - timedelta(days=2))}), "2")

    def test_email_2b_replaces_2_when_first_useful_report_exists(self) -> None:
        events = _events(2, first_useful_report=_iso(NOW - timedelta(days=1)))
        self.assertEqual(self.due(events=events, sent={"1": _iso(NOW - timedelta(days=2))}), "2b")

    def test_emails_2_and_2b_are_one_mutually_exclusive_step(self) -> None:
        events = _events(3, first_useful_report=_iso(NOW - timedelta(days=1)))
        sent = {"1": _iso(NOW - timedelta(days=3)), "2": _iso(NOW - timedelta(days=1))}
        self.assertIsNone(self.due(events=events, sent=sent))

    def test_email_3_on_day_five(self) -> None:
        sent = {"1": _iso(NOW - timedelta(days=5)), "2": _iso(NOW - timedelta(days=3))}
        self.assertEqual(self.due(events=_events(5), sent=sent), "3")

    def test_email_4_waits_for_day_nine_and_a_confirmed_report(self) -> None:
        sent = {k: _iso(NOW - timedelta(days=5)) for k in ("1", "2", "3")}
        self.assertIsNone(self.due(events=_events(9), sent=sent), "no report -> no email 4")
        events = _events(9, first_useful_report=_iso(NOW - timedelta(days=4)))
        self.assertEqual(self.due(events=events, sent=sent), "4")
        events_early = _events(8, first_useful_report=_iso(NOW - timedelta(days=4)))
        self.assertIsNone(self.due(events=events_early, sent=sent), "day 8 is too early")

    def test_email_5_three_days_before_the_actual_period_end(self) -> None:
        account = _account(current_period_end=_iso(NOW + timedelta(days=2, hours=12)))
        self.assertEqual(self.due(account=account, events=_events(11)), "5")

    def test_email_5_is_not_caught_up_when_the_window_passed(self) -> None:
        account = _account(current_period_end=_iso(NOW + timedelta(days=1, hours=12)))
        sent = {k: _iso(NOW - timedelta(days=2)) for k in ("1", "2", "3", "4")}
        self.assertIsNone(self.due(account=account, events=_events(12), sent=sent))

    def test_email_6_in_the_last_24_hours(self) -> None:
        account = _account(current_period_end=_iso(NOW + timedelta(hours=6)))
        sent = {"5": _iso(NOW - timedelta(days=2))}
        self.assertEqual(self.due(account=account, events=_events(13), sent=sent), "6")

    def test_email_6_is_not_sent_after_the_trial_already_ended(self) -> None:
        account = _account(current_period_end=_iso(NOW - timedelta(hours=1)))
        sent = {k: _iso(NOW - timedelta(days=2)) for k in ("1", "2", "3", "4", "5")}
        self.assertIsNone(self.due(account=account, events=_events(14), sent=sent))

    def test_deadline_reminder_outranks_the_onboarding_steps(self) -> None:
        account = _account(current_period_end=_iso(NOW + timedelta(hours=6)))
        self.assertEqual(self.due(account=account, events=_events(13)), "6")

    def test_email_7_after_confirmed_grace_end_and_stopped_tenant(self) -> None:
        account = _account(
            status="canceled", current_period_end=_iso(NOW - timedelta(days=6)),
            past_due_since=_iso(NOW - timedelta(days=6)),
        )
        self.assertEqual(
            self.due(account=account, events=_events(20), tenant_status="stopped", grace_days=5), "7"
        )

    def test_email_7_needs_a_really_stopped_tenant(self) -> None:
        """canceled alone is not enough -- stop_tenant() can fail."""
        account = _account(
            status="canceled", past_due_since=_iso(NOW - timedelta(days=6)),
            current_period_end=_iso(NOW - timedelta(days=6)),
        )
        self.assertIsNone(self.due(account=account, events=_events(20), tenant_status="running", grace_days=5))

    def test_email_7_waits_for_the_grace_period_to_actually_end(self) -> None:
        account = _account(
            status="canceled", past_due_since=_iso(NOW - timedelta(days=2)),
            current_period_end=_iso(NOW - timedelta(days=2)),
        )
        self.assertIsNone(self.due(account=account, events=_events(16), tenant_status="stopped", grace_days=5))

    def test_email_7_only_once(self) -> None:
        account = _account(
            status="canceled", past_due_since=_iso(NOW - timedelta(days=9)),
            current_period_end=_iso(NOW - timedelta(days=9)),
        )
        sent = {"7": _iso(NOW - timedelta(days=3))}
        self.assertIsNone(
            self.due(account=account, events=_events(25), sent=sent, tenant_status="stopped", grace_days=5)
        )

    # --- exit conditions -------------------------------------------------

    def test_paid_mid_sequence_exits_permanently(self) -> None:
        events = _events(5, payment_succeeded=_iso(NOW - timedelta(days=1)))
        sent = {"1": _iso(NOW - timedelta(days=5))}
        self.assertIsNone(self.due(events=events, sent=sent))
        # ...and a later cancellation does not bring them back.
        account = _account(status="canceled", past_due_since=_iso(NOW - timedelta(days=40)))
        self.assertIsNone(self.due(account=account, events=events, tenant_status="stopped"))

    def test_active_status_exits(self) -> None:
        self.assertIsNone(self.due(account=_account(status="active")))

    def test_unverified_email_never_starts_the_sequence(self) -> None:
        self.assertIsNone(self.due(account=_account(email_verified_at="")))

    def test_registration_alone_does_not_start_the_sequence(self) -> None:
        self.assertIsNone(self.due(events={"account_created": _iso(NOW)}))

    def test_opted_out_gets_nothing(self) -> None:
        self.assertIsNone(self.due(opted_out=True))

    def test_already_sent_is_not_repeated(self) -> None:
        self.assertIsNone(self.due(sent={"1": _iso(NOW - timedelta(days=5))}, events=_events(0)))

    def test_at_most_one_email_per_day(self) -> None:
        sent = {"1": _iso(NOW - timedelta(hours=3))}
        self.assertIsNone(self.due(events=_events(5), sent=sent))

    def test_past_due_gets_nothing_until_the_stop_is_confirmed(self) -> None:
        account = _account(status="past_due", past_due_since=_iso(NOW - timedelta(days=1)))
        self.assertIsNone(self.due(account=account, events=_events(15), tenant_status="running"))


class ContentTests(unittest.TestCase):
    def render(self, key: str):
        return trial_emails.render_email(
            key, cabinet_url="https://acme.app.example.ru/",
            unsubscribe_link="https://example.ru/unsubscribe?token=1.abc",
            period_end=NOW + timedelta(days=3), grace_days=5,
        )

    def test_every_email_carries_an_unsubscribe_link(self) -> None:
        for key in trial_emails.EMAIL_KEYS:
            with self.subTest(email=key):
                _, body = self.render(key)
                self.assertIn("https://example.ru/unsubscribe?token=1.abc", body)
                self.assertIn("отпиской не отключаются", body)

    def test_only_unconfirmed_facts_are_left_as_placeholders(self) -> None:
        """The doc's TODO items stay visible; everything else is filled in."""
        unfilled = {}
        for key in trial_emails.EMAIL_KEYS:
            subject, body = self.render(key)
            unfilled[key] = trial_emails.unfilled_placeholders(subject + body)
        self.assertEqual(unfilled["5"], ["%УСЛОВИЯ_ХРАНЕНИЯ%"])
        self.assertEqual(unfilled["7"], ["%ПОДТВЕРЖДЁННЫЕ_УСЛОВИЯ_ВОЗВРАТА_И_ХРАНЕНИЯ%"])
        for key in ("1", "2", "2b", "3", "4", "6"):
            self.assertEqual(unfilled[key], [], f"email {key} still has placeholders")

    def test_real_period_end_is_substituted(self) -> None:
        _, body = self.render("5")
        self.assertIn("23.09.2026", body)

    def test_grace_period_wording_follows_the_configured_value(self) -> None:
        _, body = trial_emails.render_email(
            "6", cabinet_url="https://x/", unsubscribe_link="u", period_end=NOW, grace_days=0,
        )
        self.assertNotIn("льготный период", body)

    def test_unknown_email_key_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.render("8")


class UnsubscribeTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        self._secret = mock.patch.object(config, "INTERNAL_API_SECRET", "unit-test-secret")
        self._secret.start()
        self.addCleanup(self._secret.stop)
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "seller@shop.ru", "password1234")

    def test_token_round_trip_and_rejection(self) -> None:
        token = trial_emails.unsubscribe_token(self.account_id)
        self.assertEqual(trial_emails.account_id_from_token(token), self.account_id)
        self.assertIsNone(trial_emails.account_id_from_token(""))
        self.assertIsNone(trial_emails.account_id_from_token(f"{self.account_id}.deadbeef"))
        self.assertIsNone(trial_emails.account_id_from_token(token.replace(str(self.account_id), "99", 1)))

    def test_get_confirms_without_changing_anything(self) -> None:
        """Mail scanners and antivirus prefetchers open every link in a message,
        so the GET must not opt anyone out -- it only renders the confirmation."""
        from fastapi.testclient import TestClient
        import app

        real = control_db.connect
        token = trial_emails.unsubscribe_token(self.account_id)
        with mock.patch.object(app.control_db, "connect", lambda *a, **k: real(self.db_path)):
            client = TestClient(app.app)
            shown = client.get("/unsubscribe", params={"token": token})
            rejected = client.get("/unsubscribe", params={"token": "1.not-a-signature"})
        self.assertEqual(shown.status_code, 200)
        self.assertIn("<form", shown.text)
        self.assertIn("noindex", shown.text)
        self.assertEqual(rejected.status_code, 400)
        self.assertNotIn("<form", rejected.text)
        with self.connect() as conn:
            self.assertFalse(trial_emails.is_opted_out(conn, self.account_id))

    def test_post_records_the_optout(self) -> None:
        from fastapi.testclient import TestClient
        import app

        real = control_db.connect
        token = trial_emails.unsubscribe_token(self.account_id)
        with mock.patch.object(app.control_db, "connect", lambda *a, **k: real(self.db_path)):
            client = TestClient(app.app)
            done = client.post("/unsubscribe", data={"token": token})
            rejected = client.post("/unsubscribe", data={"token": "1.not-a-signature"})
        self.assertEqual(done.status_code, 200)
        self.assertIn("Вы отписаны", done.text)
        self.assertEqual(rejected.status_code, 400)
        with self.connect() as conn:
            self.assertTrue(trial_emails.is_opted_out(conn, self.account_id))


    def test_optout_is_recorded_idempotently(self) -> None:
        with self.connect() as conn:
            self.assertFalse(trial_emails.is_opted_out(conn, self.account_id))
            trial_emails.record_optout(conn, self.account_id)
            trial_emails.record_optout(conn, self.account_id)
            self.assertTrue(trial_emails.is_opted_out(conn, self.account_id))
            rows = conn.execute("SELECT * FROM trial_email_optouts").fetchall()
        self.assertEqual(len(rows), 1)


class SendLogTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "seller@shop.ru", "password1234")

    def test_unique_constraint_prevents_a_second_send(self) -> None:
        with self.connect() as conn:
            self.assertTrue(trial_emails.claim_send(conn, self.account_id, "1"))
            self.assertFalse(trial_emails.claim_send(conn, self.account_id, "1"))
            trial_emails.finish_send(conn, self.account_id, "1", delivered=True)
            self.assertFalse(trial_emails.claim_send(conn, self.account_id, "1"))
            rows = conn.execute("SELECT * FROM trial_emails WHERE account_id=?", (self.account_id,)).fetchall()
        self.assertEqual([(r["email_key"], r["status"]) for r in rows], [("1", "sent")])

    def test_failed_send_is_logged_and_not_retried(self) -> None:
        with self.connect() as conn:
            trial_emails.claim_send(conn, self.account_id, "1")
            trial_emails.finish_send(conn, self.account_id, "1", delivered=False)
            log = trial_emails.sent_log(conn, self.account_id)
            self.assertFalse(trial_emails.claim_send(conn, self.account_id, "1"))
        self.assertIn("1", log)

    def test_unknown_key_cannot_be_claimed(self) -> None:
        with self.connect() as conn:
            with self.assertRaises(ValueError):
                trial_emails.claim_send(conn, self.account_id, "42")


def _load_runner():
    sys.path.insert(0, str(GATEWAY_ROOT.parent / "scripts"))
    return importlib.import_module("run_trial_emails")


class RunnerTests(GatewayDbTestCase):
    """The runner must not send anything by default."""

    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        self.runner = _load_runner()
        self._secret = mock.patch.object(config, "INTERNAL_API_SECRET", "unit-test-secret")
        self._secret.start()
        self.addCleanup(self._secret.stop)
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "seller@shop.ru", "password1234")
            conn.execute(
                "UPDATE accounts SET email_verified_at=? WHERE id=?",
                (_iso(datetime.now(timezone.utc)), self.account_id),
            )
            slug = accounts.generate_unique_slug(conn, "seller@shop.ru")
            tenant_id = accounts.create_tenant_instance(conn, self.account_id, slug)
            accounts.mark_tenant_provisioned(conn, tenant_id, self.account_id)
            billing.start_trial(conn, self.account_id, trial_days=14)

    def _patched_connect(self):
        """Point the runner at this test's db without recursing through
        GatewayDbTestCase.connect(), which calls the same function."""
        real = control_db.connect
        return mock.patch.object(control_db, "connect", lambda *a, **k: real(self.db_path))

    def _run(self, send: bool) -> int:
        with self._patched_connect():
            return self.runner.run(send=send)

    def test_dry_run_sends_nothing_and_writes_no_log(self) -> None:
        with mock.patch.object(self.runner.mailer, "send_email") as send_email:
            self.assertEqual(self._run(send=False), 0)
        send_email.assert_not_called()
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM trial_emails").fetchone()["n"], 0)

    def test_dry_run_is_the_default_from_the_command_line(self) -> None:
        with mock.patch.object(self.runner.mailer, "send_email") as send_email, \
                mock.patch.object(sys, "argv", ["run_trial_emails.py"]), self._patched_connect():
            self.assertEqual(self.runner.main(), 0)
        send_email.assert_not_called()

    def test_send_delivers_email_1_once(self) -> None:
        with mock.patch.object(self.runner.mailer, "send_email", return_value=True) as send_email:
            self.assertEqual(self._run(send=True), 0)
            self.assertEqual(send_email.call_count, 1)
            self.assertEqual(send_email.call_args[0][0], "seller@shop.ru")
            self.assertIn("/unsubscribe?token=", send_email.call_args[0][2])
            # Second run the same day: nothing more goes out.
            self._run(send=True)
            self.assertEqual(send_email.call_count, 1)

    def test_opted_out_account_is_skipped(self) -> None:
        with self.connect() as conn:
            trial_emails.record_optout(conn, self.account_id)
        with mock.patch.object(self.runner.mailer, "send_email") as send_email:
            self._run(send=True)
        send_email.assert_not_called()

    def test_excluded_growth_account_is_skipped(self) -> None:
        with self.connect() as conn:
            growth.set_account_exclusion(conn, self.account_id, "test")
            self.assertEqual(self.runner.candidate_account_ids(conn), [])


if __name__ == "__main__":
    unittest.main()
