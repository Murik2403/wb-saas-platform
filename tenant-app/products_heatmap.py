"""SKU x day matrix for one metric (Products "Тепловая карта").

Read-only, local SQLite only. Definitions are the ones of
calculations.build_dashboard / products_trend:
  orders      non-cancelled orders by order_date, pieces
  orders_rub  the same orders, finished_price first
  sales       net buyouts by sale_date (return = -1)
  revenue     net buyout amount, price_with_disc first (return negative)
  ad_spend / ad_orders  ads_daily rows with nm_id > 0 only. Rows with nm_id = 0 are
              campaign-level totals; they are not split by SKU, so they are only
              reported in meta.campaign_level_total, never in the matrix.
Stock and per-day profit are deliberately absent: `stocks` snapshots are not a
guaranteed daily history, and finance data is keyed by report date, not order date.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from calculations import _money_series, _prepare_dates
from db import read_table

MAX_COLUMNS = 60
MAX_ROWS = 60
METRICS = {
    "orders": ("Заказы, шт", "pcs"),
    "orders_rub": ("Заказы, ₽", "rub"),
    "sales": ("Выкупы, шт (за вычетом возвратов)", "pcs"),
    "revenue": ("Выручка, ₽ (за вычетом возвратов)", "rub"),
    "ad_spend": ("Расход на рекламу, ₽", "rub"),
    "ad_orders": ("Заказы с рекламы, шт", "pcs"),
}


def _frame(metric: str, start: date, end: date) -> pd.DataFrame:
    """Rows of (nm, day, value, article) inside [start, end]."""
    cols = ["nm", "day", "value", "article"]
    if metric.startswith("ad_"):
        df = _prepare_dates(read_table("ads_daily", omit_raw=True), "day")
        if df.empty:
            return pd.DataFrame(columns=cols)
        df = df[(df["day"] >= start) & (df["day"] <= end)].copy()
        df["nm"] = pd.to_numeric(df["nm_id"], errors="coerce").fillna(0).astype(int)
        df = df[df["nm"] > 0]
        src = "spend" if metric == "ad_spend" else "orders_count"
        df["value"] = pd.to_numeric(df[src], errors="coerce").fillna(0.0)
        df["article"] = ""
        return df[cols]
    table, date_col = ("orders", "order_date") if metric.startswith("orders") else ("sales", "sale_date")
    df = _prepare_dates(read_table(table, omit_raw=True), date_col)
    if df.empty:
        return pd.DataFrame(columns=cols)
    df = df[(df["day"] >= start) & (df["day"] <= end)].copy()
    if table == "orders":
        df = df[df["is_cancel"].fillna(0).astype(int) == 0]
        if metric == "orders":
            df["value"] = 1.0
        else:
            df["value"] = _money_series(df, ["finished_price", "price_with_disc", "total_price"])
    else:
        sign = np.where(df["is_return"].fillna(0).astype(int) == 1, -1, 1)
        if metric == "sales":
            df["value"] = sign.astype(float)
        else:
            df["value"] = _money_series(df, ["price_with_disc", "finished_price", "total_price"]).abs() * sign
    df["nm"] = pd.to_numeric(df["nm_id"], errors="coerce").fillna(0).astype(int)
    df = df[df["nm"] > 0]
    df["article"] = df["supplier_article"].fillna("").astype(str)
    return df[cols]


def _campaign_level_total(metric: str, start: date, end: date) -> float | None:
    if not metric.startswith("ad_"):
        return None
    df = _prepare_dates(read_table("ads_daily", omit_raw=True), "day")
    if df.empty:
        return 0.0
    df = df[(df["day"] >= start) & (df["day"] <= end)]
    df = df[pd.to_numeric(df["nm_id"], errors="coerce").fillna(0).astype(int) == 0]
    src = "spend" if metric == "ad_spend" else "orders_count"
    return round(float(pd.to_numeric(df[src], errors="coerce").fillna(0).sum()), 2)


def compute_products_heatmap(metric: str, start: date, end: date, max_rows: int = MAX_ROWS) -> dict:
    if metric not in METRICS:
        raise ValueError(metric)
    days = (end - start).days + 1
    bucket_days = max(1, -(-days // MAX_COLUMNS))
    points = -(-days // bucket_days)
    dates = [(start + timedelta(days=i * bucket_days)).isoformat() for i in range(points)]

    df = _frame(metric, start, end)
    names: dict[int, str] = {}
    cells: dict[int, list[float]] = {}
    articles: dict[int, str] = {}
    if not df.empty:
        df["bucket"] = df["day"].map(lambda d: (d - start).days // bucket_days)
        for (nm, bucket), v in df.groupby(["nm", "bucket"])["value"].sum().items():
            cells.setdefault(int(nm), [0.0] * points)[int(bucket)] = float(v)
        for nm, art in df.groupby("nm")["article"].first().items():
            articles[int(nm)] = art
        catalog = read_table("products_catalog", omit_raw=True)
        if not catalog.empty:
            for _, r in catalog.iterrows():
                names[int(r["nm_id"])] = str(r.get("product_name") or "")
                if r.get("supplier_article"):
                    articles[int(r["nm_id"])] = str(r["supplier_article"])

    totals_by_nm = {nm: sum(v) for nm, v in cells.items()}
    order = sorted((nm for nm, v in cells.items() if any(abs(x) > 1e-9 for x in v)),
                   key=lambda nm: (-totals_by_nm[nm], nm))
    top, rest = order[:max_rows], order[max_rows:]

    def rounded(values: list[float]) -> list[float]:
        return [round(x, 2) for x in values]

    rows = [{
        "nm_id": nm, "vendor_code": articles.get(nm, ""), "name": names.get(nm, ""),
        "total": round(totals_by_nm[nm], 2), "values": rounded(cells[nm]),
    } for nm in top]
    other_values = [sum(cells[nm][i] for nm in rest) for i in range(points)]
    other = {"count": len(rest), "total": round(sum(other_values), 2), "values": rounded(other_values)}
    col_totals = [sum(cells[nm][i] for nm in order) for i in range(points)]
    all_cells = [x for r in rows for x in r["values"]]
    return {
        "metric": metric, "label": METRICS[metric][0], "unit": METRICS[metric][1],
        "start": start.isoformat(), "end": end.isoformat(),
        "bucket_days": bucket_days, "dates": dates,
        "rows": rows, "other": other if rest else None,
        "column_totals": rounded(col_totals), "grand_total": round(sum(col_totals), 2),
        "min": min(all_cells) if all_cells else 0.0, "max": max(all_cells) if all_cells else 0.0,
        "campaign_level_total": _campaign_level_total(metric, start, end),
    }
