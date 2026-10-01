"""Ads analytics (totals, previous window, per-day, per-campaign) from local ads_daily.

Row definition is exactly calculations.build_dashboard's `ads_kpi`: only the
authoritative campaign/day rows (nm_id == 0); if the window has none, all rows
(pre-v2.3 fallback). So totals equal the existing Реклама KPI cards.
Zero denominators give None (never inf/NaN).
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from calculations import _prepare_dates
from db import read_table

MAX_POINTS = 60  # same bucketing convention as products_trend
_SUMS = ("views", "clicks", "spend", "atbs", "orders_count", "canceled", "ad_revenue")


def _div(a: float, b: float, k: float = 1.0) -> float | None:
    return round(a / b * k, 2) if b else None


def metrics(s: dict) -> dict:
    """Derived metrics from summed raw values."""
    return {
        "spend": round(s["spend"], 2), "views": int(s["views"]), "clicks": int(s["clicks"]),
        "ctr": _div(s["clicks"], s["views"], 100),
        "cpc": _div(s["spend"], s["clicks"]),
        "cpm": _div(s["spend"], s["views"], 1000),
        "carts": int(s["atbs"]), "cart_conversion": _div(s["atbs"], s["clicks"], 100),
        "orders": int(s["orders_count"]), "order_conversion": _div(s["orders_count"], s["clicks"], 100),
        "cpo": _div(s["spend"], s["orders_count"]),
        "ad_revenue": round(s["ad_revenue"], 2),
        "drr": _div(s["spend"], s["ad_revenue"], 100),
        "canceled": int(s["canceled"]),
    }


def _window_rows(ads: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    if ads.empty:
        return ads
    ads = ads[(ads["day"] >= start) & (ads["day"] <= end)]
    nm = pd.to_numeric(ads["nm_id"], errors="coerce").fillna(0).astype(int)
    total = ads[nm == 0]
    rows = (total if not total.empty else ads).copy()
    for col in _SUMS:
        rows[col] = pd.to_numeric(rows[col], errors="coerce").fillna(0)
    return rows


def _sums(rows: pd.DataFrame) -> dict:
    return {c: (float(rows[c].sum()) if not rows.empty else 0.0) for c in _SUMS}


def compute_ads_analytics(start: date, end: date) -> dict:
    ads = _prepare_dates(read_table("ads_daily", omit_raw=True), "day")
    days = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    cur = _window_rows(ads, start, end)
    prev = _window_rows(ads, prev_start, prev_end)

    bucket_days = max(1, -(-days // MAX_POINTS))
    by_day: list[dict] = []
    if not cur.empty:
        cur["bucket"] = cur["day"].map(lambda d: (d - start).days // bucket_days)
        for bucket, part in cur.groupby("bucket"):
            day = start + timedelta(days=int(bucket) * bucket_days)
            by_day.append({"day": day.isoformat(), **metrics(_sums(part))})
    campaigns = [
        {"campaign_id": int(cid), **metrics(_sums(part))}
        for cid, part in cur.groupby("campaign_id")
    ] if not cur.empty else []
    campaigns.sort(key=lambda r: -r["spend"])
    return {
        "start": start.isoformat(), "end": end.isoformat(), "bucket_days": bucket_days,
        "previous_start": prev_start.isoformat(), "previous_end": prev_end.isoformat(),
        "has_data": not cur.empty, "has_previous_data": not prev.empty,
        "totals": metrics(_sums(cur)),
        "previous_totals": metrics(_sums(prev)) if not prev.empty else None,
        "by_day": by_day, "campaigns": campaigns,
    }
