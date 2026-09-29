"""Actual cash and dated plans, deliberately separate from accrual P&L."""
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
from .core import connect


def initialize(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS cash_entries (
      id INTEGER PRIMARY KEY, request_key TEXT NOT NULL UNIQUE,
      payload_json TEXT NOT NULL, kind TEXT NOT NULL, direction TEXT NOT NULL,
      planned_date TEXT NOT NULL, actual_date TEXT, amount_minor INTEGER NOT NULL,
      currency TEXT NOT NULL, amount TEXT NOT NULL, exchange_rate TEXT NOT NULL,
      category TEXT NOT NULL, reference TEXT NOT NULL, status TEXT NOT NULL,
      created_at TEXT NOT NULL, actor TEXT);
    CREATE TABLE IF NOT EXISTS cash_entry_events (
      id INTEGER PRIMARY KEY, entry_id INTEGER NOT NULL, status TEXT NOT NULL,
      actual_date TEXT, changed_at TEXT NOT NULL, actor TEXT);
    CREATE TRIGGER IF NOT EXISTS cash_events_no_update BEFORE UPDATE ON cash_entry_events BEGIN SELECT RAISE(ABORT,'cash history is immutable'); END;
    CREATE TRIGGER IF NOT EXISTS cash_events_no_delete BEFORE DELETE ON cash_entry_events BEGIN SELECT RAISE(ABORT,'cash history is immutable'); END;
    CREATE TRIGGER IF NOT EXISTS cash_events_no_replace BEFORE INSERT ON cash_entry_events WHEN EXISTS(SELECT 1 FROM cash_entry_events WHERE id=NEW.id) BEGIN SELECT RAISE(ABORT,'cash history is immutable'); END;
    """)


def _now():
    return datetime.now(timezone.utc).isoformat()


def create_entry(payload, actor=None):
    data = dict(payload)
    if data["kind"] not in {"opening","movement"} or data["direction"] not in {"inflow","outflow"} or data["status"] not in {"planned","actual"}:
        raise ValueError("Недопустимый тип, направление или статус операции")
    for field in ("planned_date", "actual_date"):
        if data.get(field): data[field] = date.fromisoformat(str(data[field])).isoformat()
    amount, rate = Decimal(str(data["amount"])), Decimal(str(data["exchange_rate"]))
    if not amount.is_finite() or not rate.is_finite() or amount < 0 or rate <= 0:
        raise ValueError("Сумма и курс должны быть конечными положительными значениями")
    if data["kind"] != "opening" and amount == 0: raise ValueError("Сумма операции должна быть больше нуля")
    if data["currency"] == "RUB" and rate != 1: raise ValueError("Для RUB курс должен быть равен 1")
    minor = int((amount * rate * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if data["kind"] != "opening" and minor == 0: raise ValueError("Сумма меньше одной копейки")
    if minor > 10**15: raise ValueError("Сумма превышает допустимый предел")
    if data["status"] == "actual" and not data.get("actual_date"):
        raise ValueError("Укажите дату фактического платежа")
    if data["status"] == "planned" and data.get("actual_date"):
        raise ValueError("У плана не может быть фактической даты")
    if data["kind"] == "opening" and (data["status"] != "actual" or data["actual_date"] != data["planned_date"]):
        raise ValueError("Начальный остаток требует совпадающих дат и фактического статуса")
    if (data["kind"] == "opening") != (data["category"] == "opening"):
        raise ValueError("Статья начального остатка требует соответствующего типа записи")
    encoded = json.dumps(data, sort_keys=True, ensure_ascii=False)
    with connect() as conn:
        initialize(conn)
        conn.execute("BEGIN IMMEDIATE")
        old = conn.execute("SELECT * FROM cash_entries WHERE request_key=?", (data["request_key"],)).fetchone()
        if old:
            if old["payload_json"] != encoded: raise ValueError("Ключ операции уже использован для других данных")
            return dict(old)
        if data["kind"] == "opening" and conn.execute("SELECT 1 FROM cash_entries WHERE kind='opening' AND status!='cancelled'").fetchone():
            raise ValueError("Сначала отмените предыдущий начальный остаток")
        cursor = conn.execute("""INSERT INTO cash_entries(request_key,payload_json,kind,direction,planned_date,actual_date,
          amount_minor,currency,amount,exchange_rate,category,reference,status,created_at,actor)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (data["request_key"], encoded, data["kind"], data["direction"],
          data["planned_date"], data.get("actual_date"), minor, data["currency"], str(amount), str(rate),
          data["category"], data["reference"], data["status"], _now(), actor))
        conn.execute("INSERT INTO cash_entry_events(entry_id,status,actual_date,changed_at,actor) VALUES(?,?,?,?,?)",
                     (cursor.lastrowid, data["status"], data.get("actual_date"), _now(), actor))
        return dict(conn.execute("SELECT * FROM cash_entries WHERE id=?", (cursor.lastrowid,)).fetchone())


def change_status(entry_id, status, actual_date=None, actor=None):
    if status not in {"actual", "cancelled"}: raise ValueError("Недопустимый статус")
    if status == "actual": actual_date = date.fromisoformat(str(actual_date)).isoformat()
    elif actual_date: raise ValueError("Дата платежа для отмены не требуется")
    with connect() as conn:
        initialize(conn)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM cash_entries WHERE id=?", (entry_id,)).fetchone()
        if not row: raise LookupError("Операция не найдена")
        if row["status"] == status:
            if status == "actual" and row["actual_date"] != actual_date: raise ValueError("Платёж уже подтверждён другой датой")
            return dict(row)
        if row["status"] == "cancelled": raise ValueError("Отменённую операцию нельзя провести")
        conn.execute("UPDATE cash_entries SET status=?,actual_date=? WHERE id=?", (status, actual_date if status == "actual" else row["actual_date"], entry_id))
        conn.execute("INSERT INTO cash_entry_events(entry_id,status,actual_date,changed_at,actor) VALUES(?,?,?,?,?)", (entry_id,status,actual_date,_now(),actor))
        return dict(conn.execute("SELECT * FROM cash_entries WHERE id=?", (entry_id,)).fetchone())


def cash_report(start, end):
    start, end = date.fromisoformat(str(start)).isoformat(), date.fromisoformat(str(end)).isoformat()
    if start > end: raise ValueError("Начало периода позже окончания")
    with connect() as conn:
        initialize(conn)
        rows = [dict(row) for row in conn.execute("SELECT * FROM cash_entries ORDER BY planned_date,id")]
        events = [dict(row) for row in conn.execute("SELECT * FROM cash_entry_events ORDER BY id")]
    active = [row for row in rows if row["status"] != "cancelled"]
    anchor = next((r for r in active if r["kind"] == "opening"), None)
    anchor_date = anchor["planned_date"] if anchor else None
    valid_period = not anchor_date or start >= anchor_date
    def signed(row): return row["amount_minor"] * (1 if row["direction"] == "inflow" else -1)
    actual = [r for r in active if r["kind"] != "opening" and r["status"] == "actual" and (not anchor_date or r["actual_date"] >= anchor_date)]
    balance = (signed(anchor) if anchor and valid_period else 0) + sum(signed(r) for r in actual if r["actual_date"] < start)
    period = [r for r in actual if start <= r["actual_date"] <= end]
    # Opening cash does not settle unpaid obligations dated before the anchor.
    plans = [r for r in active if r["status"] == "planned" and r["planned_date"] <= end]
    actual_close = balance + sum(signed(r) for r in period)
    days = sorted({r["actual_date"] for r in period} | {max(start, r["planned_date"]) for r in plans})
    running_actual, running_forecast = balance, balance
    daily = []
    for day in days:
        delta = sum(signed(r) for r in period if r["actual_date"] == day)
        planned = sum(signed(r) for r in plans if max(start,r["planned_date"]) == day)
        running_actual += delta
        running_forecast += delta + planned
        daily.append({"date":day,"actual_balance_minor":running_actual,"forecast_balance_minor":running_forecast})
    for row in rows: row.pop("payload_json", None)
    return {"items":rows,"events":events,"start":start,"end":end,"opening_balance_minor":balance,
      "actual_inflow_minor":sum(r["amount_minor"] for r in period if r["direction"]=="inflow"),
      "actual_outflow_minor":sum(r["amount_minor"] for r in period if r["direction"]=="outflow"),
      "actual_closing_minor":actual_close,"forecast_closing_minor":actual_close+sum(signed(r) for r in plans),
      "overdue_planned_minor":sum(signed(r) for r in plans if r["planned_date"]<start),"daily":daily,
      "opening_confirmed":bool(anchor and valid_period),"period_before_opening":not valid_period,
      "coverage":"manual_entries_only","procurement_auto_included":False}
