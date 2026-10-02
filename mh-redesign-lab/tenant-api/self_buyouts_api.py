"""Самовыкупы: ручной журнал затрат (local SQLite only, no WB calls, no effect on profit/P&L/tax).

Writes are limited to owner/manager by the global role middleware in api_server (write_allowed).
Money is integer kopeks. Write routes take the id in the body (exact-path shop dispatch).
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr

import self_buyouts as calc
from db import core
from overview_service import previous_window, resolve_window

router = APIRouter(prefix="/api/v1/self-buyouts", tags=["self-buyouts"])
PAST_DAYS, FUTURE_DAYS = 400, 1
MAX_UNITS, MAX_KOP, MAX_RECORDS, MAX_WINDOW_DAYS = 10000, 100_000_000, 500, 400
_COLUMNS = "id, day, nm_id, vendor_code, units, buyout_amount_kop, cashback_kop, format, note, created_at, actor"


def _today() -> date:
    return datetime.now(ZoneInfo("Europe/Moscow")).date()


def _init(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS self_buyout (id INTEGER PRIMARY KEY, day TEXT NOT NULL, nm_id INTEGER NOT NULL, "
                 "vendor_code TEXT, units INTEGER NOT NULL, buyout_amount_kop INTEGER NOT NULL, cashback_kop INTEGER NOT NULL DEFAULT 0, "
                 "format TEXT, note TEXT, created_at TEXT NOT NULL, actor TEXT)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_self_buyout_day ON self_buyout(day)")


class RecordInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: StrictStr
    nm_id: StrictInt
    units: StrictInt
    buyout_amount_kop: StrictInt
    cashback_kop: StrictInt = 0
    format: StrictStr | None = None
    note: StrictStr | None = None


class UpdateInput(RecordInput):
    id: StrictInt


class DeleteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: StrictInt


def _known(conn, nm_id: int) -> bool:
    return any(conn.execute(f"SELECT 1 FROM {t} WHERE nm_id = ? LIMIT 1", (nm_id,)).fetchone()
               for t in ("products_catalog", "sales", "orders"))


def _clean(conn, p: RecordInput) -> dict:
    errors: dict[str, str] = {}
    today = _today()
    day = None
    try:
        day = date.fromisoformat(p.day)
        if not today - timedelta(days=PAST_DAYS) <= day <= today + timedelta(days=FUTURE_DAYS):
            errors["day"] = "Дата вне допустимого диапазона (не раньше 400 дней назад и не позже завтрашнего дня)"
    except ValueError:
        errors["day"] = "Укажите дату в формате ГГГГ-ММ-ДД"
    if not 1 <= p.units <= MAX_UNITS:
        errors["units"] = f"Количество: от 1 до {MAX_UNITS}"
    if not 0 <= p.buyout_amount_kop <= MAX_KOP:
        errors["buyout_amount_kop"] = "Сумма оплаты: от 0 до 1 000 000 ₽"
    if not 0 <= p.cashback_kop <= MAX_KOP:
        errors["cashback_kop"] = "Кешбэк: от 0 до 1 000 000 ₽"
    label, note = (p.format or "").strip(), (p.note or "").strip()
    if len(label) > 40:
        errors["format"] = "Метка: не длиннее 40 символов"
    if len(note) > 200:
        errors["note"] = "Примечание: не длиннее 200 символов"
    vendor = ""
    if p.nm_id <= 0 or not _known(conn, p.nm_id):
        errors["nm_id"] = "Неизвестный артикул WB: его нет в каталоге, продажах и заказах"
    else:
        row = conn.execute("SELECT supplier_article FROM products_catalog WHERE nm_id = ?", (p.nm_id,)).fetchone()
        vendor = (row[0] if row and row[0] else "") or ""
    if errors:
        raise HTTPException(422, detail={"errors": errors})
    return dict(day=day.isoformat(), nm_id=p.nm_id, vendor_code=vendor, units=p.units, buyout_amount_kop=p.buyout_amount_kop,
                cashback_kop=p.cashback_kop, format=label or None, note=note or None)


def _record(row) -> dict:
    d = dict(zip(_COLUMNS.split(", "), row))
    d["net_cost_kop"] = d["buyout_amount_kop"] - d["cashback_kop"]
    return d


def _fetch(conn, lo: date, hi: date, nm_id: int | None) -> list[tuple]:
    sql, args = f"SELECT {_COLUMNS} FROM self_buyout WHERE day BETWEEN ? AND ?", [lo.isoformat(), hi.isoformat()]
    if nm_id is not None:
        sql, args = sql + " AND nm_id = ?", args + [nm_id]
    return conn.execute(sql + " ORDER BY day DESC, id DESC", args).fetchall()


def _sums(rows) -> dict:
    return calc.totals((r[4], r[5], r[6]) for r in rows)


@router.get("")
def list_self_buyouts(period: str = Query("30d", pattern="^(today|7d|30d|90d|custom)$"), start: date | None = None,
                      end: date | None = None, group: str = Query("day", pattern="^(day|week|month)$"),
                      nm_id: int | None = None) -> dict:
    try:
        lo, hi = resolve_window(period, _today(), start, end)
    except ValueError:
        raise HTTPException(422, detail="custom_period_requires_start_and_end")
    if (hi - lo).days + 1 > MAX_WINDOW_DAYS:
        raise HTTPException(422, detail=f"Период не длиннее {MAX_WINDOW_DAYS} дней")
    prev = previous_window(lo, hi)
    with core.connect() as conn:
        _init(conn)
        rows = _fetch(conn, lo, hi, nm_id)
        prev_rows = _fetch(conn, *prev, nm_id) if prev else []
    current, previous = _sums(rows), _sums(prev_rows)
    return {
        "start": lo.isoformat(), "end": hi.isoformat(), "group": group, "nm_id": nm_id,
        "previous_window": {"start": prev[0].isoformat(), "end": prev[1].isoformat()} if prev else None,
        "totals": current, "previous_totals": previous, "deltas": calc.with_deltas(current, previous),
        "groups": calc.group_rows([(date.fromisoformat(r[1]), r[4], r[5], r[6]) for r in rows], group),
        "records": [_record(r) for r in rows[:MAX_RECORDS]], "truncated": len(rows) > MAX_RECORDS,
    }


def _one(conn, row_id: int) -> dict | None:
    row = conn.execute(f"SELECT {_COLUMNS} FROM self_buyout WHERE id = ?", (row_id,)).fetchone()
    return _record(row) if row else None


@router.post("")
def create_self_buyout(payload: RecordInput, request: Request) -> dict:
    actor = request.headers.get("x-tenant-account", "local-owner")[:80]
    with core.connect() as conn:
        _init(conn)
        values = _clean(conn, payload)
        with conn:
            cur = conn.execute(
                "INSERT INTO self_buyout (day, nm_id, vendor_code, units, buyout_amount_kop, cashback_kop, format, note, created_at, actor) "
                "VALUES (:day, :nm_id, :vendor_code, :units, :buyout_amount_kop, :cashback_kop, :format, :note, :created_at, :actor)",
                {**values, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "actor": actor})
        return _one(conn, cur.lastrowid)


@router.post("/update")
def update_self_buyout(payload: UpdateInput) -> dict:
    with core.connect() as conn:
        _init(conn)
        if _one(conn, payload.id) is None:
            raise HTTPException(404, detail="Запись не найдена")
        values = _clean(conn, payload)
        with conn:
            conn.execute("UPDATE self_buyout SET day=:day, nm_id=:nm_id, vendor_code=:vendor_code, units=:units, "
                         "buyout_amount_kop=:buyout_amount_kop, cashback_kop=:cashback_kop, format=:format, note=:note WHERE id=:id",
                         {**values, "id": payload.id})
        return _one(conn, payload.id)


@router.post("/delete")
def delete_self_buyout(payload: DeleteInput) -> dict:
    with core.connect() as conn:
        _init(conn)
        with conn:
            gone = conn.execute("DELETE FROM self_buyout WHERE id = ?", (payload.id,)).rowcount
    if not gone:
        raise HTTPException(404, detail="Запись не найдена")
    return {"deleted": payload.id}
