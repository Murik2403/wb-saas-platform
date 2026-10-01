"""План-факт по артикулу (РНП): pure calculations, no I/O.

Everything is a plain, labelled heuristic. NO machine learning.

Per article and per calendar day d of the window (MSK dates):

* plan_orders        - the number the seller typed (rnp_plan), None when not set.
* fact_orders        - non-cancelled orders of that order date: pieces and rubles
                       (same definition as calculations.build_dashboard).
* fact_sales         - sales minus returns of that sale date (pieces, rubles), same
                       sign/amount rules as products_trend.py. Future days: None.
* forecast_sales     - pieces/day, "среднее за последние 7 дней":
    - past day or today: mean of the daily net sales of the (up to) 7 days
      BEFORE d (days with no sales count as 0);
    - future day: mean of the 7 last COMPLETED days (the 7 days ending yesterday),
      the same number for every future day; today is excluded because it is partial.
    - the history never reaches before the SKU's first local sale: a partial first
      week averages only the days that exist (fewer than 7); no history at all -> None;
      a SKU that never sold in the local data -> 0.
  Seasonality, promotions, stock-outs and price elasticity are NOT considered.
* deviation          - fact_orders - plan_orders (abs) and abs / plan * 100 (%);
                       None when the plan is missing, the % is None when plan == 0,
                       and None for today/future (the day is not complete).
* price before/after - mean of total_price / price_with_disc of the non-return
                       sales of that day (the cabinet's "до/после скидки"); None
                       when there were no sales that day. current_* come from the
                       last local sale (same rule as price_advice).
* ad spend, DRR      - ads_daily rows with nm_id > 0; DRR% = ad spend / fact sales
                       rubles * 100 (as the cabinet's DRR); None when no revenue.
* forecast margin    - price_advice.advise_sku at the CURRENT price with the same
                       model (price, WB commission share, fixed per-unit costs from
                       financial_products); None with a reason when data is short.
* margin gap         - forecast margin - plan margin, percentage points.

Division by zero always gives None (JSON null, "-" in the UI).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import price_advice

HISTORY_DAYS = 7
MAX_WINDOW_DAYS = 31
MAX_PLAN = 100000
MAX_FAVORITES = 10
FORECAST_NOTE = "прогноз = среднее за последние 7 дней; акции, сезонность и остатки не учитываются"


def window_days(start: date, end: date) -> list[date]:
    """Inclusive days, clamped to the last MAX_WINDOW_DAYS ending at `end`."""
    if start > end:
        start, end = end, start
    start = max(start, end - timedelta(days=MAX_WINDOW_DAYS - 1))
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def mean_of(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def forecast_for_day(day: date, today: date, units: dict[date, float], history_from: date | None) -> float | None:
    """Mean daily net sales over the (up to) 7 days before `day` (past/today) or
    before today (future). `history_from` = first day with usable history."""
    anchor = day if day <= today else today
    lo = anchor - timedelta(days=HISTORY_DAYS)
    if history_from is not None:
        lo = max(lo, history_from)
    span = [lo + timedelta(days=i) for i in range((anchor - lo).days)]
    return mean_of([float(units.get(d, 0)) for d in span])


def deviation(plan: int | None, fact: float | None) -> tuple[float | None, float | None]:
    if plan is None or fact is None:
        return None, None
    return fact - plan, (round((fact - plan) / plan * 100, 1) if plan else None)


def forecast_margin(row: dict | None, price: Any, discount: Any) -> tuple[float | None, dict]:
    """Margin at the current price from price_advice's model (target 0 so an
    unreachable target never hides the number). Returns (margin_pct, info)."""
    if row is None:
        return None, {"status": "insufficient_data", "note": "Нет данных по товару за период.", "breakeven_final": None}
    out = price_advice.advise_sku(row, price, discount, 0.0)
    margin = out["est_margin_now_pct"]
    return margin, {"status": "ok" if margin is not None else "insufficient_data",
                    "note": "" if margin is not None else out["note"], "breakeven_final": out["breakeven_final"]}


def _r(v: float | None, n: int = 2) -> float | None:
    return None if v is None else round(v, n)


def build_article(
    days: list[date], today: date, *, plan: dict[date, int], orders: dict[date, tuple[int, float]],
    sales: dict[date, tuple[int, float]], first_sale: date | None, history_start: date,
    price_by_day: dict[date, tuple[float, float]], ads: dict[date, float] | None,
    stock_by_day: dict[date, int], plan_margin_pct: float | None, margin_pct: float | None,
) -> dict:
    """`sales` must also contain the days before days[0] needed for the forecast
    (from history_start). `ads` None = no per-SKU ad rows exist."""
    units = {d: float(v[0]) for d, v in sales.items()}
    hist_from = max(history_start, first_sale) if first_sale else history_start
    rows: list[dict] = []
    t = {"plan": 0, "has_plan": False, "orders": 0, "orders_sum": 0.0, "sales": 0, "sales_sum": 0.0,
         "forecast": 0.0, "dev_plan": 0, "dev_fact": 0, "dev_days": 0, "ads": 0.0}
    for d in days:
        started = d <= today
        o = orders.get(d, (0, 0.0)) if started else (None, None)
        s = sales.get(d, (0, 0.0)) if started else (None, None)
        fc = forecast_for_day(d, today, units, hist_from)
        p = plan.get(d)
        dev_abs, dev_pct = deviation(p, o[0]) if d < today else (None, None)
        spend = None if ads is None or not started else round(ads.get(d, 0.0), 2)
        drr = spend / s[1] * 100 if spend is not None and s[1] and s[1] > 0 else None
        pr = price_by_day.get(d)
        rows.append({
            "day": d.isoformat(), "is_future": d > today, "is_today": d == today, "plan_orders": p,
            "fact_orders": o[0], "fact_orders_sum": _r(o[1]), "fact_sales": s[0], "fact_sales_sum": _r(s[1]),
            "forecast_sales": _r(fc, 2), "deviation_abs": _r(dev_abs, 2), "deviation_pct": dev_pct,
            "price_before": _r(pr[0]) if pr else None, "price_after": _r(pr[1]) if pr else None,
            "ad_spend": spend, "drr_pct": _r(drr, 1), "stock": stock_by_day.get(d),
        })
        if p is not None:
            t["plan"] += p
            t["has_plan"] = True
        if started:
            t["orders"] += o[0]
            t["orders_sum"] += o[1]
            t["sales"] += s[0]
            t["sales_sum"] += s[1]
            t["ads"] += spend or 0.0
        if fc is not None:
            t["forecast"] += fc
        if p is not None and d < today:
            t["dev_plan"] += p
            t["dev_fact"] += o[0]
            t["dev_days"] += 1
    dev_abs, dev_pct = deviation(t["dev_plan"], t["dev_fact"]) if t["dev_days"] else (None, None)
    drr = t["ads"] / t["sales_sum"] * 100 if ads is not None and t["sales_sum"] > 0 else None
    gap = margin_pct - plan_margin_pct if margin_pct is not None and plan_margin_pct is not None else None
    return {
        "days": rows,
        "totals": {
            "plan_orders": t["plan"] if t["has_plan"] else None, "fact_orders": t["orders"],
            "fact_orders_sum": _r(t["orders_sum"]), "fact_sales": t["sales"],
            "fact_sales_sum": _r(t["sales_sum"]), "forecast_sales": _r(t["forecast"], 2),
            "deviation_abs": _r(dev_abs, 2), "deviation_pct": dev_pct,
            "ad_spend": _r(t["ads"]) if ads is not None else None, "drr_pct": _r(drr, 1),
        },
        "plan_margin_pct": plan_margin_pct, "forecast_margin_pct": margin_pct, "margin_gap_pp": _r(gap, 1),
    }
