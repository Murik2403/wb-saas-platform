"""Background loop that generates scheduled PDF reports.

Same subprocess-loop shape as sync.py/agent_sync.py, but checks every
5 minutes instead of sleeping for a whole interval -- schedule_time needs
to be hit reasonably close to the minute, not once every 30-60 minutes.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from config import DB_PATH
from reports import delivery
from reports.pdf_budget import PdfBusy, announce_background_waiting, pdf_slot, try_pdf_slot, withdraw_background_waiting
from reports.store import LEASE_SECONDS, ReportStore

CHECK_INTERVAL_SECONDS = 5
LEASE_OWNER = f"worker-{os.getpid()}"
REPORTS_DIR = DB_PATH.parent / "reports"


class ReportLeaseLost(RuntimeError):
    """This builder may neither publish completion nor deliver another attempt's run."""


def build_report_pdf(name, metric_codes, start, end):
    """Load chart rendering only when a PDF is actually requested."""
    from reports.pdf_builder import build_report_pdf as render_pdf
    return render_pdf(name, metric_codes, start, end)


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


# The generated window follows the schedule cadence: a daily report covers
# one day, weekly covers seven days, and monthly covers thirty days. This keeps
# the report period explicit and predictable for recurring deliveries.
_SCHEDULE_PERIOD_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}


@contextmanager
def _lease_heartbeat(store: ReportStore, run_id: int, owner: str, attempt: int):
    """Keep the run's lease alive while its PDF is built; if this process dies the lease expires and the run is requeued."""
    stop = threading.Event()

    def beat():
        while not stop.wait(LEASE_SECONDS / 3):
            try:
                if not store.heartbeat(run_id, owner, attempt=attempt):
                    break
            except Exception:  # noqa: BLE001 -- a missed beat only shortens the lease; the next one repairs it
                pass
    thread = threading.Thread(target=beat, name=f"report-lease-{run_id}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1)


def generate_and_save(
    store: ReportStore,
    definition: dict,
    *,
    period_days: int | None = None,
    run_id: int | None = None,
) -> tuple[bytes, Path]:
    """Hold admission through claim, rendering, file save and fenced completion."""
    try:
        wait = float(os.environ.get("WB_SAAS_PDF_WAIT_SECONDS", "120"))
    except ValueError:
        wait = 120.0
    with pdf_slot(wait=wait):
        return _generate_and_save_held(store, definition, period_days=period_days, run_id=run_id)


def _generate_and_save_held(
    store: ReportStore,
    definition: dict,
    *,
    period_days: int | None = None,
    run_id: int | None = None,
) -> tuple[bytes, Path]:
    """Builds the PDF and saves it to disk; records the run either way.
    Deliberately does NOT do email/Telegram delivery -- see deliver_report()
    below, split out so a synchronous caller (reports_page.py's "Сгенерировать
    сейчас" button) can run delivery in a background thread instead of
    blocking on it (Telegram's network path is documented as intermittently
    slow/unreachable from this host, see reports/delivery.py).

    `period_days=None` (the default, used by every current caller) picks the
    window from the definition's own schedule_type -- see
    _SCHEDULE_PERIOD_DAYS. An explicit value always wins, for any future
    caller that wants a specific custom range."""
    if period_days is None:
        period_days = _SCHEDULE_PERIOD_DAYS.get(definition.get("schedule_type"), 30)
    end = _now_msk().date()
    start = end - timedelta(days=period_days - 1)
    if run_id is None:
        run_id = store.start_run(definition["id"])
    owner, attempt = store.lease_token(run_id)
    if not store.heartbeat(run_id, owner, attempt=attempt):
        raise ReportLeaseLost("Report lease is no longer owned by this builder")
    try:
        metric_codes = json.loads(definition["metrics"])
        with _lease_heartbeat(store, run_id, owner, attempt):
            pdf_bytes = build_report_pdf(definition["name"], metric_codes, start, end)

        out_dir = REPORTS_DIR / str(definition["id"])
        out_dir.mkdir(parents=True, exist_ok=True)
        file_path = out_dir / f"{end.isoformat()}-{run_id}.pdf"
        file_path.write_bytes(pdf_bytes)

        if not store.finish_run(run_id, status="ok", file_path=str(file_path), owner=owner, attempt=attempt):
            raise ReportLeaseLost("Report completion lease was replaced")
        store.set_last_run(definition["id"], datetime.now(ZoneInfo("Europe/Moscow")).replace(tzinfo=None).isoformat(timespec="seconds"))
        return pdf_bytes, file_path
    except ReportLeaseLost:
        raise  # never overwrite or advance the schedule of a replacement attempt
    except PdfBusy as exc:
        # No slot in time: nothing was built. Do not burn today's schedule slot (set_last_run) for a report that never ran.
        store.finish_run(run_id, status="error", error=str(exc), owner=owner, attempt=attempt)
        raise
    except Exception as exc:
        if store.finish_run(run_id, status="error", error=str(exc), owner=owner, attempt=attempt):
            store.set_last_run(definition["id"], _now_msk().isoformat(timespec="seconds"))
        raise


def deliver_report(
    definition: dict,
    pdf_bytes: bytes,
    filename: str,
    *,
    store: ReportStore | None = None,
    run_id: int | None = None,
) -> dict[str, bool]:
    """Best-effort side-channel delivery -- never raises (see
    delivery.send_report_email/send_report_telegram's own contracts). A
    failed delivery must not undo an already-successful, already-saved
    generation."""
    results: dict[str, bool] = {}
    if definition.get("email_enabled"):
        results["email"] = delivery.send_report_email(definition["name"], pdf_bytes, filename)
    if definition.get("telegram_enabled") and not delivery.telegram_is_paused():
        results["telegram"] = delivery.send_report_telegram(definition["name"], pdf_bytes, filename)
    if store is not None and run_id is not None:
        failures = [channel for channel, ok in results.items() if not ok]
        status = "ok" if results and not failures else ("error" if failures else "not_requested")
        error = f"Не доставлено: {', '.join(failures)}" if failures else None
        store.finish_delivery(run_id, status=status, error=error)
    return results


_deferred = {"due": False}  # a scheduled report found every slot busy during the current tick


def run_definition(store: ReportStore, definition: dict, *, period_days: int | None = None) -> bool:
    """Synchronous generate + deliver -- used by the background scheduler
    loop (check_once/loop), where blocking on a slow delivery is harmless
    since it's already off the UI thread.

    The PDF slot is taken before the run is started and released before delivery: a busy host leaves the definition due
    (returns False, retried on the next tick) instead of recording a failed run, and a slow delivery never holds a slot."""
    if store.has_pending_generation(definition["id"]):
        return False  # a fresh crash lease must expire/recover, never spawn a second scheduled delivery
    slot = try_pdf_slot()
    if slot is None:
        _deferred["due"] = True
        announce_background_waiting()  # requests must not take the slot this schedule is waiting for
        return False
    withdraw_background_waiting()  # holding a slot: this worker no longer waits, so a free second slot is not withheld from requests
    with slot:
        run_id = store.start_run(definition["id"], delivery_requested=bool(definition.get("email_enabled") or definition.get("telegram_enabled")))
        pdf_bytes, file_path = generate_and_save(store, definition, period_days=period_days, run_id=run_id)
    deliver_report(definition, pdf_bytes, file_path.name, store=store, run_id=run_id)
    return True


def process_queued(store: ReportStore, *, limit: int = 1) -> int:
    """Generate manual requests outside the web process."""
    processed = 0
    store.requeue_expired_leases()  # a worker that died mid-build left its lease to expire: give the run back
    for _ in range(limit):
        if not store.has_queued():
            break
        slot = try_pdf_slot()
        if slot is None:
            announce_background_waiting()  # this queued run has priority over requests until it gets a slot
            break  # every PDF slot is busy: the run stays queued (durable) and the next tick tries again
        withdraw_background_waiting()  # holding a slot: not waiting any more (re-announced at the end of the tick while a backlog remains)
        delivery = None
        with slot:
            job = store.claim_next_queued(owner=LEASE_OWNER)
            if job is None:
                break
            run_id = int(job.pop("run_id"))
            period_days = job.pop("period_days", None)
            delivery_requested = bool(job.pop("delivery_requested", 0))
            try:
                pdf_bytes, file_path = generate_and_save(
                    store,
                    job,
                    period_days=int(period_days) if period_days else None,
                    run_id=run_id,
                )
                if delivery_requested:
                    delivery = (job, pdf_bytes, file_path.name, run_id)
            except Exception as exc:
                print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Ошибка ручного отчёта '{job['name']}': {exc}", flush=True)
        if delivery is not None:  # delivery is slow and needs no memory budget: outside the slot
            job, pdf_bytes, filename, run_id = delivery
            deliver_report(job, pdf_bytes, filename, store=store, run_id=run_id)
        processed += 1
    return processed


def check_once(*, deliver: bool = True) -> int:
    _deferred["due"] = False
    try:
        return _check_once(deliver=deliver)
    finally:
        # hold the beacon exactly while queued, due or shop-schedule reports still wait for a slot (including the pause before the
        # next tick, after this worker withdrew it on taking a slot); a crash drops it with the process
        try:
            from shop_schedule_runner import pdf_waiting
            if _deferred["due"] or ReportStore(DB_PATH).has_queued() or pdf_waiting():
                announce_background_waiting()
            else:
                withdraw_background_waiting()
        except Exception:  # noqa: BLE001 - an unreadable store must not leave requests blocked forever: the beacon dies with the process anyway
            withdraw_background_waiting()


def _check_once(*, deliver: bool = True) -> int:
    store = ReportStore(DB_PATH)
    from shop_schedule_runner import run_one
    run_one()
    queued = process_queued(store)
    now = _now_msk()
    generated = queued
    for definition in store.list_definitions(enabled_only=True):
        if _is_due(definition, now) and not store.has_pending_generation(definition["id"]):
            try:
                if deliver:
                    generated += 1 if run_definition(store, definition) else 0
                else:
                    slot = try_pdf_slot()
                    if slot is not None:
                        withdraw_background_waiting()
                        with slot:
                            generate_and_save(store, definition)
                        generated += 1
                    else:
                        _deferred["due"] = True
                        announce_background_waiting()
            except Exception as exc:
                print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Ошибка отчёта '{definition['name']}': {exc}", flush=True)
    return generated


def loop() -> None:
    from sync_lock import sync_lock
    with sync_lock(DB_PATH.with_suffix(".reports.lock")):
        _loop()


def _loop() -> None:
    store = ReportStore(DB_PATH)
    recovered = store.requeue_interrupted_runs()
    if recovered:
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Повторно поставлено прерванных отчётов: {recovered}", flush=True)
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
