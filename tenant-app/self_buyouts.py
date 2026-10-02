"""Manual cost journal of purchases the owner has already made (pure arithmetic).

Money is integer kopeks end to end. Net cost = paid - cashback. No WB data is used or sent.
"""
from __future__ import annotations

from datetime import date, timedelta

GROUPS = ("day", "week", "month")
METRICS = ("units", "paid_kop", "cashback_kop", "net_cost_kop")


def bucket_start(day: date, group: str) -> date:
    """Day itself, ISO-week Monday, or first of the calendar month."""
    if group == "day":
        return day
    if group == "week":
        return day - timedelta(days=day.weekday())
    if group == "month":
        return day.replace(day=1)
    raise ValueError(group)


def totals(rows) -> dict:
    """rows: iterable of (units, paid_kop, cashback_kop)."""
    units = paid = cashback = count = 0
    for u, p, c in rows:
        units, paid, cashback, count = units + u, paid + p, cashback + c, count + 1
    return {"records": count, "units": units, "paid_kop": paid, "cashback_kop": cashback, "net_cost_kop": paid - cashback}


def group_rows(rows, group: str) -> list[dict]:
    """rows: (day: date, units, paid_kop, cashback_kop). Newest bucket first; empty buckets are omitted."""
    acc: dict[date, list] = {}
    for day, u, p, c in rows:
        acc.setdefault(bucket_start(day, group), []).append((u, p, c))
    return [{"start": k.isoformat(), **totals(v)} for k, v in sorted(acc.items(), reverse=True)]


def delta(current: int, previous: int) -> dict:
    """Signed difference; percent is null when the previous value is zero (no division by zero)."""
    diff = current - previous
    pct = None if previous == 0 else round(diff * 1000 / abs(previous)) / 10
    return {"value": current, "previous": previous, "delta": diff, "delta_pct": pct}


def with_deltas(current: dict, previous: dict) -> dict:
    return {m: delta(current[m], previous[m]) for m in METRICS}
