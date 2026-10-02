"""Versioned tax worksheets, using manually confirmed tax data per shop."""
from __future__ import annotations

import calendar
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from db import connect
from tax_calculator import EXTRA_MONEY, normalize_options, calculate_tax

BASE_FIELDS = ("received", "offset", "compensation", "other_income", "refunds")
MONEY_FIELDS = (*BASE_FIELDS, "profit_before_tax", "fns_assessed", "paid", *EXTRA_MONEY)


def validate(payload: dict) -> dict:
    month = payload.get("month", "")
    if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", month):
        raise ValueError("Нужен календарный месяц в формате ГГГГ-ММ (2000–2099)")
    if not payload.get("confirmed") or not str(payload.get("source", "")).strip():
        raise ValueError("Укажите основание и подтвердите состав налоговой базы")
    result = {"month": month, "regime": payload.get("regime", "ausn_income"), "confirmed": True,
              "source": str(payload["source"]).strip()}
    if len(result["source"]) > 500:
        raise ValueError("Основание: не больше 500 символов")
    for field in MONEY_FIELDS:
        value = payload.get(field)
        if value is None:
            result[field] = None
            continue
        number = Decimal(str(value))
        if not number.is_finite() or abs(number) > Decimal("1000000000000"):
            raise ValueError("Сумма должна быть конечным числом не больше 1 трлн ₽")
        if field != "profit_before_tax" and number < 0:
            raise ValueError("Суммы доходов, возвратов, начисления и оплаты не могут быть отрицательными")
        if number != number.quantize(Decimal("0.01")):
            raise ValueError("Сумма должна содержать не больше двух знаков после запятой")
        result[field] = float(number)
    for field in ("period_mode", "rate", "has_staff", "vat_mode", "taxpayer_type"):
        result[field] = payload.get(field)
    result["taxpayer_type"] = result["taxpayer_type"] or ("company" if result["regime"] == "osno_company" else "ip")
    result["vat_mode"] = result["vat_mode"] or "exempt"
    result = normalize_options(result)
    # Reject incompatible nonzero fields rather than quietly ignoring them.
    for field, allowed in (("insurance", {"usn_income"}), ("other_ndfl_base", {"osno_ip"}),
                           ("loss", {"ausn_income_expense", "usn_income_expense", "osno_company"})):
        if result.get(field) and result["regime"] not in allowed:
            raise ValueError("Поле " + field + " не применяется в выбранном режиме")
    return result


def calculate(values: dict) -> dict:
    result = calculate_tax(values)
    result.update(after_estimate=None, fns_difference=None, after_fns=None, assessed_minus_paid=None)
    money = lambda key: Decimal(str(values[key]))
    rounded = lambda value: float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    if result["estimate"] is not None:
        tax = Decimal(str(result["estimate"]))
        if values.get("profit_before_tax") is not None:
            result["after_estimate"] = rounded(money("profit_before_tax") - tax)
        if values.get("fns_assessed") is not None:
            result["fns_difference"] = rounded(money("fns_assessed") - tax)
    if values.get("fns_assessed") is not None:
        if values.get("profit_before_tax") is not None:
            result["after_fns"] = rounded(money("profit_before_tax") - money("fns_assessed"))
        if values.get("paid") is not None:
            result["assessed_minus_paid"] = rounded(money("fns_assessed") - money("paid"))
    return result


def _schema(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS tax_reconciliation_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, month TEXT NOT NULL, revision INTEGER NOT NULL,
        values_json TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(month, revision))""")


def read_month(month: str) -> dict:
    if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", month):
        raise ValueError("Некорректный месяц")
    with connect() as conn:
        _schema(conn)
        rows = conn.execute("SELECT * FROM tax_reconciliation_history WHERE month=? ORDER BY revision DESC LIMIT 50", (month,)).fetchall()
    current = json.loads(rows[0]["values_json"]) if rows else {"month": month, "regime": "ausn_income", "confirmed": False, "source": "", **dict.fromkeys(MONEY_FIELDS)}
    year, number = map(int, month.split("-"))
    return {"values": current, "revision": rows[0]["revision"] if rows else 0,
            "period": {"start": (month[:4] + "-01-01") if current.get("period_mode") == "ytd" else month + "-01", "end": f"{month}-{calendar.monthrange(year, number)[1]:02d}"},
            "calculation": calculate(current),
            "history": [{"revision": row["revision"], "actor": row["actor"], "created_at": row["created_at"],
                         "values": json.loads(row["values_json"])} for row in rows],
            "history_limit": 50}


def save_month(payload: dict, expected_revision: int, actor: str) -> dict:
    values = validate(payload)
    serialized = json.dumps(values, ensure_ascii=False, sort_keys=True)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _schema(conn)
        row = conn.execute("SELECT revision, values_json FROM tax_reconciliation_history WHERE month=? ORDER BY revision DESC LIMIT 1", (values["month"],)).fetchone()
        revision = row["revision"] if row else 0
        # Lost-response retries are safe even when their expected revision is now old.
        if not row or row["values_json"] != serialized:
            if revision != expected_revision:
                raise ValueError("revision_conflict")
            conn.execute("INSERT INTO tax_reconciliation_history(month,revision,values_json,actor,created_at) VALUES (?,?,?,?,?)",
                         (values["month"], revision + 1, serialized, actor,
                          datetime.now(timezone.utc).isoformat()))
    return read_month(values["month"])
