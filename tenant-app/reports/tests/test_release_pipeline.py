from datetime import datetime
from pathlib import Path

import pytest
import report_scheduler as scheduler
from reports.store import ReportStore


def setup_report(tmp_path, monkeypatch):
    store = ReportStore(tmp_path / "reports.db")
    definition_id = store.add_definition(name="Release", metrics=["sales_orders"], schedule_type="daily", schedule_time="09:00", email_enabled=True, telegram_enabled=True)
    monkeypatch.setattr(scheduler, "REPORTS_DIR", tmp_path / "pdf")
    monkeypatch.setattr(scheduler, "_now_msk", lambda: datetime(2026, 9, 18, 0, 10))
    return store, store.list_definitions()[0], definition_id


def test_queue_deduplicates_and_persists_partial_delivery(tmp_path, monkeypatch):
    store, definition, def_id = setup_report(tmp_path, monkeypatch)
    def build(name, metrics, start, end):
        assert start == end == datetime(2026, 9, 18).date()
        return b"%PDF-1.4 fixture"
    monkeypatch.setattr(scheduler, "build_report_pdf", build)
    monkeypatch.setattr(scheduler.delivery, "send_report_email", lambda *a: True)
    monkeypatch.setattr(scheduler.delivery, "send_report_telegram", lambda *a: False)
    first = store.enqueue_run(def_id, period_days=1, delivery_requested=True)
    assert store.enqueue_run(def_id, period_days=1, delivery_requested=True) == first
    assert scheduler.process_queued(store) == 1
    row = ReportStore(store.db_path).list_runs()[0]
    assert row["status"] == "ok"
    assert Path(row["file_path"]).read_bytes().startswith(b"%PDF")
    assert row["delivery_status"] == "error" and "telegram" in row["delivery_error"]


def test_schedule_owns_run_id_even_when_another_run_appears(tmp_path, monkeypatch):
    store, definition, def_id = setup_report(tmp_path, monkeypatch)
    def build(*args):
        store.start_run(def_id)
        return b"%PDF-1.4 fixture"
    monkeypatch.setattr(scheduler, "build_report_pdf", build)
    monkeypatch.setattr(scheduler.delivery, "send_report_email", lambda *a: True)
    monkeypatch.setattr(scheduler.delivery, "send_report_telegram", lambda *a: True)
    scheduler.run_definition(store, definition)
    newest, scheduled = store.list_runs()
    assert scheduled["delivery_requested"] == 1
    assert scheduled["delivery_status"] == "ok"
    assert newest["delivery_status"] == "not_requested"


def test_failed_schedule_does_not_spin_every_five_seconds(tmp_path, monkeypatch):
    store, definition, def_id = setup_report(tmp_path, monkeypatch)
    def fail(*a):
        raise ValueError("PDF failed")
    monkeypatch.setattr(scheduler, "build_report_pdf", fail)
    with pytest.raises(ValueError):
        scheduler.generate_and_save(store, definition)
    assert not scheduler._is_due(store.list_definitions()[0], datetime(2026, 9, 18, 10))
    assert store.list_runs()[0]["status"] == "error"


def test_restart_marks_ambiguous_delivery_without_resending(tmp_path, monkeypatch):
    store, _, def_id = setup_report(tmp_path, monkeypatch)
    run_id = store.start_run(def_id, delivery_requested=True)
    store.finish_run(run_id, status="ok", file_path="ready.pdf")
    assert store.requeue_interrupted_runs() == 0
    assert store.list_runs()[0]["delivery_status"] == "error"
