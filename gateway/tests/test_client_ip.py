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

    def test_takes_the_first_address_in_a_forwarded_chain(self) -> None:
        # Traefik appends to X-Forwarded-For rather than replacing it, so a
        # chain (client, any upstream proxies) is possible; the original
        # client is always first.
        request = _request([(b"x-forwarded-for", b"203.0.113.7, 10.0.0.1")])
        self.assertEqual(app_module._client_ip(request), "203.0.113.7")

    def test_falls_back_to_request_client_without_the_header(self) -> None:
        request = _request([])
        self.assertEqual(app_module._client_ip(request), "172.18.0.5")


if __name__ == "__main__":
    unittest.main()
