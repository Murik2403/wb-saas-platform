"""Single-flight runner for a tenant's WB synchronization job."""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock, Thread


class SyncRunner:
    def __init__(self) -> None:
        self._lock = Lock()

    def start(self, worker: Callable[[], None]) -> bool:
        if not self._lock.acquire(blocking=False):
            return False

        def run() -> None:
            try:
                worker()
            finally:
                self._lock.release()

        Thread(target=run, daemon=True, name="wb-tenant-sync").start()
        return True

    @property
    def is_running(self) -> bool:
        return self._lock.locked()
