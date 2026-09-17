"""Unit test for app.py's _client_ip() -- the gateway sits entirely behind
Traefik (no port mapping lets a request reach it directly, see
docker-compose.yml), so request.client.host is always Traefik's own
container IP. Using it directly for rate-limiting would bucket every real
visitor together under one shared counter instead of limiting attackers
individually. Regression test for exactly that bug.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import Request

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

import app as app_module  # noqa: E402


def _request(headers: list[tuple[bytes, bytes]], client=("172.18.0.5", 12345)) -> Request:
    scope = {"type": "http", "headers": headers, "method": "GET", "path": "/", "client": client}
    return Request(scope)


class ClientIpTests(unittest.TestCase):
    def test_uses_x_forwarded_for_when_present(self) -> None:
        request = _request([(b"x-forwarded-for", b"203.0.113.7")])
        self.assertEqual(app_module._client_ip(request), "203.0.113.7")

    def test_ignores_a_spoofed_leftmost_address(self) -> None:
        # Traefik appends the direct peer to the right. A client-controlled
        # value placed on the left must never select a new rate-limit bucket.
        request = _request([(b"x-forwarded-for", b"203.0.113.7, 10.0.0.1")])
        self.assertEqual(app_module._client_ip(request), "10.0.0.1")

    def test_falls_back_to_request_client_without_the_header(self) -> None:
        request = _request([])
        self.assertEqual(app_module._client_ip(request), "172.18.0.5")

    def test_uses_configured_trusted_proxy_hops_from_the_right(self) -> None:
        request = _request([(b"x-forwarded-for", b"198.51.100.4, 10.0.0.1")])
        with patch.object(app_module.config, "TRUSTED_PROXY_HOPS", 1):
            self.assertEqual(app_module._client_ip(request), "198.51.100.4")

    def test_ignores_malformed_forwarded_for_addresses(self) -> None:
        request = _request([(b"x-forwarded-for", b"not-an-ip,203.0.113.7,bad")], client=("172.18.0.5", 12345))
        self.assertEqual(app_module._client_ip(request), "203.0.113.7")


if __name__ == "__main__":
    unittest.main()
