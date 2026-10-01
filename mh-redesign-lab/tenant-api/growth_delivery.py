"""Retry the tenant's durable first-value outbox using its scoped credential.

Only lifecycle name/time leave the tenant. Product IDs, report contents and
WB credentials are never included. A disabled transport retains pending rows.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

import requests
import config


def deliver_pending_growth_events(db_path: str | Path, *, limit: int = 10) -> dict:
    origin = str(getattr(config, "INTERNAL_API_URL", "")).rstrip("/")
    secret = str(getattr(config, "INTERNAL_API_SECRET", ""))
    slug = str(getattr(config, "TENANT_SLUG", ""))
    if not origin or not secret or not slug or getattr(config, "IS_DEMO", False):
        return {"status": "disabled", "delivered": 0}
    parsed = urlsplit(origin)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.scheme not in {"https", "http"}:
        return {"status": "invalid_configuration", "delivered": 0}
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        return {"status": "https_required", "delivered": 0}
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(seconds=60)).isoformat()
    candidates = []
    try:
        with sqlite3.connect(str(db_path), timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            # A short write transaction claims retries, preventing concurrent
            # requests from immediately dispatching the same pending row.
            conn.execute("BEGIN IMMEDIATE")
            candidates = [dict(row) for row in conn.execute(
                "SELECT event_key,event_name,occurred_at FROM growth_event_outbox "
                "WHERE delivered_at IS NULL AND (last_attempt_at IS NULL OR last_attempt_at < ?) "
                "AND event_name IN ('wb_connected','first_sync_succeeded','first_useful_report','first_production_result','useful_use') "
                "ORDER BY occurred_at LIMIT ?",
                (cutoff, min(max(int(limit), 1), 10)),
            ).fetchall()]
            for event in candidates:
                conn.execute("UPDATE growth_event_outbox SET attempt_count=attempt_count+1,last_attempt_at=? WHERE event_key=?", (now.isoformat(), event["event_key"]))
    except sqlite3.OperationalError:
        return {"status": "storage_unavailable", "delivered": 0}
    delivered = 0
    for event in candidates:
        try:
            response = requests.post(
                origin + "/internal/growth/event",
                headers={"X-Internal-Secret": secret},
                json={"slug": slug, **event}, timeout=(2, 3), allow_redirects=False,
            )
            if response.status_code != 204:
                continue
            with sqlite3.connect(str(db_path), timeout=5) as conn:
                conn.execute("UPDATE growth_event_outbox SET delivered_at=? WHERE event_key=? AND delivered_at IS NULL", (datetime.now(timezone.utc).isoformat(), event["event_key"]))
            delivered += 1
        except (requests.RequestException, sqlite3.OperationalError):
            # Retain the original occurrence time and retry next visit. Never
            # log credential-bearing URLs, request headers or response bodies.
            continue
    return {"status": "complete", "attempted": len(candidates), "delivered": delivered}
