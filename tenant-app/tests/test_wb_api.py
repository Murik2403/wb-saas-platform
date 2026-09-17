"""Unit tests for wb_api.py -- the WB API HTTP client. No existing test
mocked HTTP calls in this codebase; following the same stdlib-only ethos as
the rest of the suite (see tests/base.py's docstring), these patch
requests.Session.request directly rather than pulling in a mocking library.
"""
from __future__ import annotations

import sys
import unittest
import base64
import json
from pathlib import Path
from unittest import mock

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from wb_api import WBAPI, WBAPIError, flatten_ad_stats  # noqa: E402


def _fake_response(status_code: int = 200, json_body=None, text: str = "", headers=None, raise_on_json=False):
    resp = mock.Mock()
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.text = text
    resp.content = b"{}"  # non-empty by default; tests needing an empty body override it explicitly
    if raise_on_json:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = json_body
    return resp


class HeadersTests(unittest.TestCase):
    def test_non_bearer_uses_raw_token(self) -> None:
        api = WBAPI("my-token")
        self.assertEqual(api._headers(bearer=False)["Authorization"], "my-token")

    def test_bearer_prefixes_token(self) -> None:
        api = WBAPI("my-token")
        self.assertEqual(api._headers(bearer=True)["Authorization"], "Bearer my-token")


class TokenScopeTests(unittest.TestCase):
    @staticmethod
    def _token(payload: dict) -> str:
        def encode(value: bytes) -> str:
            return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

        header = encode(json.dumps({"alg": "none"}).encode())
        body = encode(json.dumps(payload).encode())
        return f"{header}.{body}.signature"

    def test_decodes_scope_mask_without_network_request(self) -> None:
        token = self._token({"s": (1 << 5) | (1 << 10) | (1 << WBAPI.READ_ONLY_BIT), "exp": 4102444800})
        result = WBAPI(token).token_scope_info()
        self.assertTrue(result["known"])
        self.assertEqual(result["scopes"], ["Статистика", "Поставки"])
        self.assertIn("Контент", result["missing_scopes"])
        self.assertTrue(result["read_only"])
        self.assertEqual(result["expires_at"], 4102444800)

    def test_opaque_token_returns_unknown_scope_metadata(self) -> None:
        result = WBAPI("opaque-token").token_scope_info()
        self.assertFalse(result["known"])
        self.assertEqual(result["scopes"], [])

class RequestHeaderTests(unittest.TestCase):
    def test_bearer_does_not_double_prefix(self) -> None:
        api = WBAPI("Bearer my-token")
        self.assertEqual(api._headers(bearer=True)["Authorization"], "Bearer my-token")


class RequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = WBAPI("token")
        self.sleep_patcher = mock.patch("wb_api.time.sleep")
        self.sleep_patcher.start()
        self.addCleanup(self.sleep_patcher.stop)

    def test_success_returns_json(self) -> None:
        with mock.patch.object(self.api.session, "request", return_value=_fake_response(200, json_body=[1, 2])):
            result = self.api._request("GET", "https://example.test")
        self.assertEqual(result, [1, 2])

    def test_empty_body_returns_none(self) -> None:
        resp = _fake_response(200)
        resp.content = b""
        with mock.patch.object(self.api.session, "request", return_value=resp):
            result = self.api._request("GET", "https://example.test")
        self.assertIsNone(result)

    def test_401_retries_with_bearer_prefix(self) -> None:
        unauthorized = _fake_response(401)
        ok = _fake_response(200, json_body={"ok": True})
        with mock.patch.object(self.api.session, "request", side_effect=[unauthorized, ok]) as req:
            result = self.api._request("GET", "https://example.test")
        self.assertEqual(result, {"ok": True})
        # First call without Bearer, second with -- proves the fallback path fired.
        self.assertNotIn("Bearer", req.call_args_list[0].kwargs["headers"]["Authorization"])
        self.assertIn("Bearer", req.call_args_list[1].kwargs["headers"]["Authorization"])

    def test_429_sleeps_and_retries(self) -> None:
        throttled = _fake_response(429, headers={"Retry-After": "5"})
        ok = _fake_response(200, json_body=[])
        with mock.patch.object(self.api.session, "request", side_effect=[throttled, throttled, ok]):
            result = self.api._request("GET", "https://example.test")
        self.assertEqual(result, [])

    def test_4xx_raises_wbapierror(self) -> None:
        with mock.patch.object(self.api.session, "request", return_value=_fake_response(404, text="Not Found")):
            with self.assertRaises(WBAPIError):
                self.api._request("GET", "https://example.test")

    def test_non_json_body_raises_wbapierror(self) -> None:
        with mock.patch.object(self.api.session, "request", return_value=_fake_response(200, raise_on_json=True)):
            with self.assertRaises(WBAPIError):
                self.api._request("GET", "https://example.test")

    def test_connection_error_eventually_raises_wbapierror(self) -> None:
        with mock.patch.object(self.api.session, "request", side_effect=requests.ConnectionError("boom")):
            with self.assertRaises(WBAPIError):
                self.api._request("GET", "https://example.test")

    def test_5xx_is_retried_then_succeeds(self) -> None:
        # A transient 502/503 must not fail the whole call on the first blip --
        # it should back off and retry, unlike a 4xx which raises immediately.
        server_error = _fake_response(503, text="Service Unavailable")
        ok = _fake_response(200, json_body={"ok": True})
        with mock.patch.object(self.api.session, "request", side_effect=[server_error, ok]):
            result = self.api._request("GET", "https://example.test")
        self.assertEqual(result, {"ok": True})

    def test_persistent_5xx_eventually_raises(self) -> None:
        server_error = _fake_response(500, text="Internal Error")
        with mock.patch.object(self.api.session, "request", return_value=server_error):
            with self.assertRaises(WBAPIError):
                self.api._request("GET", "https://example.test")


class EndpointMethodTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = WBAPI("token")

    def test_get_orders_returns_list(self) -> None:
        with mock.patch.object(WBAPI, "_request", return_value=[{"nmId": 1}]):
            result = self.api.get_orders("2026-01-01")
        self.assertEqual(result, [{"nmId": 1}])

    def test_get_orders_coerces_non_list_to_empty(self) -> None:
        with mock.patch.object(WBAPI, "_request", return_value=None):
            result = self.api.get_orders("2026-01-01")
        self.assertEqual(result, [])

    def test_get_fbs_supplies_follows_next_cursor(self) -> None:
        with mock.patch.object(WBAPI, "_request", side_effect=[
            {"supplies": [{"id": "WB-1"}], "next": 123},
            {"supplies": [{"id": "WB-2"}], "next": 0},
        ]) as request:
            self.assertEqual(self.api.get_fbs_supplies(), [{"id": "WB-1"}, {"id": "WB-2"}])
        self.assertEqual(request.call_args_list[0].kwargs["params"], {"limit": 100, "next": 0})
        self.assertEqual(request.call_args_list[1].kwargs["params"], {"limit": 100, "next": 123})

    def test_get_sales_returns_list(self) -> None:
        with mock.patch.object(WBAPI, "_request", return_value=[{"nmId": 2}]):
            result = self.api.get_sales("2026-01-01")
        self.assertEqual(result, [{"nmId": 2}])

    def test_get_campaign_ids_filters_by_allowed_status(self) -> None:
        payload = {
            "adverts": [
                {"status": 7, "advert_id": 1},
                {"status": 4, "advert_id": 2},  # not an allowed status -- excluded
                {"status": 11, "advertId": 3},
            ]
        }
        with mock.patch.object(WBAPI, "_request", return_value=payload):
            result = self.api.get_campaign_ids()
        self.assertEqual(result, [1, 3])

    def test_customer_quality_methods_are_available_and_normalize_payloads(self) -> None:
        with mock.patch.object(WBAPI, "_request", side_effect=[
            {"data": {"valuation": 4.9}},
            {"data": {"items": [{"id": "q1"}]}},
            {"data": {"goods": [{"nmId": 7}]}},
            {"report": [{"nmId": 8}]},
        ]) as request:
            self.assertEqual(self.api.get_seller_rating(), {"valuation": 4.9})
            self.assertEqual(self.api.get_new_feedbacks_questions(), {"data": {"items": [{"id": "q1"}]}})
            self.assertEqual(self.api.get_quarantine_goods(), [{"nmId": 7}])
            self.assertEqual(self.api.get_goods_returns("2026-09-01", "2026-09-17"), [{"nmId": 8}])
        self.assertEqual(request.call_count, 4)

    def test_send_seller_message_uses_multipart_and_disables_retries(self) -> None:
        with mock.patch.object(WBAPI, "_request", return_value={"result": {"chatID": "chat-1"}}) as request:
            result = self.api.send_seller_message("1:chat-signature", "Добрый день")
        self.assertEqual(result["result"]["chatID"], "chat-1")
        self.assertEqual(request.call_args.args[:2], ("POST", "https://buyer-chat-api.wildberries.ru/api/v1/seller/message"))
        fields = request.call_args.kwargs["files"]
        self.assertEqual([(name, value[0], value[1]) for name, value in fields], [("replySign", None, "1:chat-signature"), ("message", None, "Добрый день")])
        self.assertFalse(request.call_args.kwargs["retry_safe"])

    def test_send_seller_message_validates_lengths(self) -> None:
        with self.assertRaises(ValueError):
            self.api.send_seller_message("", "текст")
        with self.assertRaises(ValueError):
            self.api.send_seller_message("подпись", "x" * 1001)

    def test_quarantine_retries_with_explicit_limit_when_wb_requires_it(self) -> None:
        with mock.patch.object(
            WBAPI,
            "_request",
            side_effect=[WBAPIError("WB API 400: limit required query parameter"), {"data": {"goods": [{"nmId": 9}]}}],
        ) as request:
            self.assertEqual(self.api.get_quarantine_goods(), [{"nmId": 9}])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[1].kwargs["params"], {"limit": 1000})


class FlattenAdStatsTests(unittest.TestCase):
    def test_produces_campaign_day_total_and_product_rows(self) -> None:
        campaigns = [
            {
                "advertId": 100,
                "days": [
                    {
                        "date": "2026-01-05",
                        "views": 1000,
                        "clicks": 50,
                        "sum": 500.0,
                        "apps": [
                            {"nms": [{"nmId": 111, "name": "Товар А", "views": 600, "sum": 300.0}]},
                        ],
                    }
                ],
            }
        ]
        rows = flatten_ad_stats(campaigns)
        totals = [r for r in rows if r["nm_id"] == 0]
        products = [r for r in rows if r["nm_id"] == 111]
        self.assertEqual(len(totals), 1)
        self.assertEqual(totals[0]["spend"], 500.0)
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["product_name"], "Товар А")
        self.assertEqual(products[0]["spend"], 300.0)

    def test_skips_days_without_a_date(self) -> None:
        campaigns = [{"advertId": 1, "days": [{"views": 10}]}]
        self.assertEqual(flatten_ad_stats(campaigns), [])


if __name__ == "__main__":
    unittest.main()
