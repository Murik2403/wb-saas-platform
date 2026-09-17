"""Sliding-window rate limiter with optional DB-backed storage.

By default this is in-memory (zero dependencies, low latency). For
production resilience, it can store counters in the control-plane database:
that keeps throttle state across gateway restarts and makes multi-instance
deployments possible when `RateLimiter` is shared with `control_db.connect()`.
"""
from __future__ import annotations

from collections.abc import Callable
import time
from collections import defaultdict, deque
from threading import Lock


class RateLimiter:
    def __init__(
        self,
        requests_per_window: int = 10,
        window_seconds: int = 60,
        *,
        storage_backend: str = "memory",
        db_connect: Callable[[], object] | None = None,
    ):
        self.requests_per_window = requests_per_window
        self.window_seconds = window_seconds
        self.storage_backend = storage_backend
        self._db_connect = db_connect
        self._history: dict[str, deque[float]] = defaultdict(deque[float])
        self._lock = Lock()
        self._next_cleanup = 0.0
        if self.storage_backend == "db" and self._db_connect is None:
            raise ValueError("rate limiter db backend requires a db_connect callable")
        if self.storage_backend not in {"memory", "db"}:
            raise ValueError(f"Unsupported rate limiter storage_backend: {storage_backend!r}")

    def is_allowed(self, key: str) -> bool:
        if self.storage_backend == "db":
            return self._is_allowed_db(key)
        return self._is_allowed_memory(key)

    def _is_allowed_memory(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            if now >= self._next_cleanup:
                self._prune_expired_memory(cutoff)
                # Full cleanup is O(number of distinct keys), so run it at
                # most once per window rather than on every login attempt.
                self._next_cleanup = now + self.window_seconds
            bucket = self._history[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.requests_per_window:
                return False
            bucket.append(now)
            return True

    def _is_allowed_db(self, key: str) -> bool:
        now = time.time()
        bucket_start = int(now)
        cutoff = now - self.window_seconds
        cutoff_bucket = int(cutoff)
        # Even without strict guarantees, this block remains safe under
        # concurrent callers thanks to database write-lock semantics for this
        # tiny table (and the gateway's single-process / low concurrency model).
        with self._lock:
            conn_ctx = self._db_connect()  # type: ignore[misc]
            conn = conn_ctx.__enter__() if hasattr(conn_ctx, "__enter__") else conn_ctx  # type: ignore[operator]
            try:
                self._prepare_rate_limit_table(conn)
                conn.execute("DELETE FROM rate_limit_buckets WHERE bucket_start < ?", (cutoff_bucket,))
                row = conn.execute(
                    """
                    SELECT COALESCE(SUM(request_count), 0) AS request_count
                    FROM rate_limit_buckets
                    WHERE bucket_key = ? AND bucket_start >= ?
                    """,
                    (key, cutoff_bucket),
                ).fetchone()
                used = self._coerce_count(row)
                if used >= self.requests_per_window:
                    return False
                conn.execute(
                    """
                    INSERT INTO rate_limit_buckets(bucket_key, bucket_start, request_count)
                    VALUES (?, ?, 1)
                    ON CONFLICT(bucket_key, bucket_start) DO UPDATE
                        SET request_count = rate_limit_buckets.request_count + 1
                    """,
                    (key, bucket_start),
                )
                conn.commit()
            finally:
                if hasattr(conn_ctx, "__exit__"):
                    conn_ctx.__exit__(None, None, None)
                if hasattr(conn, "close"):
                    conn.close()
        return True

    def _coerce_count(self, row) -> int:
        if row is None:
            return 0
        if "request_count" in row:
            return int(row["request_count"] or 0)
        return int(row[0] or 0)

    def _prepare_rate_limit_table(self, conn) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rate_limit_buckets (
                bucket_key TEXT NOT NULL,
                bucket_start INTEGER NOT NULL,
                request_count INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(bucket_key, bucket_start)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rate_limit_buckets_bucket_key ON rate_limit_buckets(bucket_key)")

    def _prune_expired_memory(self, cutoff: float) -> None:
        for key, timestamps in list(self._history.items()):
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            if not timestamps:
                del self._history[key]

    def clear(self) -> None:
        with self._lock:
            if self.storage_backend == "memory":
                self._history.clear()
                self._next_cleanup = 0.0
                return

            conn_ctx = self._db_connect()  # type: ignore[misc]
            conn = conn_ctx.__enter__() if hasattr(conn_ctx, "__enter__") else conn_ctx  # type: ignore[operator]
            try:
                self._prepare_rate_limit_table(conn)
                conn.execute("DELETE FROM rate_limit_buckets")
                conn.commit()
            finally:
                if hasattr(conn_ctx, "__exit__"):
                    conn_ctx.__exit__(None, None, None)
                if hasattr(conn, "close"):
                    conn.close()
            self._next_cleanup = 0.0
