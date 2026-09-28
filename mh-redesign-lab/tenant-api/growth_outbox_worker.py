"""Periodic, tenant-local retry loop for durable growth delivery events."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from urllib.parse import urlsplit

from growth_delivery import deliver_pending_growth_events

logger = logging.getLogger(__name__)


def delivery_worker_enabled(
    *,
    is_demo: bool,
    internal_api_url: str,
    internal_api_secret: str,
    tenant_slug: str,
) -> bool:
    """Avoid starting a no-op worker for demos or incomplete/unsafe transports."""
    origin = str(internal_api_url or "").strip().rstrip("/")
    secret = str(internal_api_secret or "").strip()
    slug = str(tenant_slug or "").strip()
    if is_demo or not origin or not secret or not slug:
        return False
    try:
        parsed = urlsplit(origin)
        hostname = parsed.hostname
        # Accessing ``port`` validates malformed port strings as well.
        parsed.port
        return (
            bool(hostname)
            and parsed.path in {"", "/"}
            and (
                parsed.scheme == "https"
                or (parsed.scheme == "http" and hostname in {"localhost", "127.0.0.1", "::1"})
            )
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


async def run_growth_outbox_worker(
    stop_event: asyncio.Event,
    db_path: str | Path,
    *,
    deliver=deliver_pending_growth_events,
    interval_seconds: float = 60,
) -> None:
    """Attempt delivery immediately, then retry until lifespan requests stop."""
    interval = max(0.1, float(interval_seconds))
    while not stop_event.is_set():
        try:
            result = await asyncio.to_thread(deliver, db_path, limit=1)
            status = str(result.get("status", "unknown")) if isinstance(result, dict) else "unknown"
            if status in {"disabled", "invalid_configuration", "https_required", "storage_unavailable"}:
                logger.warning("Growth outbox delivery skipped; status=%s", status)
            elif status == "complete":
                attempted = max(0, int(result.get("attempted", 0)))
                delivered = max(0, int(result.get("delivered", 0)))
                if delivered < attempted:
                    logger.warning(
                        "Growth outbox delivery incomplete; attempted=%d delivered=%d",
                        attempted,
                        delivered,
                    )
            else:
                logger.warning("Growth outbox delivery skipped; status=unknown")
        except Exception:
            # Do not include exception text: downstream clients may embed URLs
            # or request metadata in errors. The durable row remains retryable.
            logger.warning("Growth outbox delivery attempt failed; will retry")
        if stop_event.is_set():
            return
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
