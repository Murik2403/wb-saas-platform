"""Shared memory budget for starting tenant containers.

Every tenant container has a hard ``mem_limit`` (a ceiling) and a soft
``mem_reservation`` (what it normally needs). Summing ceilings would cap a
16 GiB host at a handful of cabinets, so admission sums the *reservations* of
the tenants that are already running plus the reservations of starts that are
in flight, and compares them with one budget. A start that does not fit is
refused (or waits a bounded time) with a machine-readable reason; running
cabinets are never touched.

The check and the claim happen under one lock, so concurrent requests cannot
both pass on the same free memory. The claim is released when the start
finishes either way: on success Docker itself shows the new container, on
failure nothing remains to count.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Callable

logger = logging.getLogger("wb_saas_provisioner.budget")

TENANT_LABEL = "wb-saas.slug"
_COUNTED_STATES = {"running", "restarting", "paused"}
_UNITS = {"": 1, "b": 1, "k": 1024, "kb": 1024, "kib": 1024, "m": 1024**2, "mb": 1024**2, "mib": 1024**2,
          "g": 1024**3, "gb": 1024**3, "gib": 1024**3}
MIB = 1024 * 1024


def parse_size(text: str | int | float) -> int:
    """'384m' / '1.5g' / '1610612736' -> bytes. Raises ValueError on garbage."""
    if isinstance(text, (int, float)):
        return int(text)
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]*)\s*", str(text))
    if not match or match.group(2).lower() not in _UNITS:
        raise ValueError(f"Invalid size: {text!r}")
    return int(float(match.group(1)) * _UNITS[match.group(2).lower()])


class CapacityExhausted(Exception):
    """No room for one more tenant. ``info`` is safe to show to the caller."""

    def __init__(self, info: dict[str, Any]):
        super().__init__(info.get("message", "capacity exhausted"))
        self.info = info


def host_available_bytes() -> int | None:
    """MemAvailable of the host (a container sees the host's /proc/meminfo); None when unreadable."""
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class TenantBudget:
    def __init__(self) -> None:
        self._cond = threading.Condition(threading.Lock())
        self._pending: dict[str, list[int]] = {}  # slug -> [bytes, holders]

    # -- measurement ------------------------------------------------------------------------------
    def _running_reservations(self, client: Any, default: int, skip: set[str]) -> tuple[int, int]:
        total = count = 0
        for container in client.containers.list(all=True, filters={"label": TENANT_LABEL}):
            container.reload()
            attrs = container.attrs
            if str(attrs.get("State", {}).get("Status")) not in _COUNTED_STATES:
                continue
            slug = (attrs.get("Config", {}).get("Labels") or {}).get(TENANT_LABEL, "")
            if slug in skip:
                continue
            reserved = (attrs.get("HostConfig") or {}).get("MemoryReservation")
            total += int(reserved) if _is_number(reserved) and reserved > 0 else default
            count += 1
        return total, count

    def _budget(self, client: Any, configured: str, fraction: float) -> int | None:
        """Bytes for all tenants together; None = unlimited or unknown."""
        if configured.strip().lower() in {"0", "off", "none"}:
            return None
        if configured.strip():
            return parse_size(configured)
        total = client.info().get("MemTotal")
        return int(total * fraction) if _is_number(total) and total > 0 else None

    def snapshot(self, client: Any, slug: str, *, need: int, configured: str, fraction: float,
                 min_available: int, host_available: Callable[[], int | None] = host_available_bytes) -> dict[str, Any]:
        """Current picture for ``slug``; called with the lock held (or for read-only status)."""
        pending_others = {s: v[0] for s, v in self._pending.items() if s != slug}
        running, count = self._running_reservations(client, need, skip={slug, *pending_others})
        budget = self._budget(client, configured, fraction)
        available = host_available()
        pending_total = sum(pending_others.values())
        reserved = running + pending_total
        info: dict[str, Any] = {
            "budget_mib": None if budget is None else budget // MIB,
            "reserved_mib": reserved // MIB,
            "running_tenants": count + len(pending_others),
            "per_tenant_mib": need // MIB,
            "host_available_mib": None if available is None else available // MIB,
            "min_available_mib": min_available // MIB,
        }
        info["free_mib"] = None if budget is None else max(0, (budget - reserved) // MIB)
        if budget is not None and reserved + need > budget:
            info.update(code="capacity_exhausted", reason="budget",
                        message="Сейчас нет свободных ресурсов под новый кабинет: общий лимит памяти для кабинетов занят.")
        elif available is not None and min_available and available - pending_total - need < min_available:
            info.update(code="capacity_exhausted", reason="host_memory",
                        message="Сейчас нет свободных ресурсов под новый кабинет: на сервере мало свободной памяти.")
        return info

    # -- admission --------------------------------------------------------------------------------
    def acquire(self, client: Any, slug: str, *, need: int, configured: str, fraction: float, min_available: int,
                wait_seconds: float = 0.0, force: bool = False,
                host_available: Callable[[], int | None] = host_available_bytes) -> None:
        """Claim ``need`` bytes for ``slug`` or raise CapacityExhausted.

        ``force`` claims without checking (replacing a tenant that already runs: no extra memory).
        A second holder of the same slug (an upgrade calling create) shares the first claim.
        """
        deadline = time.monotonic() + max(0.0, wait_seconds)
        with self._cond:
            while True:
                held = self._pending.get(slug)
                if held is not None:
                    held[1] += 1
                    return
                if force:
                    self._pending[slug] = [need, 1]
                    return
                try:
                    info = self.snapshot(client, slug, need=need, configured=configured, fraction=fraction,
                                         min_available=min_available, host_available=host_available)
                except Exception:  # noqa: BLE001 -- unreadable Docker state must not invent a refusal
                    logger.warning("Tenant budget unreadable; admitting without a check", exc_info=True)
                    info = {}
                if info.get("code") != "capacity_exhausted":
                    self._pending[slug] = [need, 1]
                    return
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CapacityExhausted(info)
                self._cond.wait(timeout=min(1.0, remaining))  # woken by release(); re-read Docker at least every second

    def release(self, slug: str) -> None:
        with self._cond:
            held = self._pending.get(slug)
            if held is None:
                return
            held[1] -= 1
            if held[1] <= 0:
                del self._pending[slug]
            self._cond.notify_all()

    def notify(self) -> None:
        """A tenant stopped or was removed: let waiting starts re-check."""
        with self._cond:
            self._cond.notify_all()

    def pending(self) -> dict[str, int]:
        with self._cond:
            return {slug: held[0] for slug, held in self._pending.items()}
