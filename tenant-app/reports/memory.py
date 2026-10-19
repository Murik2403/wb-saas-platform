"""Allocator policy for the isolated PDF process, never the API process."""
from __future__ import annotations
import ctypes
import gc
import sys
from functools import lru_cache


def configure_report_allocator() -> str:
    # A batch worker should return temporary Arrow buffers to the C heap,
    # rather than keeping the default pool's high-water cache between phases.
    # Do not call Arrow release_unused(), which is intended for diagnostics.
    try:
        import pyarrow as pa
    except ImportError:
        return "unavailable"
    pa.set_memory_pool(pa.system_memory_pool())
    return pa.default_memory_pool().backend_name


@lru_cache(maxsize=1)
def _system_trim():
    if not sys.platform.startswith("linux"):
        return None
    try:
        function = ctypes.CDLL(None).malloc_trim
    except (OSError, AttributeError):
        return None
    function.argtypes = [ctypes.c_size_t]
    function.restype = ctypes.c_int
    return function


def release_report_memory() -> None:
    """Release unreachable objects and free libc pages at report phase boundaries."""
    gc.collect()
    trim = _system_trim()
    if trim is not None:
        trim(0)
