"""Tests for the /webhooks/yookassa route in billing_routes.py.

Follows the same convention as test_csrf.py: a bare starlette Request built
from a hand-crafted ASGI scope/receive pair, not a full FastAPI TestClient
(none of this codebase's tests hit real HTTP routes that way).

The single highest-value test here (test_forged_status_in_body_is_ignored)
encodes the module's own documented security invariant: YooKassa webhook
notifications are unsigned, so the POST body's own "status" field must never
be trusted -- only whatever the authenticated get_payment() call returns.
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from fastapi import Request

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

import billing_routes  # noqa: E402
import yookassa_client  # noqa: E402
from logic import accounts, billing  # noqa: E402
from logic import db as control_db  # noqa: E402

from .base import GatewayDbTestCase  # noqa: E402

_real_connect = control_db.connect  # captured before any test patches billing_routes.control_db.connect


def _post_request(payload: dict) -> Request:
    body = json.dumps(payload).encode("utf-8")
    sent = {"done": False}

    async def receive():
        if sent["done"]:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent["done"] = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/webhooks/yookassa",
        "headers": [(b"content-type", b"application/json")],
    }
    return Request(scope, receive)


def _raw_body_request(raw: bytes) -> Request:
    sent = {"done": False}

    async def receive():
        if sent["done"]:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent["done"] = True
        return {"type": "http.request", "body": raw, "more_body": False}

    scope = {"type": "http", "method": "POST", "path": "/webhooks/yookassa", "headers": []}
    return Request(scope, receive)


class YooKassaWebhookTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "seller@shop.ru", "password1234")
            billing.start_trial(conn, self.account_id, trial_days=14)
            billing.record_payment_attempt(conn, self.account_id, "yk-1", "initial", 2990.0)

        # billing_routes calls control_db.connect() with no arguments; redirect
        # that to this test's isolated sqlite file for the duration of each test.
        # Patches the shared logic.db module's connect(), so it must tolerate
        # both call shapes that hit it during a test: billing_routes' own
        # no-arg `control_db.connect()` and this fixture's own
        # `self.connect()` (via GatewayDbTestCase.connect -> control_db.connect(self.db_path)).
        self.connect_patcher = mock.patch(
            "billing_routes.control_db.connect", side_effect=lambda *a, **k: _real_connect(self.db_path)
        )
        self.connect_patcher.start()
        self.addCleanup(self.connect_patcher.stop)

    def _call(self, request: Request):
        return asyncio.run(billing_routes.yookassa_webhook(request))

    def test_succeeded_applies_payment(self) -> None:
        payment = {
            "id": "yk-1",
            "status": "succeeded",
            "metadata": {"account_id": str(self.account_id)},
            "payment_method": {"id": "pm-abc", "saved": True},
        }
        with mock.patch.object(yookassa_client, "get_payment", return_value=payment):
            response = self._call(_post_request({"object": {"id": "yk-1"}}))
        self.assertEqual(response.status_code, 200)
        with self.connect() as conn:
            account = accounts.get_account_by_id(conn, self.account_id)
            row = conn.execute("SELECT * FROM payments WHERE yookassa_payment_id='yk-1'").fetchone()
        self.assertEqual(account["status"], "active")
        self.assertEqual(account["payment_method_id"], "pm-abc")
        self.assertEqual(row["status"], "succeeded")

    def test_forged_status_in_body_is_ignored(self) -> None:
        """The POST body claims 'succeeded', but the authenticated get_payment()
        call says 'canceled' -- the account must end up past_due, not active.
        This is the core security property the webhook design relies on."""
        payment = {
            "id": "yk-1",
            "status": "canceled",
            "metadata": {"account_id": str(self.account_id)},
            "cancellation_details": {"reason": "card_expired"},
        }
        with mock.patch.object(yookassa_client, "get_payment", return_value=payment):
            response = self._call(_post_request({"object": {"id": "yk-1"}, "object_status_forged": "succeeded"}))
        self.assertEqual(response.status_code, 200)
        with self.connect() as conn:
            account = accounts.get_account_by_id(conn, self.account_id)
        self.assertNotEqual(account["status"], "active")

    def test_canceled_marks_payment_failed_and_account_past_due(self) -> None:
        payment = {
            "id": "yk-1",
            "status": "canceled",
            "metadata": {"account_id": str(self.account_id)},
            "cancellation_details": {"reason": "card_expired"},
        }
        with mock.patch.object(yookassa_client, "get_payment", return_value=payment):
            response = self._call(_post_request({"object": {"id": "yk-1"}}))
        self.assertEqual(response.status_code, 200)
        with self.connect() as conn:
            account = accounts.get_account_by_id(conn, self.account_id)
            row = conn.execute("SELECT * FROM payments WHERE yookassa_payment_id='yk-1'").fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["error_message"], "card_expired")
        self.assertEqual(account["status"], "past_due")

    def test_pending_status_is_a_noop(self) -> None:
        payment = {"id": "yk-1", "status": "pending", "metadata": {"account_id": str(self.account_id)}}
        with mock.patch.object(yookassa_client, "get_payment", return_value=payment):
            response = self._call(_post_request({"object": {"id": "yk-1"}}))
        self.assertEqual(response.status_code, 200)
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM payments WHERE yookassa_payment_id='yk-1'").fetchone()
        self.assertEqual(row["status"], "pending")

    def test_invalid_json_returns_400(self) -> None:
        response = self._call(_raw_body_request(b"not json"))
        self.assertEqual(response.status_code, 400)

    def test_missing_payment_id_returns_400(self) -> None:
        response = self._call(_post_request({"object": {}}))
        self.assertEqual(response.status_code, 400)

    def test_get_payment_error_returns_502_for_retry(self) -> None:
        with mock.patch.object(yookassa_client, "get_payment", side_effect=yookassa_client.YooKassaError("boom")):
            response = self._call(_post_request({"object": {"id": "yk-1"}}))
        self.assertEqual(response.status_code, 502)

    def test_missing_account_id_acks_without_mutating_db(self) -> None:
        payment = {"id": "yk-1", "status": "succeeded", "metadata": {}}
        with mock.patch.object(yookassa_client, "get_payment", return_value=payment):
            response = self._call(_post_request({"object": {"id": "yk-1"}}))
        self.assertEqual(response.status_code, 200)
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM payments WHERE yookassa_payment_id='yk-1'").fetchone()
            account = accounts.get_account_by_id(conn, self.account_id)
        self.assertEqual(row["status"], "pending")
        self.assertNotEqual(account["status"], "active")


if __name__ == "__main__":
    unittest.main()
