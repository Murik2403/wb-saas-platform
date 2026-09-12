"""Regression checks for public health endpoints used by uptime monitors."""
from __future__ import annotations

import sys
from pathlib import Path
from fastapi.testclient import TestClient

import unittest

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

import app as gateway_app  # noqa: E402


class GatewayHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(gateway_app.app)

    def test_healthz_endpoint_returns_ok(self) -> None:
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_stcore_health_endpoint_returns_ok(self) -> None:
        response = self.client.get("/_stcore/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
