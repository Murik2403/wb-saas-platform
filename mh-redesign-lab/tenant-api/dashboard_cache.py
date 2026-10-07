"""Small concurrency guard for expensive dashboard response builders."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from threading import Lock
from time import monotonic
from typing import TypeVar
import sys
from dataclasses import fields,is_dataclass


T = TypeVar("T")


def retained_size(value, limit: int) -> int:
    """Conservative retained size without JSON serialization or DataFrame copies.

    Pandas' deep memory accounting includes strings/index; references shared
    inside a payload and cycles are visited once. Stop traversing oversized
    graphs early so a denied cache entry does not itself allocate a second copy.
    """
    seen=set();frames=[iter((value,))];total=0
    while frames:
        try:item=next(frames[-1])
        except StopIteration:
            frames.pop();continue
        identity=id(item)
        if identity in seen:continue
        seen.add(identity)
        module=type(item).__module__
        if module.startswith('pandas.') and hasattr(item,'memory_usage'):
            try:usage=item.memory_usage(index=True,deep=True)
            except TypeError:usage=item.memory_usage(deep=True)
            total+=max(sys.getsizeof(item),int(usage.sum() if hasattr(usage,'sum') else usage))
            # A small sliced DataFrame can keep a much larger NumPy owner
            # alive. Account those retained owners as well as visible cells.
            manager=getattr(item,'_mgr',None)
            for block in getattr(manager,'blocks',()):
                base=getattr(getattr(block,'values',None),'base',None)
                if base is not None:frames.append(iter((base,)))
        else:
            total+=sys.getsizeof(item)
            if isinstance(item,dict):
                frames.append((part for pair in item.items() for part in pair))
            elif isinstance(item,(list,tuple,set,frozenset)):
                frames.append(iter(item))
            elif module.startswith('numpy') and hasattr(item,'nbytes'):
                base=getattr(item,'base',None)
                if base is not None:frames.append(iter((base,)))
                if getattr(getattr(item,'dtype',None),'hasobject',False):frames.append(iter(item.flat))
            elif is_dataclass(item) and not isinstance(item,type) and not hasattr(item,'__dict__'):
                frames.append(iter(map(lambda field,record=item:getattr(record,field.name),fields(item))))
            elif hasattr(item,'__dict__'):
                frames.append(iter((vars(item),)))
        if total>limit:return total
    return total


class DashboardResponseCache:
    """Cache compact response payloads and serialize expensive cache misses."""

    def __init__(self, ttl_seconds: float = 20.0, max_entries: int = 128, *, release_before_build: bool = False, max_bytes: int = 32*1024*1024) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        if isinstance(max_bytes,bool) or max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._max_bytes = int(max_bytes)
        self._bytes = 0
        self._sizes: dict[str,int] = {}
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
                    self._sizes.clear()
                    self._bytes = 0

            value = builder()
            try:size = retained_size(value,self._max_bytes) + sys.getsizeof(key) + 192
            except Exception:size = self._max_bytes + 1 # accounting failure must never fail a valid response
            with self._lock:
                if generation == self._generation and size <= self._max_bytes:
                    self._prune_expired(monotonic())
                    self._remove(key)
                    self._values[key] = (monotonic() + self._ttl_seconds, value)
                    self._sizes[key] = size
                    self._bytes += size
                    self._values.move_to_end(key)
                    while len(self._values) > self._max_entries or self._bytes > self._max_bytes:
                        self._remove(next(iter(self._values)))
            return value

    def clear(self) -> None:
        with self._lock:
            self._generation += 1
            self._values.clear()
            self._sizes.clear()
            self._bytes = 0

    @property
    def retained_bytes(self) -> int:
        with self._lock:return self._bytes

    def _remove(self,key) -> None:
        self._values.pop(key,None)
        self._bytes -= self._sizes.pop(key,0)

    def _get_fresh(self, key: str) -> object | None:
        cached = self._values.get(key)
        if cached is None:
            return None
        expires_at, value = cached
        if expires_at <= monotonic():
            self._remove(key)
            return None
        self._values.move_to_end(key)
        return value

    def _prune_expired(self, now: float) -> None:
        """Remove expired entries on insertion so one-off keys cannot leak memory."""
        for key, (expires_at, _) in tuple(self._values.items()):
            if expires_at <= now:
                self._remove(key)
