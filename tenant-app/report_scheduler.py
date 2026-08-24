"""Background loop that generates scheduled PDF reports.

Same subprocess-loop shape as sync.py/agent_sync.py, but checks every
5 minutes instead of sleeping for a whole interval -- schedule_time needs
to be hit reasonably close to the minute, not once every 30-60 minutes.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from config import DB_PATH
from reports import delivery
from reports.pdf_builder import build_report_pdf
from reports.store import ReportStore

CHECK_INTERVAL_SECONDS = 300
REPORTS_DIR = DB_PATH.parent / "reports"


def _now_msk() -> datetime:
    return datetime.now(ZoneInfo("Europe/Moscow")).replace(tzinfo=None, second=0, microsecond=0)


def _is_due(definition: dict, now: datetime) -> bool:
    # Checked every CHECK_INTERVAL_SECONDS (5 min), not every minute, so an
    # exact-minute match would silently never fire for most schedule_time
    # values depending on container start offset. ">=" plus the once-per-day
    # guard below means it fires at the first check after schedule_time,
    # up to 5 minutes late, but reliably.
    hh, mm = (int(x) for x in definition["schedule_time"].split(":"))
    if (now.hour, now.minute) < (hh, mm):
        return False

    last_run_at = definition.get("last_run_at")
    if last_run_at:
        last_run = datetime.fromisoformat(last_run_at)
        if last_run.date() == now.date():
            return False  # уже отработали сегодня, не дублируем в течение той же минуты/после рестарта

    schedule_type = definition["schedule_type"]
    if schedule_type == "daily":
        return True
    if schedule_type == "weekly":
        return now.weekday() == int(definition["schedule_weekday"])
    if schedule_type == "monthly":
        return now.day == int(definition["schedule_day"])
    return False


def generate_and_save(store: ReportStore, definition: dict, *, period_days: int = 30) -> tuple[bytes, Path]:
    """Builds the PDF and saves it to disk; records the run either way.
    Deliberately does NOT do email/Telegram delivery -- see deliver_report()
    below, split out so a synchronous caller (reports_page.py's "Сгенерировать
    сейчас" button) can run delivery in a background thread instead of
    blocking on it (Telegram's network path is documented as intermittently
    slow/unreachable from this host, see reports/delivery.py)."""
    end = date.today()
    start = end - timedelta(days=period_days - 1)
    run_id = store.start_run(definition["id"])
    try:
        metric_codes = json.loads(definition["metrics"])
        pdf_bytes = build_report_pdf(definition["name"], metric_codes, start, end)

        out_dir = REPORTS_DIR / str(definition["id"])
        out_dir.mkdir(parents=True, exist_ok=True)
        file_path = out_dir / f"{datetime.now():%Y%m%d_%H%M%S}.pdf"
        file_path.write_bytes(pdf_bytes)

        store.finish_run(run_id, status="ok", file_path=str(file_path))
        store.set_last_run(definition["id"], datetime.now(ZoneInfo("Europe/Moscow")).replace(tzinfo=None).isoformat(timespec="seconds"))
        return pdf_bytes, file_path
    except Exception as exc:
        store.finish_run(run_id, status="error", error=str(exc))
        raise


def deliver_report(definition: dict, pdf_bytes: bytes, filename: str) -> None:
    """Best-effort side-channel delivery -- never raises (see
    delivery.send_report_email/send_report_telegram's own contracts). A
    failed delivery must not undo an already-successful, already-saved
    generation."""
    if definition.get("email_enabled"):
        delivery.send_report_email(definition["name"], pdf_bytes, filename)
    if definition.get("telegram_enabled"):
        delivery.send_report_telegram(definition["name"], pdf_bytes, filename)


def run_definition(store: ReportStore, definition: dict, *, period_days: int = 30) -> None:
    """Synchronous generate + deliver -- used by the background scheduler
    loop (check_once/loop), where blocking on a slow delivery is harmless
    since it's already off the UI thread."""
    pdf_bytes, file_path = generate_and_save(store, definition, period_days=period_days)
    deliver_report(definition, pdf_bytes, file_path.name)


def check_once() -> int:
    store = ReportStore(DB_PATH)
    now = _now_msk()
    generated = 0
    for definition in store.list_definitions(enabled_only=True):
        if _is_due(definition, now):
            try:
                run_definition(store, definition)
                generated += 1
            except Exception as exc:
                print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Ошибка отчёта '{definition['name']}': {exc}", flush=True)
    return generated


def loop() -> None:
    while True:
        try:
            generated = check_once()
            if generated:
                print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Сгенерировано отчётов: {generated}", flush=True)
        except Exception as exc:
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Ошибка планировщика отчётов: {exc}", flush=True)
        time.sleep(CHECK_INTERVAL_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    if args.loop:
        loop()
    else:
        print(f"Сгенерировано: {check_once()}")


if __name__ == "__main__":
    main()
