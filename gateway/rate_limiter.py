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

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            timestamps = [t for t in self._history[key] if t > cutoff]
            if len(timestamps) >= self.requests_per_window:
                self._history[key] = timestamps
                return False
            timestamps.append(now)
            self._history[key] = timestamps
            return True

    def clear(self) -> None:
        with self._lock:
            self._history.clear()
