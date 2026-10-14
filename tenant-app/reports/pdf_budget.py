"""Memory budget for building report PDFs.

Rendering a PDF (pandas + matplotlib) adds roughly 200-300 MiB to a tenant for tens of seconds and is CPU-bound. Every
tenant used to render whenever it liked, so the many tenants of one host could all render at the same moment (the
isolated 8 GiB stand fell below its safety floor with a handful of simultaneous reports).

Two layers, both OS file locks, so a process that dies (crash, OOM kill, container stop) releases its place by itself and
nothing has to be cleaned up or expired:

* the tenant slot  - one PDF at a time per tenant, across the API process and the worker (a private lock in the data dir);
* the host slots   - ``WB_SAAS_PDF_SLOTS`` slot files in ``WB_SAAS_PDF_SLOT_DIR``, a directory every tenant container of
  the host mounts; each PDF holds one slot, so at most that many PDFs render on the host at once. Not configured: only
  the tenant slot applies (no behaviour change for a single tenant).

Waiting never builds a PDF anyway. Callers that can wait without holding any state use ``try_pdf_slot`` and leave the work
queued; callers inside a request use a short ``wait`` and turn ``PdfBusy`` into 503 + Retry-After.
"""
from __future__ import annotations

import os
import random
import threading
import time
from contextlib import contextmanager
from pathlib import Path

try:  # config is the tenant's own; tests and tools may import this module without a data directory
    from config import DATA_DIR
except Exception:  # noqa: BLE001
    DATA_DIR = Path(os.environ.get("WB_SAAS_DATA_DIR", "."))

POLL_SECONDS = 0.5
_held = threading.local()


class PdfBusy(RuntimeError):
    """Every PDF slot is in use; nothing was started."""


def _locker():
    if os.name == "nt":
        import msvcrt

        def lock(handle):
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                return False

        def unlock(handle):
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        def lock(handle):
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except (BlockingIOError, OSError):
                return False

        def unlock(handle):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return lock, unlock


_lock, _unlock = _locker()


def _open(path: Path, *, create_parent: bool = True):
    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")  # kept forever: the OS lock on the open file, not the file's existence, is the state
    try:
        if os.name == "nt" and path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
    except BaseException:
        handle.close()
        raise
    return handle


_warned = False


def _warn_once() -> None:
    global _warned
    if not _warned:
        _warned = True
        import logging
        logging.getLogger("marketshelper.pdf_budget").warning("PDF slot directory is unusable: report generation is paused")


def _host_slots() -> tuple[Path | None, int]:
    directory = os.environ.get("WB_SAAS_PDF_SLOT_DIR", "").strip()
    try:
        count = max(1, int(os.environ.get("WB_SAAS_PDF_SLOTS", "1")))
    except ValueError:
        count = 1
    return (Path(directory) if directory else None), count


def tenant_slot_directory() -> Path:
    """A private shop child shares the cabinet lock, never a caller-supplied path."""
    parent = os.environ.get("MH_INTERNAL_PDF_TENANT_DIR", "")
    if not parent:
        return DATA_DIR
    directory = Path(parent)
    child = DATA_DIR.resolve()
    if (not directory.is_absolute() or not child.name.startswith(".shop-workspace-")
            or directory.resolve() != child.parent):
        raise ValueError("Invalid internal cabinet PDF lock directory")
    return directory


def status() -> dict:
    """Observe occupancy without creating the shared directory or lock files."""
    directory, count = _host_slots()
    if directory is None:
        return {"host_slots": 0, "host_busy": None, "configured": False, "healthy": None}
    unhealthy = {"host_slots": count, "host_busy": None, "configured": True, "healthy": False}
    if not directory.is_dir() or not os.access(directory, os.W_OK):
        return unhealthy
    busy = 0
    try:
        for index in range(count):
            try:
                handle = (directory / f"pdf-slot-{index:02d}.lock").open("r+b")
            except FileNotFoundError:
                continue  # no holder; diagnostics must not initialize the slot
            try:
                if _lock(handle):
                    _unlock(handle)
                else:
                    busy += 1
            finally:
                handle.close()
    except OSError:
        return unhealthy
    return {"host_slots": count, "host_busy": busy, "configured": True, "healthy": True}


def _try_once():
    """Take the tenant slot, then one host slot. Returns a release function or None."""
    tenant = _open(tenant_slot_directory() / ".pdf-tenant.lock")
    if not _lock(tenant):
        tenant.close()
        return None
    directory, count = _host_slots()
    host = None
    if directory is not None:
        try:
            if not directory.is_dir():
                raise OSError("Configured shared slot directory is unavailable")
            order = list(range(count))
            random.shuffle(order)  # spread over the slot files so one file is not the single point of contention
            for index in order:
                candidate = _open(directory / f"pdf-slot-{index:02d}.lock", create_parent=False)
                if _lock(candidate):
                    host = candidate
                    break
                candidate.close()
            slots_usable = True
        except OSError:
            # A configured shared budget is mandatory. Missing mounts or permission failures must never permit
            # every tenant to render at once; leave background work queued and let requests return PdfBusy/503.
            slots_usable = False
            _warn_once()
        if not slots_usable or host is None:
            _unlock(tenant)
            tenant.close()
            return None

    def release():
        for handle in (host, tenant):
            if handle is not None:
                try:
                    _unlock(handle)
                finally:
                    handle.close()
    return release


@contextmanager
def _held_slot(release):
    _held.depth = 1
    try:
        yield
    finally:
        _held.depth = 0
        release()


def holds_slot() -> bool:
    return getattr(_held, "depth", 0) > 0


@contextmanager
def pdf_slot(wait: float = 0.0):
    """Hold one PDF slot for the block; wait up to ``wait`` seconds for it, else raise PdfBusy.

    Re-entrant on the same thread: a caller that already holds the slot (the worker around claim + build) may call code
    that asks again.
    """
    if holds_slot():
        yield
        return
    deadline = time.monotonic() + max(0.0, wait)
    while True:
        release = _try_once()
        if release is not None:
            break
        if time.monotonic() >= deadline:
            raise PdfBusy("Сейчас формируется максимум отчётов: повторите через минуту")
        time.sleep(POLL_SECONDS + random.random() * 0.25)
    with _held_slot(release):
        yield


def try_pdf_slot():
    """Non-blocking: a context manager holding the slot, or None when none is free (leave the work queued)."""
    if holds_slot():
        return _noop()
    release = _try_once()
    return _held_slot(release) if release is not None else None


@contextmanager
def _noop():
    yield
