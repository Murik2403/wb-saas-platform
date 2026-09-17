"""Unit tests for rate_limiter.py -- the sliding-window in-memory limiter
protecting /login, /register, /forgot-password from brute force (see
app.py's auth_rate_limiter and _client_ip()).
"""
from __future__ import annotations

import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
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

    def test_expired_keys_are_pruned_without_revisiting_them(self) -> None:
        limiter = RateLimiter(requests_per_window=1, window_seconds=60)
        with mock.patch("rate_limiter.time.monotonic", return_value=1000.0):
            limiter.is_allowed("old-a")
            limiter.is_allowed("old-b")
        with mock.patch("rate_limiter.time.monotonic", return_value=1061.0):
            limiter.is_allowed("current")
        self.assertEqual(set(limiter._history), {"current"})

    def test_clear_resets_all_state(self) -> None:
        limiter = RateLimiter(requests_per_window=1, window_seconds=60)
        key = "1.2.3.4"
        limiter.is_allowed(key)
        self.assertFalse(limiter.is_allowed(key))
        limiter.clear()
        self.assertTrue(limiter.is_allowed(key))


class RateLimiterPersistentTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        tmp.close()
        self.db_path = tmp.name
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rate_limit_buckets (
                bucket_key TEXT NOT NULL,
                bucket_start INTEGER NOT NULL,
                request_count INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(bucket_key, bucket_start)
            )
            """
        )
        self.conn.close()

    def tearDown(self) -> None:
        try:
            Path(self.db_path).unlink()
        except FileNotFoundError:
            pass

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def test_db_mode_keeps_counters_between_instances(self) -> None:
        limiter_a = RateLimiter( requests_per_window=1, window_seconds=60, storage_backend="db", db_connect=self._connect )
        limiter_b = RateLimiter( requests_per_window=1, window_seconds=60, storage_backend="db", db_connect=self._connect )
        self.assertTrue(limiter_a.is_allowed("same-key"))
        self.assertFalse(limiter_b.is_allowed("same-key"))

    def test_db_mode_forgets_expired_buckets(self) -> None:
        limiter = RateLimiter(requests_per_window=1, window_seconds=60, storage_backend="db", db_connect=self._connect)
        with patch("rate_limiter.time.time", return_value=1000.0):
            self.assertTrue(limiter.is_allowed("ip"))
            self.assertFalse(limiter.is_allowed("ip"))
        with patch("rate_limiter.time.time", return_value=1070.0):
            self.assertTrue(limiter.is_allowed("ip"))

    def test_db_clear_resets_persistent_state(self) -> None:
        limiter = RateLimiter(requests_per_window=1, window_seconds=60, storage_backend="db", db_connect=self._connect)
        limiter.is_allowed("ip")
        self.assertFalse(limiter.is_allowed("ip"))
        limiter.clear()
        self.assertTrue(limiter.is_allowed("ip"))


if __name__ == "__main__":
    unittest.main()
