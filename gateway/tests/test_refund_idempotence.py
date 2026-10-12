"""Durable idempotency of outgoing refunds: a stateful fake of the provider, a disposable DB, no real calls."""
import asyncio
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

import admin_routes
import billing_routes
import yookassa_client
from logic import accounts, billing, db, refund_intents

from .base import GatewayDbTestCase


class FakeYooKassa:
    """Behaves like the provider: one refund per Idempotence-Key, amount and total checked, replies can be lost."""

    def __init__(self, price_cents=300000):
        self.price, self.by_key, self.refunds, self.calls = price_cents, {}, [], []
        self.lose_reply_once = False
        self.fail_with = None
        self.slow = None
        self.lock = threading.Lock()

    def create_refund(self, payment_id, amount_rub=None, idempotence_key=None):
        self.calls.append((payment_id, amount_rub, idempotence_key))
        if self.fail_with is not None:
            raise self.fail_with
        if self.slow:
            self.slow.wait(timeout=5)
        with self.lock:
            cents = round(amount_rub * 100)
            if idempotence_key in self.by_key:
                created = self.by_key[idempotence_key]
                assert created["cents"] == cents, "same key with a different body"
            else:
                if sum(r["cents"] for r in self.refunds) + cents > self.price:
                    raise yookassa_client.YooKassaError("refund exceeds payment", 400)
                created = {"id": f"refund-{len(self.refunds) + 1}", "cents": cents}
                self.refunds.append(created)
                self.by_key[idempotence_key] = created
        if self.lose_reply_once:
            self.lose_reply_once = False
            raise yookassa_client.YooKassaError("YooKassa refund request got no answer")  # it did happen at the provider
        return {"id": created["id"], "payment_id": payment_id, "status": "pending",
                "amount": {"value": f"{created['cents'] / 100:.2f}", "currency": "RUB"}}

    def list_refunds(self, payment_id):
        return [{"id": r["id"], "payment_id": payment_id, "status": "pending",
                 "amount": {"value": f"{r['cents'] / 100:.2f}", "currency": "RUB"}} for r in self.refunds]


class RefundIntentTests(GatewayDbTestCase):
    def setUp(self):
        super().setUp()
        self.init_db()
        with self.connect() as c:
            self.owner = accounts.create_account(c, "intent@example.test", "password1234")
            billing.record_payment_attempt(c, self.owner, "paid", "manual", 3000)
            billing.apply_successful_payment(c, self.owner, "paid", period_days=30)
        original = db.connect
        for patch in (mock.patch.object(admin_routes.control_db, "connect", side_effect=lambda *a, **k: original(self.db_path)),
                      mock.patch.object(billing_routes.control_db, "connect", side_effect=lambda *a, **k: original(self.db_path)),
                      mock.patch.object(admin_routes, "_require_admin", return_value={"id": self.owner}),
                      mock.patch.object(admin_routes.csrf, "verify", return_value=True),
                      mock.patch.object(admin_routes.logger, "exception"),
                      mock.patch.object(admin_routes.logger, "warning")):
            self.enterContext(patch)
        self.provider = FakeYooKassa()
        self.enterContext(mock.patch.object(yookassa_client, "create_refund", side_effect=self.provider.create_refund))
        self.enterContext(mock.patch.object(yookassa_client, "list_refunds", side_effect=self.provider.list_refunds))

    def refund(self, amount=None):
        return admin_routes.admin_payment_refund(None, "paid", amount, "csrf")

    def refund_with(self, amount, request_id):
        return admin_routes.admin_payment_refund(None, "paid", amount, "csrf", request_id)

    def intents(self):
        with db.connect(self.db_path) as c:
            return [dict(r) for r in c.execute("SELECT * FROM refund_intents ORDER BY created_at, intent_id")]

    def rows(self):
        with db.connect(self.db_path) as c:
            return [dict(r) for r in c.execute("SELECT * FROM refunds")]

    def age(self, intent_id, hours):
        old = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
        with db.connect(self.db_path) as c:
            c.execute("UPDATE refund_intents SET created_at=? WHERE intent_id=?", (old, intent_id))

    def test_the_intent_is_committed_before_the_provider_is_called_and_is_its_key(self):
        seen = []

        def spy(payment_id, amount_rub=None, idempotence_key=None):
            seen.append([i for i in self.intents()])  # read through another connection: it must already be committed
            return self.provider.create_refund(payment_id, amount_rub, idempotence_key)
        with mock.patch.object(yookassa_client, "create_refund", side_effect=spy):
            self.refund()
        (rows,) = seen
        assert len(rows) == 1 and rows[0]["state"] == "claimed" and rows[0]["amount_cents"] == 300000
        assert self.provider.calls[0][2] == rows[0]["intent_id"]
        done = self.intents()[0]
        assert done["state"] == "bound" and done["provider_refund_id"] == "refund-1"
        assert [r["yookassa_refund_id"] for r in self.rows()] == ["refund-1"]

    def test_a_lost_reply_is_resumed_with_the_same_key_and_refunds_once(self):
        self.provider.lose_reply_once = True
        self.refund(1000)  # provider refunded, we never saw the answer
        (intent,) = self.intents()
        assert intent["state"] == "unknown" and self.rows() == []
        self.refund(1000)  # the operator tries again
        assert len(self.provider.refunds) == 1, "the provider was asked to refund twice"
        assert [c[2] for c in self.provider.calls] == [intent["intent_id"]] * 2
        assert self.intents()[0]["state"] == "bound" and len(self.rows()) == 1

    def test_a_crash_between_provider_and_booking_is_recovered_without_a_second_refund(self):
        real = billing.apply_refund
        with mock.patch.object(billing, "apply_refund", side_effect=RuntimeError("process died here")):
            self.refund(1500)
        (intent,) = self.intents()
        assert intent["state"] == "unknown" and len(self.provider.refunds) == 1 and self.rows() == []
        with mock.patch.object(billing, "apply_refund", side_effect=real):
            self.refund(1500)
        assert len(self.provider.refunds) == 1 and len(self.rows()) == 1 and self.intents()[0]["state"] == "bound"

    def test_a_second_request_while_the_first_is_in_flight_shares_its_key(self):
        arrivals, release = [], threading.Event()
        inner = self.provider.create_refund

        def gated(payment_id, amount_rub=None, idempotence_key=None):
            arrivals.append(idempotence_key)
            release.wait(timeout=10)
            return inner(payment_id, amount_rub, idempotence_key)
        with mock.patch.object(yookassa_client, "create_refund", side_effect=gated):
            first = threading.Thread(target=self.refund, args=(1000,))
            first.start()
            for _ in range(500):
                if arrivals:
                    break
                threading.Event().wait(0.01)
            assert arrivals, "the first request never reached the provider"  # in flight, nothing is booked yet
            second = threading.Thread(target=self.refund, args=(1000,))
            second.start()
            for _ in range(500):
                if len(arrivals) == 2:
                    break
                threading.Event().wait(0.01)
            assert len(arrivals) == 2 and arrivals[0] == arrivals[1], "the second request did not share the first one's key"
            release.set()
            first.join(timeout=15)
            second.join(timeout=15)
        assert len(self.provider.refunds) == 1, "the in-flight request was refunded twice"
        assert len(self.intents()) == 1 and len(self.rows()) == 1

    def test_the_same_request_id_is_the_same_refund_however_late_it_is_repeated(self):
        self.refund_with(1000, "ticket-17")
        assert len(self.provider.refunds) == 1
        calls = len(self.provider.calls)
        self.refund_with(1000, "ticket-17")  # a retry long after the first one was booked
        self.refund_with(1000, "ticket-17")
        assert len(self.provider.calls) == calls and len(self.provider.refunds) == 1 and len(self.rows()) == 1
        self.refund_with(1000, "ticket-18")  # a different request is a different refund
        assert len(self.provider.refunds) == 2

    def test_a_request_id_cannot_be_reused_for_another_amount_or_after_it_was_refused(self):
        self.refund_with(1000, "ticket-1")
        self.refund_with(1500, "ticket-1")
        assert len(self.provider.refunds) == 1
        self.provider.fail_with = yookassa_client.YooKassaError("bad request", 400)
        self.refund_with(500, "ticket-2")
        self.provider.fail_with = None
        calls = len(self.provider.calls)
        self.refund_with(500, "ticket-2")  # closed as refused: a new request id is required
        assert len(self.provider.calls) == calls

    def test_a_request_id_resumes_an_uncertain_attempt_with_its_original_key(self):
        self.provider.lose_reply_once = True
        self.refund_with(800, "ticket-9")
        self.refund_with(800, "ticket-9")
        assert len(self.provider.refunds) == 1 and len({c[2] for c in self.provider.calls}) == 1
        assert self.intents()[0]["state"] == "bound"

    def test_in_flight_amounts_are_reserved_so_another_amount_cannot_overspend(self):
        self.provider.lose_reply_once = True
        self.refund(2000)  # unknown outcome: 2000 stays reserved
        self.refund(2000.01 if False else 1500)  # 2000 + 1500 > 3000 payment
        assert len(self.provider.refunds) == 1, "a second refund was sent over a reserved amount"
        self.refund(1000)  # exactly the rest fits
        assert [r["cents"] for r in self.provider.refunds] == [200000, 100000]

    def test_a_definitive_refusal_releases_the_reservation_and_gets_a_fresh_key(self):
        self.provider.fail_with = yookassa_client.YooKassaError("bad request", 400)
        self.refund(1000)
        assert self.intents()[0]["state"] == "rejected"
        self.provider.fail_with = None
        self.refund(1000)
        keys = [c[2] for c in self.provider.calls]
        assert keys[0] != keys[1] and len(self.provider.refunds) == 1 and len(self.rows()) == 1

    def test_uncertain_failures_stay_reserved_and_resumable(self):
        for error in (yookassa_client.YooKassaError("boom", 500), yookassa_client.YooKassaError("slow down", 429),
                      yookassa_client.YooKassaError("no answer"), yookassa_client.YooKassaError("conflict", 409), RuntimeError("socket")):
            with self.subTest(error=str(error)):
                with db.connect(self.db_path) as c:
                    c.execute("DELETE FROM refund_intents")
                self.provider.fail_with = error
                self.refund(500)
                (intent,) = self.intents()
                assert intent["state"] == "unknown", "an uncertain outcome must not release the reservation"

    def test_a_stale_open_attempt_is_never_resent_and_is_reconciled_with_the_provider(self):
        self.provider.lose_reply_once = True
        self.refund(1200)
        (intent,) = self.intents()
        self.age(intent["intent_id"], 30)
        calls = len(self.provider.calls)
        self.refund(1200)
        assert len(self.provider.calls) == calls, "an attempt older than the key window was resent"
        reply = admin_routes.admin_reconcile_refund_intent(None, intent["intent_id"], "csrf")
        assert b"bound" in reply.body
        assert self.intents()[0]["state"] == "bound" and self.intents()[0]["provider_refund_id"] == "refund-1"
        assert [r["yookassa_refund_id"] for r in self.rows()] == ["refund-1"] and self.rows()[0]["amount_rub"] == 1200

    def test_a_stale_attempt_that_never_reached_the_provider_is_released_by_reconcile(self):
        self.provider.fail_with = yookassa_client.YooKassaError("no answer")
        self.refund(1200)
        (intent,) = self.intents()
        self.age(intent["intent_id"], 30)
        reply = admin_routes.admin_reconcile_refund_intent(None, intent["intent_id"], "csrf")
        assert b"stale" in reply.body and self.intents()[0]["state"] == "stale"
        self.provider.fail_with = None
        self.refund(3000)  # the whole payment is refundable again
        assert len(self.provider.refunds) == 1 and self.provider.refunds[0]["cents"] == 300000

    def test_a_young_open_attempt_is_not_released_by_reconcile(self):
        self.provider.fail_with = yookassa_client.YooKassaError("no answer")
        self.refund(1200)
        (intent,) = self.intents()
        reply = admin_routes.admin_reconcile_refund_intent(None, intent["intent_id"], "csrf")
        assert b"unknown" in reply.body and self.intents()[0]["state"] == "unknown"

    def test_a_verified_webhook_closes_the_open_attempt(self):
        from .test_billing_routes_webhook import _post_request, _real_connect
        self.provider.lose_reply_once = True
        self.refund(1000)  # the provider refunded, the reply was lost, the operator never retried
        assert self.intents()[0]["state"] == "unknown" and self.rows() == []
        verified = {"id": "refund-1", "payment_id": "paid", "status": "succeeded", "amount": {"value": "1000.00", "currency": "RUB"}}
        billing_routes.webhook_global_limiter.clear()
        billing_routes.webhook_source_limiter.clear()
        with mock.patch.object(billing_routes.config, "YOOKASSA_WEBHOOK_CIDRS", ("198.51.100.0/24",)),              mock.patch.object(yookassa_client, "get_refund", return_value=verified):
            reply = asyncio.run(billing_routes.yookassa_webhook(_post_request({"event": "refund.succeeded", "object": {"id": "refund-1"}})))
        assert reply.status_code == 200
        assert self.intents()[0]["state"] == "bound" and self.intents()[0]["provider_refund_id"] == "refund-1"
        assert [r["yookassa_refund_id"] for r in self.rows()] == ["refund-1"]

    def test_full_refund_retry_resumes_the_open_whole_payment_attempt(self):
        self.provider.lose_reply_once = True
        self.refund()  # no amount: everything refundable, reply lost
        self.refund()  # retry with no amount: remaining is now zero, the open attempt is resumed
        assert len(self.provider.refunds) == 1 and len({c[2] for c in self.provider.calls}) == 1
        assert self.intents()[0]["state"] == "bound"

    def test_two_equal_partial_refunds_are_both_allowed_once_the_first_is_booked(self):
        self.refund(1000)
        self.refund(1000)
        assert [r["cents"] for r in self.provider.refunds] == [100000, 100000]
        assert len(self.rows()) == 2 and {i["state"] for i in self.intents()} == {"bound"}

    def test_the_database_itself_refuses_a_second_open_attempt_for_the_same_payment_and_amount(self):
        self.provider.fail_with = yookassa_client.YooKassaError("no answer")
        self.refund(700)
        with db.connect(self.db_path) as c:
            row = dict(c.execute("SELECT * FROM refund_intents").fetchone())
            with pytest.raises(sqlite3.IntegrityError):
                c.execute("INSERT INTO refund_intents(intent_id,account_id,payment_id,provider_payment_id,amount_cents,state,created_at,updated_at) "
                          "VALUES ('x',?,?,?,?,'claimed','now','now')", (row["account_id"], row["payment_id"], row["provider_payment_id"], row["amount_cents"]))

    def test_provider_reply_that_does_not_match_keeps_the_amount_reserved(self):
        with mock.patch.object(yookassa_client, "create_refund", return_value={"id": "r", "payment_id": "other", "status": "pending",
                                                                                "amount": {"value": "1000.00", "currency": "RUB"}}):
            self.refund(1000)
        assert self.intents()[0]["state"] == "unknown" and self.rows() == []

    def test_a_refund_canceled_by_the_provider_releases_the_reservation(self):
        with mock.patch.object(yookassa_client, "create_refund", return_value={"id": "r1", "payment_id": "paid", "status": "canceled",
                                                                                "amount": {"value": "1000.00", "currency": "RUB"}}):
            self.refund(1000)
        assert self.intents()[0]["state"] == "rejected"
        self.refund(3000)
        assert self.provider.refunds[-1]["cents"] == 300000
