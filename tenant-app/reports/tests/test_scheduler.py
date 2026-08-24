from datetime import date, datetime

import report_scheduler
from report_scheduler import _is_due, run_definition
from reports import delivery
from reports.store import ReportStore


def base_definition(**overrides) -> dict:
    definition = {
        "schedule_type": "daily", "schedule_time": "09:00",
        "schedule_weekday": None, "schedule_day": None, "last_run_at": None,
    }
    definition.update(overrides)
    return definition


def test_daily_fires_at_exact_minute():
    now = datetime(2026, 8, 23, 9, 0)
    assert _is_due(base_definition(), now) is True


def test_daily_does_not_fire_before_scheduled_time():
    now = datetime(2026, 8, 23, 8, 59)
    assert _is_due(base_definition(), now) is False


def test_daily_fires_late_if_check_missed_exact_minute():
    # Checks run every 5 min, not every minute, so an exact-minute match
    # would miss most schedules entirely -- catching up within the day is
    # the whole point of the ">=" comparison.
    now = datetime(2026, 8, 23, 9, 3)
    assert _is_due(base_definition(), now) is True


def test_daily_does_not_fire_twice_same_day():
    now = datetime(2026, 8, 23, 9, 0)
    definition = base_definition(last_run_at="2026-08-23T09:00:00")
    assert _is_due(definition, now) is False


def test_daily_fires_again_next_day():
    now = datetime(2026, 8, 24, 9, 0)
    definition = base_definition(last_run_at="2026-08-23T09:00:00")
    assert _is_due(definition, now) is True


def test_weekly_only_fires_on_matching_weekday():
    now = datetime(2026, 8, 23, 10, 0)  # a Sunday (weekday() == 6)
    matching = base_definition(schedule_type="weekly", schedule_time="10:00", schedule_weekday=6)
    other_day = base_definition(schedule_type="weekly", schedule_time="10:00", schedule_weekday=0)
    assert _is_due(matching, now) is True
    assert _is_due(other_day, now) is False


def test_monthly_only_fires_on_matching_day():
    now = datetime(2026, 8, 1, 8, 0)
    matching = base_definition(schedule_type="monthly", schedule_time="08:00", schedule_day=1)
    other_day = base_definition(schedule_type="monthly", schedule_time="08:00", schedule_day=15)
    assert _is_due(matching, now) is True
    assert _is_due(other_day, now) is False


def test_run_definition_sends_email_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(report_scheduler, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(report_scheduler, "build_report_pdf", lambda name, codes, start, end: b"%PDF-1.4 fake")

    captured = {}

    def fake_send(report_name, pdf_bytes, filename):
        captured["report_name"] = report_name
        captured["pdf_bytes"] = pdf_bytes
        captured["filename"] = filename
        return True

    monkeypatch.setattr(delivery, "send_report_email", fake_send)

    store = ReportStore(tmp_path / "test_with_email.sqlite3")
    store.add_definition(
        name="Тест с email", metrics=["sales_orders"], schedule_type="daily", schedule_time="09:00",
        email_enabled=True,
    )
    definition = store.list_definitions()[0]

    run_definition(store, definition)

    assert captured["report_name"] == "Тест с email"
    assert captured["pdf_bytes"] == b"%PDF-1.4 fake"
    run = store.list_runs()[0]
    assert run["status"] == "ok"


def test_run_definition_skips_email_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(report_scheduler, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(report_scheduler, "build_report_pdf", lambda name, codes, start, end: b"%PDF-1.4 fake")

    called = {"count": 0}

    def fake_send(*a, **k):
        called["count"] += 1
        return True

    monkeypatch.setattr(delivery, "send_report_email", fake_send)

    store = ReportStore(tmp_path / "test_without_email.sqlite3")
    store.add_definition(name="Без email", metrics=["ads"], schedule_type="daily", schedule_time="09:00")
    definition = store.list_definitions()[0]

    run_definition(store, definition)

    assert called["count"] == 0


def test_run_definition_sends_telegram_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(report_scheduler, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(report_scheduler, "build_report_pdf", lambda name, codes, start, end: b"%PDF-1.4 fake")

    captured = {}

    def fake_send(report_name, pdf_bytes, filename):
        captured["report_name"] = report_name
        return True

    monkeypatch.setattr(delivery, "send_report_telegram", fake_send)

    store = ReportStore(tmp_path / "test_with_telegram.sqlite3")
    store.add_definition(
        name="Тест с Telegram", metrics=["stocks"], schedule_type="daily", schedule_time="09:00",
        telegram_enabled=True,
    )
    definition = store.list_definitions()[0]

    run_definition(store, definition)

    assert captured["report_name"] == "Тест с Telegram"


def test_run_definition_can_send_both_email_and_telegram(tmp_path, monkeypatch):
    monkeypatch.setattr(report_scheduler, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(report_scheduler, "build_report_pdf", lambda name, codes, start, end: b"%PDF-1.4 fake")

    calls = {"email": 0, "telegram": 0}
    monkeypatch.setattr(delivery, "send_report_email", lambda *a, **k: calls.__setitem__("email", calls["email"] + 1) or True)
    monkeypatch.setattr(delivery, "send_report_telegram", lambda *a, **k: calls.__setitem__("telegram", calls["telegram"] + 1) or True)

    store = ReportStore(tmp_path / "test_with_both.sqlite3")
    store.add_definition(
        name="Оба канала", metrics=["ads"], schedule_type="daily", schedule_time="09:00",
        email_enabled=True, telegram_enabled=True,
    )
    definition = store.list_definitions()[0]

    run_definition(store, definition)

    assert calls == {"email": 1, "telegram": 1}
