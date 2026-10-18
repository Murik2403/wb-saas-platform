"""Closing the three review findings on the PDF fairness candidate.

1. The Streamlit "generate now" button goes through interactive_pdf (priority + deadline), like the API endpoints.
2. A manual PDF in a shop child process (shop_report_assets.generate_one) honours the background beacon too.
3. A worker that already holds a slot no longer withholds the free second slot from requests, yet announces itself again
   at the end of the tick while a backlog remains, and an announce that lost the lock race leaves no stray file.
"""
import pytest

import report_scheduler
import shop_report_assets as assets
from db import core
from reports import pdf_budget
from reports.pdf_budget import PdfBusy, announce_background_waiting, background_waiting, pdf_slot, try_pdf_slot, withdraw_background_waiting
from reports.store import ReportStore


@pytest.fixture
def host(tmp_path, monkeypatch):
    slots = tmp_path / "slots"
    slots.mkdir()
    monkeypatch.setenv("WB_SAAS_PDF_SLOT_DIR", str(slots))
    monkeypatch.setenv("WB_SAAS_PDF_SLOTS", "2")
    monkeypatch.setattr(pdf_budget, "DATA_DIR", tmp_path / "tenant")
    monkeypatch.setattr(pdf_budget, "POLL_SECONDS", 0.02)
    yield slots
    withdraw_background_waiting()


# ---- 1. Streamlit generate-now ------------------------------------------------------------------------------------------------------

def test_streamlit_generate_now_waits_behind_queued_background_work(host, monkeypatch):
    import streamlit as st
    from pages import reports_page
    built = []
    monkeypatch.setattr(reports_page, "generate_and_save", lambda *args, **kwargs: built.append(1) or (b"%PDF", None))
    monkeypatch.setattr(st, "session_state", {}, raising=False)
    announce_background_waiting()
    reports_page._generate_now(object(), {"id": 1, "name": "x"})
    assert built == [], "a queued background report must not be overtaken by the button"
    kind, text = st.session_state["reports_page_message"]
    assert kind == "error" and "минуту" in text


# ---- 2. shop child manual PDF ----------------------------------------------------------------------------------------------------------

def test_shop_child_manual_pdf_yields_to_queued_background_work_and_stays_queued(host, tmp_path, monkeypatch):
    database = tmp_path / "db.sqlite"
    monkeypatch.setattr(core, "DB_PATH", database)
    monkeypatch.setattr(assets, "DB_PATH", database)
    core.init_db()
    store = ReportStore(database)
    run = store.enqueue_run(store.add_definition(name="queued", metrics=["stocks"], schedule_type="daily", schedule_time="00:00"))
    before = store.list_runs()[0]
    announce_background_waiting()
    with pytest.raises(PdfBusy):
        assets.generate_one(run)
    assert store.list_runs()[0] == before and before["status"] == "queued" and before["attempts"] == 0
    withdraw_background_waiting()
    monkeypatch.setattr(report_scheduler, "build_report_pdf", lambda *args: b"%PDF-1.4 fake")
    monkeypatch.setattr(report_scheduler, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(assets, "persist", lambda: None)
    assets.generate_one(run)
    assert next(row for row in store.list_runs() if row["id"] == run)["status"] == "ok"


# ---- 3. beacon while a slot is held ---------------------------------------------------------------------------------------------------

def test_worker_holding_a_slot_does_not_withhold_the_free_second_slot_from_requests(host, tmp_path, monkeypatch):
    monkeypatch.setattr(report_scheduler, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(report_scheduler, "DB_PATH", tmp_path / "db.sqlite")
    store = ReportStore(tmp_path / "db.sqlite")
    store.enqueue_run(store.add_definition(name="queued", metrics=["stocks"], schedule_type="daily", schedule_time="00:00"))
    seen = {}

    def build_while_holding_a_slot(*args, **kwargs):
        seen["beacon"] = background_waiting()
        with pdf_slot(wait=0):  # a request takes the second slot while the worker builds
            seen["second_slot_free_for_a_request"] = True
        return b"%PDF-1.4 fake", tmp_path / "x.pdf"

    monkeypatch.setattr(report_scheduler, "generate_and_save", build_while_holding_a_slot)
    announce_background_waiting()  # a previous tick had announced
    assert report_scheduler.process_queued(store) == 1
    assert seen == {"beacon": False, "second_slot_free_for_a_request": True}


def test_the_beacon_is_announced_again_at_the_end_of_a_tick_while_a_backlog_remains(host, tmp_path, monkeypatch):
    monkeypatch.setattr(report_scheduler, "DB_PATH", tmp_path / "db.sqlite")
    store = ReportStore(tmp_path / "db.sqlite")
    store.enqueue_run(store.add_definition(name="queued", metrics=["stocks"], schedule_type="daily", schedule_time="00:00"))
    monkeypatch.setattr(report_scheduler, "_check_once", lambda deliver=True: 0)  # the tick itself built nothing, a run is still queued
    report_scheduler.check_once()
    assert background_waiting() is True
    store.claim_next_queued(owner="test")  # backlog gone
    report_scheduler.check_once()
    assert background_waiting() is False


def test_an_announce_that_lost_the_lock_race_leaves_no_stray_file(host, monkeypatch):
    before = set(host.iterdir())
    monkeypatch.setattr(pdf_budget, "_lock", lambda handle: False)
    assert announce_background_waiting() is False
    assert set(host.iterdir()) == before


def test_a_free_slot_goes_to_queued_work_not_to_a_request_polling_at_that_moment(host, monkeypatch):
    import threading
    import time
    from reports.pdf_budget import interactive_pdf
    monkeypatch.setenv("WB_SAAS_PDF_SLOTS", "1")
    built = []
    outcome = {}
    with pdf_slot(wait=0):  # the only slot is busy, so the request polls
        def request():
            try:
                outcome["value"] = interactive_pdf(lambda: built.append(1) or "pdf", wait=3)
            except PdfBusy as exc:
                outcome["busy"] = exc
        thread = threading.Thread(target=request)
        thread.start()
        time.sleep(0.15)  # it has polled at least once
        announce_background_waiting()  # queued work now waits; the slot frees right after
    thread.join(10)
    assert built == [] and "busy" in outcome, "a freed slot must go to the queued work, not to the polling request"
