"""FBS orders: local mirror of WB assembly tasks, a separate best-effort refresh job, read helpers.

NOT part of the main store sync (sync.py): it is triggered explicitly (POST /api/v1/fbs/refresh),
is rate-limited server-side and any failure is stored in fbs_sync_state, never raised to other pages.

WB endpoints (marketplace-api.wildberries.ru, token category "Маркетплейс"):
  GET  /api/v3/orders?limit<=1000&next=<cursor>&dateFrom=<unix>&dateTo=<unix>  -> {"next": int, "orders": [...]}
  POST /api/v3/orders/status {"orders": [ids<=1000]} -> {"orders": [{"id", "supplierStatus", "wbStatus"}]}

Field mapping (every field optional, parsing is tolerant):
  id -> order_id | nmId -> nm_id | article (or vendorCode) -> article | createdAt (ISO 8601) -> created_at
  convertedPrice / 100 when convertedCurrencyCode=643, otherwise price / 100 for RUB -> price (rubles)
  | warehouseId -> warehouse_id | supplyId -> supply_id
  supplierStatus -> supplier_status: new, confirm (on assembly), complete (in a supply), cancel
  wbStatus -> wb_status: waiting, sorted, ready_for_pickup, sold, canceled, canceled_by_client, declined_by_client, defect

Derived state (state_of): delivered (wb sold) | cancelled (supplier cancel, wb canceled/canceled_by_client/declined_by_client)
  | defect (wb defect) | at_pickup (ready_for_pickup) | in_transit (supplier complete, wb waiting/sorted)
  | processing (supplier new/confirm) | unknown (no status yet).
FINAL states: delivered, cancelled, defect. Statuses are refreshed for non-final orders only.

ESTIMATES: WB gives no status timestamps. status_updated_at = our fetch time when the status pair changed
(or first seen), so durations are accurate to the refresh interval. "Overdue" = derived processing state and
older than OVERDUE_HOURS since createdAt (a constant of ours, not a WB rule).
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any

MIN_INTERVAL_SECONDS = 300
DEFAULT_DAYS, MAX_DAYS = 14, 30
OVERDUE_HOURS = 24
MAX_ORDER_PAGES = 10          # 10 000 orders per run
MAX_STATUS_BATCHES = 3        # 3 000 order ids per run
MARKETPLACE_SCOPE = "Маркетплейс"
NO_SCOPE_MESSAGE = "У токена нет доступа к категории «Маркетплейс»: добавьте её в токен WB, чтобы видеть FBS-заказы."

STATE_LABELS = {
    "processing": "Сборка", "in_transit": "В пути", "at_pickup": "В пункте выдачи", "delivered": "Выкуплен",
    "cancelled": "Отменён", "defect": "Брак", "unknown": "Статус неизвестен",
}
FINAL_STATES = {"delivered", "cancelled", "defect"}
PERIOD_DAYS = {"today": 1, "7d": 7, "14d": 14, "30d": 30}


class TooSoon(Exception):
    def __init__(self, retry_after: int):
        super().__init__("too soon")
        self.retry_after = retry_after


def init(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS fbs_order (order_id INTEGER PRIMARY KEY, nm_id INTEGER, article TEXT, created_at TEXT, "
        "price REAL, warehouse_id INTEGER, supply_id TEXT, supplier_status TEXT, wb_status TEXT, status_updated_at TEXT, "
        "fetched_at TEXT, raw_json TEXT)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fbs_order_created ON fbs_order (created_at)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS fbs_sync_state (id INTEGER PRIMARY KEY CHECK (id = 1), last_run_at TEXT, "
        "last_success_at TEXT, last_error TEXT, last_result TEXT)")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def state_of(supplier_status: str | None, wb_status: str | None) -> str:
    sup, wb = (supplier_status or "").lower(), (wb_status or "").lower()
    if wb == "sold":
        return "delivered"
    if sup in {"cancel", "cancel_carrier"} or wb in {"canceled", "canceled_by_client", "declined_by_client", "canceled_by_carrier"}:
        return "cancelled"
    if wb == "defect":
        return "defect"
    if wb == "ready_for_pickup":
        return "at_pickup"
    # Explicit WB delivery evidence takes precedence over a stale supplier
    # assembly status; waiting alone does not prove handover to delivery.
    if wb in {"sorted", "accepted_by_carrier", "sent_to_carrier", "postponed_delivery"}:
        return "in_transit"
    if sup in {"new", "confirm"}:
        return "processing"
    if sup == "complete" or wb == "waiting":
        return "in_transit"
    return "unknown"


def map_order(raw: dict) -> dict | None:
    order_id = _int(raw.get("id", raw.get("orderId")))
    if order_id is None:
        return None
    # WB returns sale and seller-country currencies separately. The UI labels
    # this amount in rubles; a known foreign amount must never receive ₽.
    # Missing currencyCode remains compatible with older domestic snapshots.
    price = raw.get("price") if raw.get("currencyCode") in {None, 643} else None
    if raw.get("convertedCurrencyCode") == 643 and isinstance(raw.get("convertedPrice"), (int, float)) and not isinstance(raw.get("convertedPrice"), bool):
        price = raw["convertedPrice"]
    return {
        "order_id": order_id, "nm_id": _int(raw.get("nmId")), "article": raw.get("article") or raw.get("vendorCode"),
        "created_at": raw.get("createdAt") if parse_ts(raw.get("createdAt")) else None,
        "price": round(price / 100, 2) if isinstance(price, (int, float)) and not isinstance(price, bool) else None,
        "warehouse_id": _int(raw.get("warehouseId")), "supply_id": str(raw["supplyId"]) if raw.get("supplyId") else None,
    }


def error_message(exc: Exception) -> str:
    text = str(exc)
    if "403" in text:
        return NO_SCOPE_MESSAGE
    if "401" in text:
        return "Токен WB отклонён: переподключите его в настройках."
    if "429" in text or "частот" in text:
        return "WB ограничил частоту запросов. Попробуйте позже."
    return "Данные FBS-заказов WB временно недоступны. Повторите позже."


def read_state(conn: sqlite3.Connection, *, has_scope: bool | None = None, now: datetime | None = None) -> dict:
    init(conn)
    now = now or utcnow()
    row = conn.execute("SELECT last_run_at, last_success_at, last_error, last_result FROM fbs_sync_state WHERE id = 1").fetchone()
    last_run, success, error, result = (row[0], row[1], row[2], row[3]) if row else (None, None, None, None)
    ran = parse_ts(last_run)
    next_at = ran + timedelta(seconds=MIN_INTERVAL_SECONDS) if ran else None
    wait = max(0, int((next_at - now).total_seconds() + 0.999)) if next_at and next_at > now else 0
    try:
        last_result = json.loads(result) if result else None
    except ValueError:
        last_result = None
    return {
        "last_run_at": last_run, "last_success_at": success, "last_error": error, "last_result": last_result,
        "min_interval_seconds": MIN_INTERVAL_SECONDS, "retry_after_seconds": wait,
        "has_marketplace_scope": has_scope, "overdue_hours": OVERDUE_HOURS,
        "orders_total": conn.execute("SELECT COUNT(*) FROM fbs_order").fetchone()[0],
    }


def _claim(conn: sqlite3.Connection, now: datetime) -> None:
    """Atomically reserve the run slot (so two simultaneous requests cannot both call WB)."""
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute("SELECT last_run_at FROM fbs_sync_state WHERE id = 1").fetchone()
    ran = parse_ts(row[0]) if row else None
    if ran and (now - ran).total_seconds() < MIN_INTERVAL_SECONDS:
        conn.rollback()
        raise TooSoon(int(MIN_INTERVAL_SECONDS - (now - ran).total_seconds()) + 1)
    conn.execute("INSERT INTO fbs_sync_state (id, last_run_at) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET last_run_at = excluded.last_run_at",
                 (_iso(now),))
    conn.commit()


def _finish(conn: sqlite3.Connection, now: datetime, error: str | None, result: dict | None) -> None:
    conn.execute("UPDATE fbs_sync_state SET last_error = ?, last_result = ?, last_success_at = CASE WHEN ? IS NULL THEN ? ELSE last_success_at END WHERE id = 1",
                 (error, json.dumps(result, ensure_ascii=False) if result else None, error, _iso(now)))
    conn.commit()


def _upsert_orders(conn: sqlite3.Connection, rows: list[dict], now: datetime) -> int:
    n = 0
    for raw in rows:
        m = map_order(raw)
        if not m:
            continue
        conn.execute(
            "INSERT INTO fbs_order (order_id, nm_id, article, created_at, price, warehouse_id, supply_id, fetched_at, raw_json) "
            "VALUES (:order_id, :nm_id, :article, :created_at, :price, :warehouse_id, :supply_id, :fetched, :raw) "
            "ON CONFLICT(order_id) DO UPDATE SET nm_id = excluded.nm_id, article = excluded.article, "
            "created_at = COALESCE(excluded.created_at, created_at), price = excluded.price, warehouse_id = excluded.warehouse_id, "
            "supply_id = COALESCE(excluded.supply_id, supply_id), fetched_at = excluded.fetched_at, raw_json = excluded.raw_json",
            {**m, "fetched": _iso(now), "raw": json.dumps(raw, ensure_ascii=False, default=str)[:4000]})
        n += 1
    conn.commit()
    return n


def _open_ids(conn: sqlite3.Connection, since: datetime, limit: int) -> list[int]:
    rows = conn.execute("SELECT order_id, supplier_status, wb_status FROM fbs_order WHERE created_at >= ? OR created_at IS NULL "
                        "ORDER BY created_at DESC", (_iso(since),)).fetchall()
    return [r[0] for r in rows if state_of(r[1], r[2]) not in FINAL_STATES][:limit]


def _apply_statuses(conn: sqlite3.Connection, rows: list[dict], now: datetime) -> int:
    n = 0
    for raw in rows:
        oid = _int(raw.get("id"))
        if oid is None:
            continue
        sup, wb = raw.get("supplierStatus"), raw.get("wbStatus")
        cur = conn.execute("SELECT supplier_status, wb_status, status_updated_at FROM fbs_order WHERE order_id = ?", (oid,)).fetchone()
        if cur is None:
            continue
        changed = cur[2] is None or (cur[0], cur[1]) != (sup, wb)
        conn.execute("UPDATE fbs_order SET supplier_status = ?, wb_status = ?, status_updated_at = ? WHERE order_id = ?",
                     (sup, wb, _iso(now) if changed else cur[2], oid))
        n += 1
    conn.commit()
    return n


def refresh(conn: sqlite3.Connection, api: Any, *, days: int = DEFAULT_DAYS, has_scope: bool | None = None,
            now: datetime | None = None, pause: float = 0.25) -> dict:
    """One bounded refresh run. Raises TooSoon under the minimum interval; never raises on WB failures
    (they land in fbs_sync_state.last_error). Returns the current state."""
    now = now or utcnow()
    days = max(1, min(int(days), MAX_DAYS))
    init(conn)
    _claim(conn, now)
    if has_scope is False:
        _finish(conn, now, NO_SCOPE_MESSAGE, None)
        return read_state(conn, has_scope=has_scope, now=now)
    since = now - timedelta(days=days)
    result = {"orders": 0, "statuses": 0, "pages": 0, "truncated": False}
    try:
        cursor = 0
        for page in range(MAX_ORDER_PAGES):
            if page and pause:
                time.sleep(pause)
            data = api.get_fbs_orders(int(since.timestamp()), int(now.timestamp()), 1000, cursor)
            result["pages"] += 1
            result["orders"] += _upsert_orders(conn, data.get("orders") or [], now)
            nxt = _int(data.get("next"))
            if not data.get("orders") or not nxt or nxt == cursor:
                break
            cursor = nxt
        else:
            result["truncated"] = True
        ids = _open_ids(conn, since, MAX_STATUS_BATCHES * 1000)
        for i in range(0, len(ids), 1000):
            if pause:
                time.sleep(pause)
            result["statuses"] += _apply_statuses(conn, api.get_fbs_order_statuses(ids[i:i + 1000]), now)
        _finish(conn, now, None, result)
    except Exception as exc:  # best-effort: partial data stays, error is shown calmly
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        _finish(conn, now, error_message(exc), result)
    return read_state(conn, has_scope=has_scope, now=now)


def _decorate(row: sqlite3.Row | tuple, now: datetime) -> dict:
    oid, nm, art, created, price, wh, supply, sup, wb, upd = row
    state = state_of(sup, wb)
    start, changed = parse_ts(created), parse_ts(upd)
    final = state in FINAL_STATES
    end = changed if final else now
    age = round((end - start).total_seconds() / 3600, 1) if start and end and end >= start else None
    overdue = state == "processing" and age is not None and age > OVERDUE_HOURS
    return {"order_id": oid, "nm_id": nm, "article": art, "created_at": created, "price": price, "warehouse_id": wh,
            "supply_id": supply, "supplier_status": sup, "wb_status": wb, "state": state, "state_label": STATE_LABELS[state],
            "age_hours": age, "overdue": overdue, "_start": start, "_changed": changed}


_COLS = "order_id, nm_id, article, created_at, price, warehouse_id, supply_id, supplier_status, wb_status, status_updated_at"


def _window(conn: sqlite3.Connection, days: int, now: datetime) -> list[dict]:
    init(conn)
    since = _iso(now - timedelta(days=days))
    rows = conn.execute(f"SELECT {_COLS} FROM fbs_order WHERE created_at >= ? ORDER BY created_at DESC, order_id DESC", (since,)).fetchall()
    return [_decorate(r, now) for r in rows]


def list_orders(conn: sqlite3.Connection, *, status: str | None = None, overdue: bool = False, warehouse: int | None = None,
                days: int = DEFAULT_DAYS, limit: int = 50, offset: int = 0, now: datetime | None = None) -> dict:
    now = now or utcnow()
    days = max(1, min(days, MAX_DAYS))
    rows = _window(conn, days, now)
    warehouses: dict[int, int] = {}
    for r in rows:
        if r["warehouse_id"] is not None:
            warehouses[r["warehouse_id"]] = warehouses.get(r["warehouse_id"], 0) + 1
    picked = [r for r in rows if (not status or r["state"] == status) and (not overdue or r["overdue"])
              and (warehouse is None or r["warehouse_id"] == warehouse)]
    page = picked[offset:offset + limit]
    for r in page:
        r.pop("_start"), r.pop("_changed")
    return {"items": page, "total": len(picked), "limit": limit, "offset": offset, "days": days,
            "overdue_hours": OVERDUE_HOURS, "overdue_is_estimate": True,
            "warehouses": [{"id": k, "count": v} for k, v in sorted(warehouses.items())]}


def analytics(conn: sqlite3.Connection, *, days: int = DEFAULT_DAYS, now: datetime | None = None) -> dict:
    now = now or utcnow()
    rows = _window(conn, days, now)
    counts = {k: 0 for k in STATE_LABELS}
    by_day: dict[str, dict[str, int]] = {}
    hours: list[float] = []
    for r in rows:
        counts[r["state"]] += 1
        day = (r["created_at"] or "")[:10]
        d = by_day.setdefault(day, {"total": 0, "delivered": 0, "cancelled": 0})
        d["total"] += 1
        if r["state"] == "delivered":
            d["delivered"] += 1
            if r["age_hours"] is not None:
                hours.append(r["age_hours"])
        elif r["state"] in {"cancelled", "defect"}:
            d["cancelled"] += 1
    final = counts["delivered"] + counts["cancelled"] + counts["defect"]
    return {
        "days": days, "total": len(rows),
        "by_status": [{"key": k, "label": STATE_LABELS[k], "count": counts[k]} for k in STATE_LABELS],
        "final_count": final,
        "buyout_pct": round(counts["delivered"] / final * 100, 1) if final else None,
        "avg_delivery_hours": round(sum(hours) / len(hours), 1) if hours else None,
        "avg_delivery_sample": len(hours),
        "overdue_count": sum(1 for r in rows if r["overdue"]), "overdue_hours": OVERDUE_HOURS,
        "by_day": [{"day": k, **v} for k, v in sorted(by_day.items()) if k],
        "estimate_note": "Статусы WB не содержат времени смены: сроки считаются по моменту нашего обновления.",
    }
