"""Cross-process synchronization guard, released by the OS after a crash."""
from contextlib import contextmanager
from pathlib import Path
import os

from config import DB_PATH


class SyncBusyError(RuntimeError):
    pass


@contextmanager
def sync_lock(path: Path | None = None):
    path = path or DB_PATH.with_suffix(".sync.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the file permanently: unlinking a locked file permits two locks
    # on different inodes on Unix. The OS lock, not file existence, is state.
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise SyncBusyError("Синхронизация уже выполняется") from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SyncBusyError("Синхронизация уже выполняется") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def is_sync_running() -> bool:
    try:
        with sync_lock():
            return False
    except SyncBusyError:
        return True
