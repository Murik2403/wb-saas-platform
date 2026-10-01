"""Per-SKU daily trend and previous-period totals for the Products screen.

Read-only, local SQLite only. The series use exactly the sale definitions of
calculations.build_dashboard (price_with_disc first, returns negative), so
sum(revenue series) == the per-SKU "Выручка" of /api/products for the window.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from calculations import _money_series, _prepare_dates
from db import read_table

MAX_POINTS = 60
RISK_DAYS = 7  # same threshold as ProductsView "Риск запаса"


def compute_products_trend(start: date, end: date) -> dict:
    days = (end - start).days + 1
    bucket_days = max(1, -(-days // MAX_POINTS))  # ceil
    points = -(-days // bucket_days)
    dates = [(start + timedelta(days=i * bucket_days)).isoformat() for i in range(points)]

    sales = _prepare_dates(read_table("sales", omit_raw=True), "sale_date")
    items: list[dict] = []
    if not sales.empty:
        sales = sales[(sales["day"] >= start) & (sales["day"] <= end)].copy()
    if not sales.empty:
        sales["revenue"] = _money_series(sales, ["price_with_disc", "finished_price", "total_price"]).abs()
        sales["units"] = np.where(sales["is_return"].fillna(0).astype(int) == 1, -1, 1)
        sales["revenue"] = sales["revenue"] * sales["units"]
        sales["nm"] = pd.to_numeric(sales["nm_id"], errors="coerce").fillna(0).astype(int)
        sales["bucket"] = sales["day"].map(lambda d: (d - start).days // bucket_days)
        sales = sales[sales["nm"] > 0]
        grouped = sales.groupby(["nm", "bucket"])[["revenue", "units"]].sum()
        for nm_id, part in grouped.groupby(level=0):
            revenue, units = [0.0] * points, [0] * points
            for (_, bucket), row in part.iterrows():
                revenue[int(bucket)] = round(float(row["revenue"]), 2)
                units[int(bucket)] = int(row["units"])
            items.append({"nm_id": int(nm_id), "revenue": revenue, "units": units})
    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "bucket_days": bucket_days, "dates": dates, "items": items,
    }


def products_totals(products: list[dict]) -> dict:
    """The four ProductsView tiles, from compute_products()["products"] rows
    (same arithmetic as the tiles in App.tsx; null margin counts as 0 there)."""
    revenue = sum(float(p.get("revenue") or 0) for p in products)
    profit = sum(float(p.get("margin") or 0) for p in products)
    at_risk = sum(
        1 for p in products
        if isinstance(p.get("days_of_stock"), (int, float)) and 0 <= p["days_of_stock"] < RISK_DAYS
    )
    return {
        "revenue": revenue,
        "margin_pct": profit / revenue * 100 if revenue > 0 else 0.0,
        "at_risk": at_risk,
        "sku_count": len(products),
    }
