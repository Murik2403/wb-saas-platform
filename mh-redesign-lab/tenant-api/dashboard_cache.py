"""Small concurrency guard for expensive dashboard response builders."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from threading import Lock
from time import monotonic
from typing import TypeVar


T = TypeVar("T")


class DashboardResponseCache:
    """Cache compact response payloads and serialize expensive cache misses."""

    def __init__(self, ttl_seconds: float = 20.0, max_entries: int = 128, *, release_before_build: bool = False) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_entries = int(max_entries)
        self._release_before_build = release_before_build
        self._values: OrderedDict[str, tuple[float, object]] = OrderedDict()
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
                if self._release_before_build:
                    # Heavy snapshots must not overlap with the next period's
                    # transient DataFrames. Consumer references stay valid.
                    self._values.clear()

            value = builder()
            with self._lock:
                if generation == self._generation:
                    self._prune_expired(monotonic())
                    self._values[key] = (monotonic() + self._ttl_seconds, value)
                    self._values.move_to_end(key)
                    while len(self._values) > self._max_entries:
                        self._values.popitem(last=False)
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
        self._values.move_to_end(key)
        return value

    def _prune_expired(self, now: float) -> None:
        """Remove expired entries on insertion so one-off keys cannot leak memory."""
        for key, (expires_at, _) in tuple(self._values.items()):
            if expires_at <= now:
                self._values.pop(key, None)
