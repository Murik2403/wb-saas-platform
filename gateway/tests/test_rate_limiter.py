"""Unit tests for rate_limiter.py -- the sliding-window in-memory limiter
protecting /login, /register, /forgot-password from brute force (see
app.py's auth_rate_limiter and _client_ip()).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

from rate_limiter import RateLimiter  # noqa: E402


class RateLimiterTests(unittest.TestCase):
    def test_allows_requests_up_to_the_limit(self) -> None:
        limiter = RateLimiter(requests_per_window=3, window_seconds=60)
        key = "1.2.3.4"
        self.assertTrue(limiter.is_allowed(key))
        self.assertTrue(limiter.is_allowed(key))
        self.assertTrue(limiter.is_allowed(key))

    def test_blocks_once_the_limit_is_exceeded(self) -> None:
        limiter = RateLimiter(requests_per_window=3, window_seconds=60)
        key = "1.2.3.4"
        for _ in range(3):
            limiter.is_allowed(key)
        self.assertFalse(limiter.is_allowed(key))

    def test_different_keys_have_independent_limits(self) -> None:
        limiter = RateLimiter(requests_per_window=1, window_seconds=60)
        self.assertTrue(limiter.is_allowed("attacker-ip"))
        # A different visitor must not be punished for someone else's
        # attempts -- this is the exact bug that using the wrong IP source
        # (Traefik's own address instead of the real client) would cause.
        self.assertTrue(limiter.is_allowed("innocent-ip"))

    def test_old_attempts_outside_the_window_are_forgotten(self) -> None:
        limiter = RateLimiter(requests_per_window=1, window_seconds=60)
        key = "1.2.3.4"
        with mock.patch("rate_limiter.time.monotonic", return_value=1000.0):
            self.assertTrue(limiter.is_allowed(key))
            self.assertFalse(limiter.is_allowed(key))
        with mock.patch("rate_limiter.time.monotonic", return_value=1061.0):
            self.assertTrue(limiter.is_allowed(key))

    def test_clear_resets_all_state(self) -> None:
        limiter = RateLimiter(requests_per_window=1, window_seconds=60)
        key = "1.2.3.4"
        limiter.is_allowed(key)
        self.assertFalse(limiter.is_allowed(key))
        limiter.clear()
        self.assertTrue(limiter.is_allowed(key))


if __name__ == "__main__":
    unittest.main()
