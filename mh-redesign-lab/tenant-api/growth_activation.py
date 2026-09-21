"""Local readiness evidence. A read never creates or backdates an activation."""
from __future__ import annotations

from datetime import date, timedelta
import math
from pathlib import Path
import re
import sqlite3


GROWTH_EVENT_NAMES = frozenset({
    "wb_connected",
    "first_sync_succeeded",
    "first_useful_report",
    "first_production_result",
})


def initialize_growth_schema(db_path: str | Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS growth_first_value (
                event_key TEXT PRIMARY KEY CHECK(event_key='first_useful_report'),
                nm_id INTEGER NOT NULL,
                occurred_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS growth_event_outbox (
                event_key TEXT PRIMARY KEY,
                event_name TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                delivered_at TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_attempt_at TEXT
            );
        """)


def record_growth_event(db_path: str | Path, *, event_name: str, occurred_at: str) -> bool:
    """Queue one server-verified lifecycle event exactly once.

    The event key is the lifecycle name because these milestones are first-time
    facts for a tenant. The unique outbox key makes retries from token saving,
    sync workers, or process restarts harmless.
    """
    if event_name not in GROWTH_EVENT_NAMES:
        raise ValueError("unsupported growth event")
    initialize_growth_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        result = conn.execute(
            "INSERT OR IGNORE INTO growth_event_outbox(event_key,event_name,occurred_at) VALUES(?,?,?)",
            (event_name, event_name, occurred_at),
        )
        return result.rowcount == 1


def activation_summary(db_path: str | Path, *, connected: bool, is_demo: bool, nm_id: int | None) -> dict:
    # mode=ro avoids silently creating a new tenant database during a GET.
    with sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        latest = None
        if "sync_log" in tables:
            latest = conn.execute("""SELECT status, finished_at, details FROM sync_log
                WHERE COALESCE(details,'') NOT LIKE 'scope: ads%'
                  AND COALESCE(details,'') NOT LIKE 'scope: finance%'
                ORDER BY id DESC LIMIT 1""").fetchone()
        # The full-sync result contains all three markers. A successful isolated
        # advertising/finance refresh cannot stand in for importing store data.
        details = str(latest["details"] or "") if latest else ""
        full_sync = bool(latest and latest["status"] == "success" and latest["finished_at"]
                         and all(re.search(r"(?:^|,\s*)" + key + r":\s*\d+", details)
                                 for key in ("orders", "sales", "financial_rows")))
        cost = None
        if nm_id and "costs" in tables:
            cost = conn.execute("SELECT cost_per_wb_unit,updated_at FROM costs WHERE nm_id=?", (nm_id,)).fetchone()
        amount = float(cost[0] or 0) if cost else 0.0
        cost_ready = bool(cost and cost[1] and math.isfinite(amount) and amount > 0)
        last_finance = None
        financial_rows = 0
        window = None
        recommended_product = None
        if not nm_id and full_sync and "financial_report" in tables:
            sold = conn.execute("""SELECT nm_id, MAX(SUBSTR(operation_date,1,10)) AS latest_sale_at,
                    SUM(ABS(COALESCE(quantity,0))) AS sold_units,
                    SUM(ABS(COALESCE(retail_amount,0))) AS revenue
                FROM financial_report
                WHERE nm_id > 0 AND (COALESCE(quantity,0) != 0 OR COALESCE(retail_amount,0) != 0)
                GROUP BY nm_id
                ORDER BY latest_sale_at DESC, sold_units DESC, revenue DESC, nm_id ASC
                LIMIT 1""").fetchone()
            if sold:
                product_name = ""
                supplier_article = ""
                if "products_catalog" in tables:
                    catalog = conn.execute("""SELECT supplier_article, product_name FROM products_catalog
                        WHERE nm_id=? LIMIT 1""", (int(sold[0]),)).fetchone()
                    if catalog:
                        supplier_article = str(catalog[0] or "")
                        product_name = str(catalog[1] or "")
                recommended_product = {
                    "nm_id": int(sold[0]),
                    "product_name": product_name,
                    "supplier_article": supplier_article,
                    "latest_sale_at": str(sold[1] or ""),
                }
        if nm_id and "financial_report" in tables:
            row = conn.execute("""SELECT MAX(SUBSTR(operation_date,1,10)) FROM financial_report
                WHERE nm_id=? AND (COALESCE(quantity,0)!=0 OR COALESCE(retail_amount,0)!=0)""", (nm_id,)).fetchone()
            try:
                last_finance = date.fromisoformat(row[0]) if row and row[0] else None
            except (ValueError, TypeError):
                pass
            if last_finance:
                start = last_finance - timedelta(days=29)
                window = {"start": start.isoformat(), "end": last_finance.isoformat()}
                financial_rows = conn.execute("""SELECT COUNT(*) FROM financial_report WHERE nm_id=?
                    AND SUBSTR(operation_date,1,10) BETWEEN ? AND ?
                    AND (COALESCE(quantity,0)!=0 OR COALESCE(retail_amount,0)!=0)""",
                    (nm_id, window["start"], window["end"])).fetchone()[0]
        first = conn.execute("SELECT nm_id,occurred_at FROM growth_first_value WHERE event_key='first_useful_report'").fetchone() if "growth_first_value" in tables else None

    next_step = ("demo" if is_demo else "connect_wb" if not connected else "sync" if not full_sync
                 else "select_product" if not nm_id else "cost" if not cost_ready
                 else "financial_data" if not financial_rows else "open_report")
    return {
        "schema_version": 1,
        "is_demo": is_demo,
        "wb_connected": connected,
        "sync": {"state": str(latest["status"]) if latest else "never", "complete": full_sync,
                 "successful_full_sync_at": latest["finished_at"] if full_sync else None},
        "selected_product": {"nm_id": nm_id, "cost_configured": cost_ready,
                             "has_financial_rows": bool(financial_rows)},
        "recommended_product": recommended_product,
        "period": window,
        "ready_for_report": next_step == "open_report",
        "next_step": next_step,
        "first_value_verified": bool(first) and not is_demo,
        "first_value_at": first["occurred_at"] if first and not is_demo else None,
    }


def record_first_value(db_path: str | Path, *, nm_id: int, occurred_at: str) -> dict:
    """Call only after authenticated rendering acknowledgment and fresh validation.

    The marker and delivery job commit together. Retries retain the original
    time and never infer past activation from existing tenant data.
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO growth_first_value(event_key,nm_id,occurred_at) VALUES('first_useful_report',?,?)", (nm_id, occurred_at))
        row = conn.execute("SELECT nm_id,occurred_at FROM growth_first_value WHERE event_key='first_useful_report'").fetchone()
        conn.execute("INSERT OR IGNORE INTO growth_event_outbox(event_key,event_name,occurred_at) VALUES('first_useful_report','first_useful_report',?)", (row[1],))
        return {"nm_id": row[0], "occurred_at": row[1]}
