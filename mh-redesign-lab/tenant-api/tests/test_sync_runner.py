from __future__ import annotations

import sys
from pathlib import Path
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sync_runner import SyncRunner


def test_runner_rejects_overlapping_syncs_and_releases_after_completion() -> None:
    runner = SyncRunner()
    started = Event()
    release = Event()
    completed = Event()

    def worker() -> None:
        try:
            started.set()
            release.wait(timeout=1)
        finally:
            completed.set()

    assert runner.start(worker) is True
    assert started.wait(timeout=1)
    assert runner.start(lambda: None) is False
    release.set()
    assert completed.wait(timeout=1)

    assert runner.start(lambda: None) is True
