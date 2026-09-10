import subprocess
import sys

import pytest
from sync_lock import sync_lock, SyncBusyError


def test_other_process_cannot_acquire_lock(tmp_path):
    path = tmp_path / "sync.lock"
    code = "from pathlib import Path; import sys; from sync_lock import sync_lock, SyncBusyError\ntry:\n with sync_lock(Path(sys.argv[1])): pass\nexcept SyncBusyError: sys.exit(42)"
    with sync_lock(path):
        result = subprocess.run([sys.executable, "-c", code, str(path)], timeout=10)
        assert result.returncode == 42
    assert subprocess.run([sys.executable, "-c", code, str(path)], timeout=10).returncode == 0


def test_exception_releases_lock(tmp_path):
    path = tmp_path / "sync.lock"
    with pytest.raises(ValueError):
        with sync_lock(path):
            raise ValueError("test")
    with sync_lock(path):
        pass


def test_sync_entry_points_refuse_work_while_locked(monkeypatch, tmp_path):
    import sync
    import sync_lock as module
    monkeypatch.setattr(module, "DB_PATH", tmp_path / "tenant.sqlite3")
    monkeypatch.setattr(sync, "init_db", lambda: pytest.fail("Must not start work"))
    with sync_lock():
        with pytest.raises(SyncBusyError):
            sync.sync_all()
        with pytest.raises(SyncBusyError):
            sync.sync_finances()
