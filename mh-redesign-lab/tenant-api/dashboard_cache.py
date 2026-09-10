"""Small concurrency guard for expensive dashboard response builders."""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from time import monotonic
from typing import TypeVar


T = TypeVar("T")


class DashboardResponseCache:
    """Cache compact response payloads and serialize expensive cache misses."""

    def __init__(self, ttl_seconds: float = 20.0) -> None:
        self._ttl_seconds = ttl_seconds
        self._values: dict[str, tuple[float, object]] = {}
        self._lock = Lock()
        self._build_lock = Lock()
        self._generation = 0

    def get_or_build(self, key: str, builder: Callable[[], T]) -> T:
        with self._lock:
            cached = self._get_fresh(key)
            if cached is not None:
                return cached  # type: ignore[return-value]

        # build_dashboard loads several large DataFrames. One process must not
        # run those calculations concurrently when a user switches views.
        with self._build_lock:
            with self._lock:
                cached = self._get_fresh(key)
                if cached is not None:
                    return cached  # type: ignore[return-value]
                generation = self._generation

            value = builder()
            with self._lock:
                if generation == self._generation:
                    self._values[key] = (monotonic() + self._ttl_seconds, value)
            return value

    def clear(self) -> None:
        with self._lock:
            self._generation += 1
            self._values.clear()

    def _get_fresh(self, key: str) -> object | None:
        cached = self._values.get(key)
        if cached is None:
            return None
        expires_at, value = cached
        if expires_at <= monotonic():
            self._values.pop(key, None)
            return None
        return value
