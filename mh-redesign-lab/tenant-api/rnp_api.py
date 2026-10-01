"""План-факт по артикулу (РНП): pinned articles, plan values, plan margin.

Local SQLite only, no WB calls. Formulas live in tenant-app/rnp_planfact.py.
Writes (PUT) are limited to owner/manager by the global role middleware in
api_server (write_allowed: viewer and shop-floor roles get 403 there).
api_server calls configure() once so the economics reuse its cached dashboard builder.
"""
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt

import rnp_planfact as calc
from db import core
from today_service import json_safe

router = APIRouter(prefix="/api/v1/dashboard/rnp", tags=["rnp"])
ECONOMICS_DAYS = 30
PLAN_PAST_DAYS, PLAN_FUTURE_DAYS = 400, 120
_PERIOD_DAYS = {"today": 1, "7d": 7, "14d": 14, "30d": 30, "90d": 90, "custom": 14}
_deps: dict = {}


def configure(build_dashboard, cached_response, advice_columns) -> None:
    """build_dashboard(start, end) -> data with .financial_products; cached_response(key, builder);
    advice_columns() -> {row key: financial_products column} (same map as price advice)."""
    _deps.update(build=build_dashboard, cache=cached_response, columns=advice_columns)


# Bumped after every successful write so the cached GET is rebuilt, without wiping the shared dashboard caches.
_rev = 0


def _bump() -> None:
    global _rev
    _rev += 1


def _today() -> date:
    return datetime.now(ZoneInfo("Europe/Moscow")).date()


def _init(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS rnp_favorite (nm_id INTEGER PRIMARY KEY, position INTEGER NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS rnp_plan (nm_id INTEGER NOT NULL, day TEXT NOT NULL, plan_orders INTEGER NOT NULL, "
                 "PRIMARY KEY (nm_id, day))")
    conn.execute("CREATE TABLE IF NOT EXISTS rnp_target (nm_id INTEGER PRIMARY KEY, plan_margin_pct REAL NOT NULL)")


class FavoritesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nm_ids: list[StrictInt] = Field(max_length=calc.MAX_FAVORITES)


class PlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nm_id: StrictInt = Field(gt=0)
    plan: dict[date, StrictInt | None] = Field(max_length=62)  # null clears the day


class TargetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nm_id: StrictInt = Field(gt=0)
    plan_margin_pct: float | None = Field(ge=0, le=80)


def _pinned(conn) -> list[int]:
    return [r[0] for r in conn.execute("SELECT nm_id FROM rnp_favorite ORDER BY position")]


def _require_pinned(conn, nm_id: int) -> None:
    if nm_id not in _pinned(conn):
        raise HTTPException(422, detail="Сначала закрепите артикул")


def _known(conn, nm_id: int) -> bool:
    return any(conn.execute(f"SELECT 1 FROM {t} WHERE nm_id = ? LIMIT 1", (nm_id,)).fetchone()
               for t in ("products_catalog", "sales", "orders"))


@router.put("/favorites")
def put_favorites(payload: FavoritesInput) -> dict:
    ids = payload.nm_ids
    if any(n <= 0 for n in ids) or len(set(ids)) != len(ids):
        raise HTTPException(422, detail="Артикулы WB: положительные числа без повторов")
    with core.connect() as conn:
        _init(conn)
        unknown = [n for n in ids if not _known(conn, n)]
        if unknown:
            raise HTTPException(422, detail=f"Неизвестные артикулы WB: {', '.join(map(str, unknown))}")
        with conn:
            conn.execute("DELETE FROM rnp_favorite")
            conn.executemany("INSERT INTO rnp_favorite (nm_id, position) VALUES (?, ?)", [(n, i) for i, n in enumerate(ids)])
        _bump()
        return {"nm_ids": _pinned(conn)}


@router.put("/plan")
def put_plan(payload: PlanInput) -> dict:
    today = _today()
    lo, hi = today - timedelta(days=PLAN_PAST_DAYS), today + timedelta(days=PLAN_FUTURE_DAYS)
    for day, value in payload.plan.items():
        if not lo <= day <= hi:
            raise HTTPException(422, detail=f"Дата {day.isoformat()} вне допустимого диапазона")
        if value is not None and not 0 <= value <= calc.MAX_PLAN:
            raise HTTPException(422, detail=f"План заказов: от 0 до {calc.MAX_PLAN}")
    with core.connect() as conn:
        _init(conn)
        _require_pinned(conn, payload.nm_id)
        with conn:
            for day, value in payload.plan.items():
                if value is None:
                    conn.execute("DELETE FROM rnp_plan WHERE nm_id = ? AND day = ?", (payload.nm_id, day.isoformat()))
                else:
                    conn.execute("INSERT INTO rnp_plan (nm_id, day, plan_orders) VALUES (?, ?, ?) "
                                 "ON CONFLICT(nm_id, day) DO UPDATE SET plan_orders = excluded.plan_orders",
                                 (payload.nm_id, day.isoformat(), value))
    _bump()
    return {"nm_id": payload.nm_id, "saved": len(payload.plan)}


@router.put("/target")
def put_target(payload: TargetInput) -> dict:
    with core.connect() as conn:
        _init(conn)
        _require_pinned(conn, payload.nm_id)
        with conn:
            if payload.plan_margin_pct is None:
                conn.execute("DELETE FROM rnp_target WHERE nm_id = ?", (payload.nm_id,))
            else:
                conn.execute("INSERT INTO rnp_target (nm_id, plan_margin_pct) VALUES (?, ?) "
                             "ON CONFLICT(nm_id) DO UPDATE SET plan_margin_pct = excluded.plan_margin_pct",
                             (payload.nm_id, payload.plan_margin_pct))
    _bump()
    return {"nm_id": payload.nm_id, "plan_margin_pct": payload.plan_margin_pct}


_SALE_AMOUNT = ("CASE WHEN COALESCE(price_with_disc,0) <> 0 THEN ABS(price_with_disc) WHEN COALESCE(finished_price,0) <> 0 "
                "THEN ABS(finished_price) ELSE ABS(COALESCE(total_price,0)) END")
_ORDER_AMOUNT = ("CASE WHEN COALESCE(finished_price,0) <> 0 THEN finished_price WHEN COALESCE(price_with_disc,0) <> 0 "
                 "THEN price_with_disc ELSE COALESCE(total_price,0) END")


def _d(text: str) -> date:
    return date.fromisoformat(text)


def _article_data(conn, nm_id: int, days: list[date], today: date, hist_lo: date, ads_exist: bool, snapshots: dict) -> dict:
    last = min(days[-1], today)
    first = days[0]
    orders = {_d(r[0]): (r[1], r[2] or 0.0) for r in conn.execute(
        f"SELECT substr(order_date,1,10) d, COUNT(*), SUM({_ORDER_AMOUNT}) FROM orders WHERE nm_id = ? AND is_cancel = 0 "
        "AND substr(order_date,1,10) BETWEEN ? AND ? GROUP BY d", (nm_id, first.isoformat(), last.isoformat()))}
    sales = {_d(r[0]): (int(r[1]), r[2] or 0.0) for r in conn.execute(
        f"SELECT substr(sale_date,1,10) d, SUM(CASE WHEN is_return = 1 THEN -1 ELSE 1 END), "
        f"SUM(CASE WHEN is_return = 1 THEN -({_SALE_AMOUNT}) ELSE {_SALE_AMOUNT} END) FROM sales WHERE nm_id = ? "
        "AND substr(sale_date,1,10) BETWEEN ? AND ? GROUP BY d", (nm_id, hist_lo.isoformat(), last.isoformat()))}
    first_sale = conn.execute("SELECT MIN(substr(sale_date,1,10)) FROM sales WHERE nm_id = ? AND is_return = 0", (nm_id,)).fetchone()[0]
    price_by_day = {_d(r[0]): (r[1], r[2]) for r in conn.execute(
        "SELECT substr(sale_date,1,10) d, AVG(total_price), AVG(price_with_disc) FROM sales WHERE nm_id = ? AND is_return = 0 "
        "AND total_price > 0 AND substr(sale_date,1,10) BETWEEN ? AND ? GROUP BY d", (nm_id, first.isoformat(), last.isoformat()))}
    ads = None
    if ads_exist:
        ads = {_d(r[0]): r[1] or 0.0 for r in conn.execute(
            "SELECT substr(day,1,10) d, SUM(spend) FROM ads_daily WHERE nm_id = ? AND substr(day,1,10) BETWEEN ? AND ? GROUP BY d",
            (nm_id, first.isoformat(), last.isoformat()))}
    stock_by_day = {day: int(conn.execute("SELECT COALESCE(SUM(quantity),0) FROM stocks WHERE nm_id = ? AND snapshot_at = ?",
                                          (nm_id, at)).fetchone()[0]) for day, at in snapshots["by_day"].items()}
    stock_now = None
    if snapshots["latest"]:
        stock_now = int(conn.execute("SELECT COALESCE(SUM(quantity),0) FROM stocks WHERE nm_id = ? AND snapshot_at = ?",
                                     (nm_id, snapshots["latest"])).fetchone()[0])
    price = conn.execute(
        "SELECT total_price, price_with_disc FROM sales WHERE nm_id = ? AND is_return = 0 AND total_price > 0 AND sale_date >= ? "
        "ORDER BY sale_date DESC LIMIT 1", (nm_id, (today - timedelta(days=60)).isoformat())).fetchone()
    cur = None
    if price:
        total, withdisc = price[0], price[1] or 0
        cur = (round(total), max(0, min(99, round((1 - withdisc / total) * 100))))
    return dict(orders=orders, sales=sales, first_sale=_d(first_sale) if first_sale else None, price_by_day=price_by_day,
                ads=ads, stock_by_day=stock_by_day, stock_now=stock_now, current=cur)


def _economics_rows(nm_ids: list[int], today: date) -> dict[int, dict]:
    frame = getattr(_deps["build"](today - timedelta(days=ECONOMICS_DAYS - 1), today), "financial_products", None)
    rows: dict[int, dict] = {}
    if frame is not None and not frame.empty:
        columns = _deps["columns"]()
        for rec in frame.to_dict("records"):
            row = {key: rec.get(col) for key, col in columns.items()}
            if row.get("nm_id") in nm_ids:
                rows[int(row["nm_id"])] = row
    return rows


def build_rnp(days: list[date], capped: bool) -> dict:
    today = _today()
    hist_lo = min(days[0], today) - timedelta(days=calc.HISTORY_DAYS)
    with core.connect() as conn:
        _init(conn)
        pinned = _pinned(conn)
        out = {"start": days[0].isoformat(), "end": days[-1].isoformat(), "today": today.isoformat(), "capped": capped,
               "max_days": calc.MAX_WINDOW_DAYS, "max_favorites": calc.MAX_FAVORITES,
               "forecast_note": calc.FORECAST_NOTE, "favorites": pinned, "items": [], "ads_note": None,
               "economics_window": {"start": (today - timedelta(days=ECONOMICS_DAYS - 1)).isoformat(), "end": today.isoformat()}}
        if not pinned:
            return out
        ads_exist = bool(conn.execute("SELECT 1 FROM ads_daily WHERE nm_id > 0 AND substr(day,1,10) BETWEEN ? AND ? LIMIT 1",
                                      (days[0].isoformat(), min(days[-1], today).isoformat())).fetchone())
        if not ads_exist:
            out["ads_note"] = "Реклама по артикулам не загружена (нет разбивки по nm_id в ads_daily): ДРР недоступен."
        by_day = {_d(r[0]): r[1] for r in conn.execute(
            "SELECT substr(snapshot_at,1,10) d, MAX(snapshot_at) FROM stocks WHERE substr(snapshot_at,1,10) BETWEEN ? AND ? GROUP BY d",
            (days[0].isoformat(), min(days[-1], today).isoformat()))}
        latest = conn.execute("SELECT MAX(snapshot_at) FROM stocks").fetchone()[0]
        snapshots = {"by_day": by_day, "latest": latest}
        econ = _economics_rows(pinned, today)
        names = {r[0]: (r[1] or "") for r in conn.execute(
            f"SELECT nm_id, supplier_article FROM products_catalog WHERE nm_id IN ({','.join('?' * len(pinned))})", pinned)}
        for nm in pinned:
            data = _article_data(conn, nm, days, today, hist_lo, ads_exist, snapshots)
            plan = {_d(r[0]): r[1] for r in conn.execute("SELECT day, plan_orders FROM rnp_plan WHERE nm_id = ? AND day BETWEEN ? AND ?",
                                                         (nm, days[0].isoformat(), days[-1].isoformat()))}
            target = conn.execute("SELECT plan_margin_pct FROM rnp_target WHERE nm_id = ?", (nm,)).fetchone()
            cur = data["current"]
            margin, info = calc.forecast_margin(econ.get(nm), cur[0] if cur else None, cur[1] if cur else None)
            if cur is None and margin is None:
                info["note"] = "Нет цены: у артикула нет продаж за последние 60 дней."
            art = calc.build_article(days, today, plan=plan, orders=data["orders"], sales=data["sales"], first_sale=data["first_sale"],
                                     history_start=hist_lo, price_by_day=data["price_by_day"], ads=data["ads"],
                                     stock_by_day=data["stock_by_day"], plan_margin_pct=target[0] if target else None, margin_pct=margin)
            cur_final = None if not cur else calc.price_advice.final_price(cur[0], cur[1])
            out["items"].append({
                "nm_id": nm, "vendor_code": (econ.get(nm) or {}).get("vendor_code") or names.get(nm, ""),
                "current_price": cur[0] if cur else None, "current_discount": cur[1] if cur else None, "current_final": cur_final,
                "price_source": "local_sales", "stock_now": data["stock_now"], "stock_snapshot_at": latest,
                "margin_status": info["status"], "margin_note": info["note"], "breakeven_final": info["breakeven_final"], **art})
    return out


@router.get("")
def get_rnp(period: str = Query("14d", pattern="^(today|7d|14d|30d|90d|custom)$"), start: date | None = None,
            end: date | None = None) -> dict:
    today = _today()
    if period == "custom":
        if start is None or end is None:
            raise HTTPException(422, detail="custom_period_requires_start_and_end")
        lo, hi = (start, end) if start <= end else (end, start)
    else:
        hi, lo = today, today - timedelta(days=_PERIOD_DAYS[period] - 1)
    days = calc.window_days(lo, hi)
    capped = (hi - lo).days + 1 > len(days)
    return _deps["cache"](f"rnp:{_rev}:{days[0]}:{days[-1]}", lambda: json_safe(build_rnp(days, capped)))
