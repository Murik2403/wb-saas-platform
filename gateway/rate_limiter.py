"""Simple, thread-safe in-memory sliding-window rate limiter.

Protects sensitive endpoints (/login, /register, /forgot-password) from
brute-force attacks without requiring Redis or extra third-party dependencies.
"""
from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock


class RateLimiter:
    def __init__(self, requests_per_window: int = 10, window_seconds: int = 60):
        self.requests_per_window = requests_per_window
        self.window_seconds = window_seconds
        self._history: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()
        self._next_cleanup = 0.0

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            if now >= self._next_cleanup:
                self._prune_expired(cutoff)
                # Full cleanup is O(number of distinct keys), so run it at
                # most once per window rather than on every login attempt.
                self._next_cleanup = now + self.window_seconds
            timestamps = [t for t in self._history[key] if t > cutoff]
            if len(timestamps) >= self.requests_per_window:
                self._history[key] = timestamps
                return False
            timestamps.append(now)
            self._history[key] = timestamps
            return True

    def _prune_expired(self, cutoff: float) -> None:
        for key, timestamps in list(self._history.items()):
            active = [timestamp for timestamp in timestamps if timestamp > cutoff]
            if active:
                self._history[key] = active
            else:
                del self._history[key]

    def clear(self) -> None:
        with self._lock:
            self._history.clear()
            self._next_cleanup = 0.0
