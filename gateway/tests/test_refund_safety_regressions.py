"""Financial regression cases use disposable SQLite and provider doubles only."""
from unittest import mock

import pytest
import requests
import admin_routes
import yookassa_client
from logic import db, refund_intents


@pytest.fixture
def refunds():
    from .test_refund_idempotence import RefundIntentTests
    harness = RefundIntentTests()
    harness.setUp()
    try:
        yield harness
    finally:
        harness.doCleanups()
        harness.tearDown()


def test_replayed_old_refund_does_not_close_new_equal_amount_intent(refunds):
    refunds.refund_with(1000, "old")
    old = refunds.intents()[0]
    refunds.provider.lose_reply_once = True
    refunds.refund_with(1000, "new")
    new = next(i for i in refunds.intents() if i["request_ref"] == "new")
    verified = refunds.provider.list_refunds("paid")[0]
    with db.connect(refunds.db_path) as conn:
        refund_intents.bind_from_webhook(conn, old["payment_id"], 100000, "refund-1", verified)
    assert next(i for i in refunds.intents() if i["intent_id"] == new["intent_id"])["state"] == "unknown"


def test_historical_booked_refund_does_not_close_new_intent(refunds):
    refunds.refund_with(1000, "old")
    refunds.provider.lose_reply_once = True
    refunds.refund_with(1000, "new")
    intent = next(i for i in refunds.intents() if i["request_ref"] == "new")
    historical = refunds.provider.list_refunds("paid")[:1]
    with db.connect(refunds.db_path) as conn:
        state, found = refund_intents.reconcile(conn, intent["intent_id"], historical, lambda value: round(float(value) * 100))
    assert state == "unknown" and found is None
    assert next(i for i in refunds.intents() if i["intent_id"] == intent["intent_id"])["state"] == "unknown"


def test_different_explicit_request_ref_cannot_alias_open_intent(refunds):
    refunds.provider.lose_reply_once = True
    refunds.refund_with(1000, "A")
    calls = len(refunds.provider.calls)
    refunds.refund_with(1000, "B")
    refunds.refund_with(1000, "B")
    assert len(refunds.provider.calls) == calls
    assert len(refunds.provider.refunds) == 1
    assert refunds.intents()[0]["request_ref"] == "A"


def test_resumed_definitive_error_preserves_original_unknown_outcome(refunds):
    refunds.provider.lose_reply_once = True
    refunds.refund_with(1000, "A")
    refunds.provider.fail_with = yookassa_client.YooKassaError("synthetic auth rejection", 401)
    refunds.refund_with(1000, "A")
    assert refunds.intents()[0]["state"] == "unknown"
    refunds.provider.fail_with = None
    refunds.refund_with(1000, "B")
    assert len(refunds.provider.refunds) == 1


def test_known_pending_refund_can_be_reconciled_only_to_its_exact_marker(refunds):
    refunds.provider.lose_reply_once = True
    refunds.refund_with(1000, "A")
    intent = refunds.intents()[0]
    verified = refunds.provider.list_refunds("paid")[0]
    assert verified.get("description") == intent.get("provider_description") and verified.get("description")
    with db.connect(refunds.db_path) as conn:
        state, found = refund_intents.reconcile(conn, intent["intent_id"], [verified], lambda value: round(float(value) * 100))
    assert state == "bound" and found["id"] == "refund-1"


@pytest.mark.parametrize("change", [
    {"description": "unrelated"}, {"created_at": "2020-01-01T00:00:00Z"},
    {"created_at": None}, {"amount": {"value": "1000.00", "currency": "USD"}},
])
def test_invalid_or_unrelated_candidate_never_releases_reservation(refunds, change):
    refunds.provider.lose_reply_once = True
    refunds.refund_with(1000, "A")
    intent = refunds.intents()[0]
    candidate = dict(refunds.provider.list_refunds("paid")[0], **change)
    refunds.age(intent["intent_id"], 30)
    with db.connect(refunds.db_path) as conn:
        with pytest.raises(ValueError):
            refund_intents.reconcile(conn, intent["intent_id"], [candidate], lambda value: round(float(value) * 100))
    assert refunds.intents()[0]["state"] == "unknown"


def _response(payload):
    response = mock.Mock(status_code=200)
    response.json.return_value = payload
    return response


def test_refund_list_consumes_all_pages(monkeypatch):
    monkeypatch.setattr(yookassa_client, "_auth", lambda: ("synthetic", "synthetic"))
    get = mock.Mock(side_effect=[_response({"type": "list", "items": [{"id": "new"}], "next_cursor": "older"}),
                                _response({"type": "list", "items": [{"id": "old"}]})])
    monkeypatch.setattr(requests, "get", get)
    assert yookassa_client.list_refunds("payment") == [{"id": "new"}, {"id": "old"}]
    assert get.call_args_list[1].kwargs["params"]["cursor"] == "older"


@pytest.mark.parametrize("payload", [
    {"type": "list"}, {"type": "list", "items": {}},
    {"type": "unexpected", "items": []}, {"type": "list", "items": [None]},
    {"type": "list", "items": [], "next_cursor": "more"},
])
def test_malformed_or_incomplete_refund_list_fails_closed(monkeypatch, payload):
    monkeypatch.setattr(yookassa_client, "_auth", lambda: ("synthetic", "synthetic"))
    monkeypatch.setattr(requests, "get", mock.Mock(return_value=_response(payload)))
    with pytest.raises(yookassa_client.YooKassaError):
        yookassa_client.list_refunds("payment")


def test_legacy_intent_migration_and_retry_does_not_change_provider_body(refunds):
    refunds.provider.fail_with = yookassa_client.YooKassaError("unknown")
    refunds.refund_with(1000, "legacy")
    with db.connect(refunds.db_path) as conn:
        conn.execute("UPDATE refund_intents SET provider_description='' WHERE request_ref='legacy'")
    with mock.patch.object(yookassa_client, "create_refund", return_value={}) as create:
        refunds.refund_with(1000, "legacy")
    assert "description" not in create.call_args.kwargs


def test_additive_migration_preserves_legacy_intent_and_key(refunds):
    refunds.provider.fail_with = yookassa_client.YooKassaError("unknown")
    refunds.refund_with(1000, "legacy")
    original = refunds.intents()[0]
    with db.connect(refunds.db_path) as conn:
        conn.execute("ALTER TABLE refund_intents DROP COLUMN provider_description")
        conn.execute("ALTER TABLE refund_intents DROP COLUMN attempt_count")
        refund_intents.initialize(conn)
        refund_intents.initialize(conn)
    migrated = refunds.intents()[0]
    assert migrated["intent_id"] == original["intent_id"] and migrated["state"] == "unknown"
    assert migrated["provider_description"] == "" and migrated["attempt_count"] == 1


def test_parallel_retry_prevents_first_call_refusal_from_releasing_money(refunds):
    refunds.provider.fail_with = yookassa_client.YooKassaError("unknown")
    refunds.refund_with(1000, "parallel")
    intent = refunds.intents()[0]
    with db.connect(refunds.db_path) as conn:
        conn.execute("UPDATE refund_intents SET state='claimed',attempt_count=2")
        refund_intents.mark_rejected(conn, intent["intent_id"], "first caller got 401")
    assert refunds.intents()[0]["state"] == "unknown"


def test_long_request_id_is_rejected_without_silent_collision(refunds):
    refunds.refund_with(1000, "A" * 64 + "first")
    refunds.refund_with(1000, "A" * 64 + "second")
    assert refunds.intents() == [] and refunds.provider.calls == []


def test_malformed_or_naive_intent_timestamp_is_never_resent(refunds):
    refunds.provider.fail_with = yookassa_client.YooKassaError("unknown")
    refunds.refund_with(1000, "A")
    calls = len(refunds.provider.calls)
    for stamp in ("invalid", "2026-10-12T11:00:00"):
        with db.connect(refunds.db_path) as conn:
            conn.execute("UPDATE refund_intents SET created_at=?", (stamp,))
        refunds.refund_with(1000, "A")
        assert len(refunds.provider.calls) == calls


def test_resend_cutoff_does_not_release_reservation_before_provider_key_expires(refunds):
    refunds.provider.fail_with = yookassa_client.YooKassaError("unknown")
    refunds.refund_with(1000, "A")
    intent = refunds.intents()[0]
    refunds.age(intent["intent_id"], 23.5)
    response = admin_routes.admin_reconcile_refund_intent(None, intent["intent_id"], "csrf")
    assert b"unknown" in response.body and refunds.intents()[0]["state"] == "unknown"


def test_cursor_cycle_and_excess_pages_fail_closed(monkeypatch):
    monkeypatch.setattr(yookassa_client, "_auth", lambda: ("synthetic", "synthetic"))
    for payloads in ([{"type": "list", "items": [{"id": "one"}], "next_cursor": "same"}] * 2,
                     [{"type": "list", "items": [{"id": str(i)}], "next_cursor": str(i)} for i in range(100)]):
        get = mock.Mock(side_effect=[_response(p) for p in payloads])
        monkeypatch.setattr(requests, "get", get)
        with pytest.raises(yookassa_client.YooKassaError):
            yookassa_client.list_refunds("payment")
        assert get.call_count <= 100


def test_list_deadline_failure_does_not_return_partial_results(monkeypatch):
    monkeypatch.setattr(yookassa_client, "_auth", lambda: ("synthetic", "synthetic"))
    monkeypatch.setattr("time.monotonic", mock.Mock(side_effect=[0, 0, 61]))
    monkeypatch.setattr(requests, "get", mock.Mock(return_value=_response({"type": "list", "items": [{"id": "one"}], "next_cursor": "two"})))
    with pytest.raises(yookassa_client.YooKassaError, match="deadline"):
        yookassa_client.list_refunds("payment")


def test_new_provider_body_contains_persisted_marker(monkeypatch):
    monkeypatch.setattr(yookassa_client, "_auth", lambda: ("synthetic", "synthetic"))
    post = mock.Mock(return_value=_response({"id": "refund"}))
    monkeypatch.setattr(requests, "post", post)
    yookassa_client.create_refund("payment", 1000, "stable-key", description="MARKETSHELPER refund stable-key")
    assert post.call_args.kwargs["json"]["description"] == "MARKETSHELPER refund stable-key"
    assert post.call_args.kwargs["headers"]["Idempotence-Key"] == "stable-key"


@pytest.mark.parametrize("description", [None, "MARKETSHELPER refund another-intent"])
def test_direct_provider_reply_cannot_bind_missing_or_wrong_marker(refunds, description):
    response = {"id": "old-refund", "payment_id": "paid", "status": "pending",
                "amount": {"value": "1000.00", "currency": "RUB"}, "description": description}
    with mock.patch.object(yookassa_client, "create_refund", return_value=response):
        refunds.refund_with(1000, "A")
    assert refunds.rows() == []
    assert refunds.intents()[0]["state"] == "unknown"
