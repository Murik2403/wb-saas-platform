"""Voluntary owner observations, separate from lifecycle conversions/interviews."""
from datetime import date, datetime, timezone
from statistics import median
import config

SCENARIOS = {"wb_finance":"Финансы WB", "ozon_finance":"Финансы Ozon", "production":"Производство", "expenses":"Расходы"}
REASONS = {"none":"Причину отказа не указываю", "price":"Не подходит цена", "complexity":"Сложно пользоваться", "missing_workflow":"Не хватает нужного процесса", "numbers_unclear":"Не удалось сверить цифры", "not_now":"Сейчас нет потребности"}


def validate(scenario, start, end, reason, minutes):
    if scenario not in SCENARIOS or reason not in REASONS:
        raise ValueError("Выберите сценарий и причину из списка.")
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except (ValueError, TypeError):
        raise ValueError("Укажите обе даты периода в формате ГГГГ-ММ-ДД.") from None
    if first.isoformat() != start or last.isoformat() != end or first < date(2000,1,1) or first > last or last > datetime.now(timezone.utc).date() or (last-first).days > 365:
        raise ValueError("Период должен быть прошедшим, не более 366 дней.")
    actual = None
    if minutes != "":
        if not isinstance(minutes, str) or not minutes.isascii() or not minutes.isdigit() or len(minutes) > 5 or int(minutes) > 10080:
            raise ValueError("Укажите фактически затраченные минуты от 0 до 10080 или оставьте поле пустым.")
        actual = int(minutes)
    if actual is None and reason == "none":
        raise ValueError("Укажите фактическое время или выберите причину отказа.")
    return actual


def save(conn, account_id, scenario, start, end, reason, minutes):
    actual = validate(scenario,start,end,reason,minutes)
    conn.execute("INSERT INTO product_feedback(account_id,scenario,period_start,period_end,reason,actual_minutes,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(account_id,scenario,period_start,period_end) DO UPDATE SET reason=excluded.reason,actual_minutes=excluded.actual_minutes,updated_at=excluded.updated_at", (account_id,scenario,start,end,reason,actual,datetime.now(timezone.utc).isoformat(timespec="seconds")))


def summary(conn):
    rows = conn.execute("SELECT f.*,a.email,g.exclusion_reason,t.slug FROM product_feedback f JOIN accounts a ON a.id=f.account_id LEFT JOIN growth_sources g ON g.account_id=a.id LEFT JOIN tenant_instances t ON t.account_id=a.id").fetchall()
    admins = {str(e).strip().lower() for e in config.ADMIN_EMAILS}
    included = [r for r in rows if not r["exclusion_reason"] and r["slug"] != "demo" and r["email"].strip().lower() not in admins]
    scenarios = []
    for key,label in SCENARIOS.items():
        own = [r for r in included if r["scenario"] == key]
        values = [r["actual_minutes"] for r in own if r["actual_minutes"] is not None]
        scenarios.append({"label":label,"responses":len(own),"minutes_observed":len(values),"median_minutes":median(values) if values else None})
    return {"responses":len(included), "organizations":len({r["account_id"] for r in included}), "scenarios":scenarios,
            "reasons":[{"label":label,"responses":sum(r["reason"]==key for r in included)} for key,label in REASONS.items() if key != "none"]}
