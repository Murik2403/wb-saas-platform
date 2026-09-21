"""Outbox transport contract; every HTTP call is replaced by a local mock.

TestCurrentUrlPolicy is deliberately separate: its HTTPS/localhost policy
may change when the gateway's trusted private transport is confirmed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR.parents[1] / "tenant-app"))
sys.path.insert(0, str(API_DIR))

import growth_delivery
from growth_activation import initialize_growth_schema


NOW = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)
OCCURRED_AT = (NOW - timedelta(days=3)).isoformat()
REAL_CONNECT = sqlite3.connect


class FrozenDatetime(datetime):
    current = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def transport(monkeypatch):
    configuration = SimpleNamespace(
        INTERNAL_API_URL="https://gateway.example.test",
        INTERNAL_API_SECRET="test-only-scoped-credential",
        TENANT_SLUG="test-shop",
        IS_DEMO=False,
    )
    post = Mock(return_value=SimpleNamespace(status_code=204))
    # The second guard also blocks accidental use of another requests entry
    # point if the implementation changes; no test can contact a real host.
    monkeypatch.setattr(requests.sessions.Session, "request", Mock(side_effect=AssertionError("Real HTTP is forbidden in outbox unit tests")))
    monkeypatch.setattr(growth_delivery.requests, "post", post)
    monkeypatch.setattr(growth_delivery, "config", configuration)
    monkeypatch.setattr(growth_delivery, "datetime", FrozenDatetime)
    monkeypatch.setattr(FrozenDatetime, "current", NOW)
    return configuration, post


@pytest.fixture
def outbox_db(tmp_path):
    path = tmp_path / "outbox.sqlite3"
    initialize_growth_schema(path)
    with REAL_CONNECT(path) as conn:
        conn.execute(
            "INSERT INTO growth_event_outbox(event_key,event_name,occurred_at) VALUES(?,?,?)",
            ("first_useful_report", "first_useful_report", OCCURRED_AT),
        )
    return path


def row(path, key="first_useful_report"):
    with REAL_CONNECT(path) as conn:
        conn.row_factory = sqlite3.Row
        result = conn.execute("SELECT * FROM growth_event_outbox WHERE event_key=?", (key,)).fetchone()
        return dict(result) if result else None


@pytest.mark.parametrize("field,value", [
    ("INTERNAL_API_URL", ""),
    ("INTERNAL_API_SECRET", ""),
    ("TENANT_SLUG", ""),
    ("IS_DEMO", True),
])
def test_disabled_or_demo_never_claims_event_or_uses_network(outbox_db, transport, field, value):
    configuration, post = transport
    setattr(configuration, field, value)
    before = row(outbox_db)
    assert growth_delivery.deliver_pending_growth_events(outbox_db) == {"status": "disabled", "delivered": 0}
    assert row(outbox_db) == before
    post.assert_not_called()


def test_disabled_transport_does_not_create_database(tmp_path, transport):
    configuration, post = transport
    configuration.INTERNAL_API_SECRET = ""
    path = tmp_path / "must-not-be-created.sqlite3"
    assert growth_delivery.deliver_pending_growth_events(path)["status"] == "disabled"
    assert not path.exists()
    post.assert_not_called()


def test_only_204_marks_delivered_and_second_run_does_not_send_again(outbox_db, transport):
    _, post = transport
    assert growth_delivery.deliver_pending_growth_events(outbox_db) == {"status": "complete", "attempted": 1, "delivered": 1}
    saved = row(outbox_db)
    assert saved["occurred_at"] == OCCURRED_AT
    assert saved["delivered_at"] == NOW.isoformat()
    assert saved["last_attempt_at"] == NOW.isoformat()
    assert saved["attempt_count"] == 1
    post.assert_called_once_with(
        "https://gateway.example.test/internal/growth/event",
        headers={"X-Internal-Secret": "test-only-scoped-credential"},
        json={"slug": "test-shop", "event_key": "first_useful_report", "event_name": "first_useful_report", "occurred_at": OCCURRED_AT},
        timeout=(2, 3),
        allow_redirects=False,
    )
    assert growth_delivery.deliver_pending_growth_events(outbox_db) == {"status": "complete", "attempted": 0, "delivered": 0}
    assert post.call_count == 1


@pytest.mark.parametrize("status", [200, 202, 301, 403, 422, 500])
def test_non_204_keeps_original_occurrence_pending(outbox_db, transport, status):
    _, post = transport
    post.return_value.status_code = status
    result = growth_delivery.deliver_pending_growth_events(outbox_db)
    assert result == {"status": "complete", "attempted": 1, "delivered": 0}
    saved = row(outbox_db)
    assert saved["occurred_at"] == OCCURRED_AT
    assert saved["delivered_at"] is None
    assert saved["last_attempt_at"] == NOW.isoformat()
    assert saved["attempt_count"] == 1
    assert post.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize("exception", [requests.Timeout("test timeout"), requests.ConnectionError("test connection failure")])
def test_network_exception_keeps_event_pending(outbox_db, transport, exception):
    _, post = transport
    post.side_effect = exception
    assert growth_delivery.deliver_pending_growth_events(outbox_db)["delivered"] == 0
    saved = row(outbox_db)
    assert saved["delivered_at"] is None
    assert saved["occurred_at"] == OCCURRED_AT
    assert saved["attempt_count"] == 1


def test_retry_waits_60_seconds_and_retains_original_occurrence(outbox_db, transport, monkeypatch):
    _, post = transport
    post.return_value.status_code = 503
    assert growth_delivery.deliver_pending_growth_events(outbox_db)["attempted"] == 1
    monkeypatch.setattr(FrozenDatetime, "current", NOW + timedelta(seconds=59))
    assert growth_delivery.deliver_pending_growth_events(outbox_db)["attempted"] == 0
    assert post.call_count == 1
    assert row(outbox_db)["attempt_count"] == 1
    monkeypatch.setattr(FrozenDatetime, "current", NOW + timedelta(seconds=61))
    post.return_value.status_code = 204
    assert growth_delivery.deliver_pending_growth_events(outbox_db)["delivered"] == 1
    assert post.call_count == 2
    assert row(outbox_db)["attempt_count"] == 2
    assert row(outbox_db)["occurred_at"] == OCCURRED_AT
    assert post.call_args.kwargs["json"]["occurred_at"] == OCCURRED_AT


def test_all_supported_pending_growth_events_are_eligible(outbox_db, transport):
    _, post = transport
    with REAL_CONNECT(outbox_db) as conn:
        conn.execute(
            "INSERT INTO growth_event_outbox(event_key,event_name,occurred_at) VALUES('first_sync_succeeded','first_sync_succeeded',?)",
            ((NOW - timedelta(days=5)).isoformat(),),
        )
        conn.execute(
            "INSERT INTO growth_event_outbox(event_key,event_name,occurred_at,delivered_at) VALUES('already-delivered','first_useful_report',?,?)",
            ((NOW - timedelta(days=4)).isoformat(), (NOW - timedelta(days=1)).isoformat()),
        )
    result = growth_delivery.deliver_pending_growth_events(outbox_db)
    assert result == {"status": "complete", "attempted": 2, "delivered": 2}
    assert post.call_count == 2
    assert row(outbox_db, "first_sync_succeeded")["attempt_count"] == 1
    assert row(outbox_db, "first_sync_succeeded")["delivered_at"] is not None
    assert row(outbox_db, "already-delivered")["attempt_count"] == 0


def test_missing_outbox_table_returns_storage_unavailable_without_http(tmp_path, transport):
    _, post = transport
    path = tmp_path / "legacy.sqlite3"
    with REAL_CONNECT(path) as conn:
        conn.execute("CREATE TABLE preserved(value TEXT)")
        conn.execute("INSERT INTO preserved VALUES('keep')")
    assert growth_delivery.deliver_pending_growth_events(path) == {"status": "storage_unavailable", "delivered": 0}
    post.assert_not_called()
    with REAL_CONNECT(path) as conn:
        assert conn.execute("SELECT value FROM preserved").fetchone()[0] == "keep"


def test_connection_error_returns_storage_unavailable_without_http(outbox_db, transport, monkeypatch):
    _, post = transport
    monkeypatch.setattr(growth_delivery.sqlite3, "connect", Mock(side_effect=sqlite3.OperationalError("test unavailable storage")))
    assert growth_delivery.deliver_pending_growth_events(outbox_db) == {"status": "storage_unavailable", "delivered": 0}
    post.assert_not_called()
    assert row(outbox_db)["attempt_count"] == 0


def test_failed_delivery_mark_after_204_remains_retryable(outbox_db, transport, monkeypatch):
    _, post = transport
    attempts = 0

    def flaky_connect(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise sqlite3.OperationalError("test write unavailable after acknowledgement")
        return REAL_CONNECT(*args, **kwargs)

    monkeypatch.setattr(growth_delivery.sqlite3, "connect", flaky_connect)
    result = growth_delivery.deliver_pending_growth_events(outbox_db)
    assert result == {"status": "complete", "attempted": 1, "delivered": 0}
    post.assert_called_once()
    assert row(outbox_db)["delivered_at"] is None
    assert row(outbox_db)["occurred_at"] == OCCURRED_AT


class TestCurrentUrlPolicy:
    """Current policy only; update this group if trusted private HTTP is enabled."""

    @pytest.mark.parametrize("url", [
        "https://user:password@gateway.example.test", "https://gateway.example.test?token=example",
        "https://gateway.example.test#fragment", "ftp://gateway.example.test",
    ])
    def test_invalid_configuration_never_sends_credentials(self, outbox_db, transport, url):
        configuration, post = transport
        configuration.INTERNAL_API_URL = url
        assert growth_delivery.deliver_pending_growth_events(outbox_db)["status"] == "invalid_configuration"
        post.assert_not_called()
        assert row(outbox_db)["attempt_count"] == 0

    def test_untrusted_remote_plain_http_is_not_used(self, outbox_db, transport):
        configuration, post = transport
        configuration.INTERNAL_API_URL = "http://public.example.test"
        assert growth_delivery.deliver_pending_growth_events(outbox_db)["status"] == "https_required"
        post.assert_not_called()

    @pytest.mark.parametrize("url", ["http://localhost:8000", "http://127.0.0.1:8000"])
    def test_loopback_http_is_available_for_local_verification(self, outbox_db, transport, url):
        configuration, post = transport
        configuration.INTERNAL_API_URL = url
        assert growth_delivery.deliver_pending_growth_events(outbox_db)["delivered"] == 1
        assert post.call_args.args[0] == url + "/internal/growth/event"
