from __future__ import annotations

import hashlib
import unittest
from unittest import mock

import password_breach


class _Response:
    def __init__(self, body: str):
        self.body = body.encode("ascii")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self.body


class PwnedPasswordRangeTests(unittest.TestCase):
    def test_returns_matching_count_without_sending_full_hash(self):
        password = "not-the-password-to-send"
        digest = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["padding"] = request.get_header("Add-padding")
            captured["agent"] = request.get_header("User-agent")
            captured["timeout"] = timeout
            return _Response(f"{digest[5:]}:42\r\n00000000000000000000000000000000000:0\r\n")

        self.assertEqual(password_breach.pwned_password_count(password, opener=opener), 42)
        self.assertTrue(captured["url"].endswith(digest[:5]))
        self.assertNotIn(digest[5:], captured["url"])
        self.assertNotIn(password, captured["url"])
        self.assertEqual(captured["padding"], "true")
        self.assertTrue(captured["agent"])
        self.assertGreater(captured["timeout"], 0)

    def test_returns_zero_when_suffix_is_absent(self):
        with mock.patch("urllib.request.urlopen", return_value=_Response("ABC:1\r\n")):
            self.assertEqual(password_breach.pwned_password_count("unique enough"), 0)

    def test_transport_error_hides_password(self):
        password = "do-not-include-me"
        with self.assertRaises(password_breach.PasswordBreachCheckError) as raised:
            password_breach.pwned_password_count(password, opener=mock.Mock(side_effect=OSError("offline")))
        self.assertNotIn(password, str(raised.exception))
