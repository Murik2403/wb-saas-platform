"""Unit tests for logic/csrf.py -- the double-submit-cookie token logic in
isolation, using bare Request/Response objects rather than a full FastAPI
TestClient (none of this codebase's tests hit real HTTP routes -- see the
other test files' docstrings for why).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from fastapi import Request, Response

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

from logic import csrf  # noqa: E402


def _request_with_cookie(value: str | None) -> Request:
    headers = []
    if value is not None:
        headers.append((b"cookie", f"{csrf.CSRF_COOKIE_NAME}={value}".encode()))
    scope = {"type": "http", "headers": headers, "method": "GET", "path": "/"}
    return Request(scope)


class GetOrCreateTokenTests(unittest.TestCase):
    def test_generates_a_token_when_no_cookie_present(self) -> None:
        token = csrf.get_or_create_token(_request_with_cookie(None))
        self.assertTrue(token)

    def test_reuses_the_existing_cookie_value(self) -> None:
        token = csrf.get_or_create_token(_request_with_cookie("existing-token-value"))
        self.assertEqual(token, "existing-token-value")

    def test_two_fresh_requests_get_different_tokens(self) -> None:
        a = csrf.get_or_create_token(_request_with_cookie(None))
        b = csrf.get_or_create_token(_request_with_cookie(None))
        self.assertNotEqual(a, b)


class VerifyTests(unittest.TestCase):
    def test_matching_cookie_and_submitted_token_passes(self) -> None:
        request = _request_with_cookie("same-value")
        self.assertTrue(csrf.verify(request, "same-value"))

    def test_mismatched_token_fails(self) -> None:
        request = _request_with_cookie("cookie-value")
        self.assertFalse(csrf.verify(request, "different-value"))

    def test_missing_cookie_fails_even_if_a_token_was_submitted(self) -> None:
        request = _request_with_cookie(None)
        self.assertFalse(csrf.verify(request, "whatever-the-form-said"))

    def test_missing_submitted_token_fails_even_with_a_valid_cookie(self) -> None:
        request = _request_with_cookie("cookie-value")
        self.assertFalse(csrf.verify(request, ""))


class SetCookieTests(unittest.TestCase):
    def test_sets_the_expected_cookie_name_and_value(self) -> None:
        response = Response()
        csrf.set_cookie(response, "some-token")
        raw_cookies = response.headers.getlist("set-cookie")
        self.assertEqual(len(raw_cookies), 1)
        self.assertIn(f"{csrf.CSRF_COOKIE_NAME}=some-token", raw_cookies[0])
        self.assertIn("HttpOnly", raw_cookies[0])
        self.assertIn("samesite=lax", raw_cookies[0].lower())


if __name__ == "__main__":
    unittest.main()
