"""Tenant-local manual expense register.

These rows are management inputs and deliberately stay separate from the
accounting-backed WB financial report.
"""
from __future__ import annotations

from datetime import date, datetime
import math
from typing import Any

from .core import connect

EXPENSE_ARTICLES = (
    "purchase_production", "wb_commission", "logistics_returns", "storage",
    "acceptance", "advertising", "packaging_labor", "defects_losses", "taxes",
)
ALLOCATION_BASES = ("per_unit", "total", "revenue_pct")
SOURCES = ("manual", "import")


def _iso(value: Any, field: str) -> str:
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO date") from exc


def save_manual_expense(
    *, nm_id: int, article: str, amount_rub: float, allocation_basis: str,
    source: str, period_start: Any, period_end: Any, note: str = "",
) -> int:
    nm_id = int(nm_id)
    if nm_id <= 0:
        raise ValueError("nm_id must be positive")
    if article not in EXPENSE_ARTICLES:
        raise ValueError("unsupported expense article")
    amount = float(amount_rub)
    if not math.isfinite(amount) or amount < 0:
        raise ValueError("amount_rub must be finite and non-negative")
    if allocation_basis not in ALLOCATION_BASES:
        raise ValueError("unsupported allocation basis")
    if source not in SOURCES:
        raise ValueError("unsupported expense source")
    start, end = _iso(period_start, "period_start"), _iso(period_end, "period_end")
    if end < start:
        raise ValueError("period_end precedes period_start")
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO manual_expenses(
                nm_id,article,amount_rub,allocation_basis,source,
                period_start,period_end,note,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (nm_id, article, amount, allocation_basis, source, start, end,
             str(note or "").strip(), now, now),
        )
        return int(cur.lastrowid)


def read_manual_expenses(*, nm_id: int | None = None, start: Any = None, end: Any = None) -> list[dict[str, Any]]:
    clauses, params = [], []
    if nm_id is not None:
        clauses.append("nm_id=?")
        params.append(int(nm_id))
    if start is not None:
        clauses.append("period_end>=?")
        params.append(_iso(start, "start"))
    if end is not None:
        clauses.append("period_start<=?")
        params.append(_iso(end, "end"))
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM manual_expenses" + where + " ORDER BY period_start, id", params
        ).fetchall()
    return [dict(row) for row in rows]


def delete_manual_expense(expense_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM manual_expenses WHERE id=?", (int(expense_id),))
        return cur.rowcount == 1
