"""A request for a PDF while every host slot is busy gets 503 + Retry-After instead of rendering over the memory budget."""
from contextlib import closing
import importlib.util
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR.parents[1] / "tenant-app"))
sys.path.insert(0, str(API_DIR))
spec = importlib.util.spec_from_file_location("pdf_budget_route_api", API_DIR / "api_server.py")
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)
from reports import pdf_budget
from reports.pdf_budget import PdfBusy


@pytest.fixture
def client(monkeypatch, tmp_path):
    from db import core
    monkeypatch.setattr(core, "DB_PATH", tmp_path / "pdf.db")
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "pdf.db")
    monkeypatch.setattr(api, "IS_DEMO", False)
    monkeypatch.setattr(pdf_budget, "DATA_DIR", tmp_path / "tenant")
    monkeypatch.delenv("WB_SAAS_PDF_SLOT_DIR", raising=False)
    core.init_db()
    with closing(TestClient(api.app, base_url="http://127.0.0.1", headers={"x-tenant-role": "owner", "x-tenant-account": "1"})) as value:
        yield value


def test_pdf_download_is_503_with_retry_after_when_no_slot_is_free(client, monkeypatch):
    def busy(*args):
        raise PdfBusy("Сейчас формируется максимум отчётов: повторите через минуту")
    monkeypatch.setattr(api, "build_report_pdf", busy)
    response = client.get("/api/v1/operations/reports/pdf")
    assert response.status_code == 503 and response.headers["retry-after"] == "30"
    assert "максимум отчётов" in response.json()["detail"]


def test_pdf_download_still_works_when_a_slot_is_free(client, monkeypatch):
    monkeypatch.setattr(api, "build_report_pdf", lambda *args: b"%PDF-1.4 ok")
    response = client.get("/api/v1/operations/reports/pdf")
    assert response.status_code == 200 and response.content == b"%PDF-1.4 ok"


def test_pdf_download_waits_at_most_15_seconds_before_building(client, monkeypatch):
    waits = []
    def denied(wait=0):
        waits.append(wait)
        raise PdfBusy("Сейчас формируется максимум отчётов")
    monkeypatch.setattr(pdf_budget, "pdf_slot", denied)
    monkeypatch.setattr(api, "build_report_pdf", lambda *args: pytest.fail("Render must not start before acquiring the slot"))
    response = client.get("/api/v1/operations/reports/pdf")
    assert response.status_code == 503 and response.headers["retry-after"] == "30"
    assert waits == [15]


def test_pdf_download_refuses_unusable_configured_host_budget_before_render(client, monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_bytes(b"dummy")
    monkeypatch.setenv("WB_SAAS_PDF_SLOT_DIR", str(blocker / "slots"))
    real_slot = pdf_budget.pdf_slot
    monkeypatch.setattr(pdf_budget, "pdf_slot", lambda wait=0: real_slot(wait=0))
    monkeypatch.setattr(api, "build_report_pdf", lambda *args: pytest.fail("Configured host budget failure must not bypass admission"))
    response = client.get("/api/v1/operations/reports/pdf")
    assert response.status_code == 503 and response.headers["retry-after"] == "30"


def test_pdf_download_is_refused_at_once_while_queued_background_reports_wait(client, monkeypatch):
    monkeypatch.setattr(pdf_budget, "background_waiting", lambda: True)
    monkeypatch.setattr(api, "build_report_pdf", lambda *args: pytest.fail("a request must not build while queued reports wait for the slots"))
    response = client.get("/api/v1/operations/reports/pdf")
    assert response.status_code == 503 and response.headers["retry-after"] == "30"
    assert "очереди" in response.json()["detail"]


def test_pdf_download_answers_503_before_the_proxy_timeout_when_the_build_is_too_slow(client, monkeypatch):
    """The real-run 504 after 60047 ms: a build that cannot finish inside the deadline is answered 503, never left to the proxy."""
    import threading
    import time
    release = threading.Event()
    monkeypatch.setattr(pdf_budget, "INTERACTIVE_DEADLINE_SECONDS", 0.5)
    monkeypatch.setattr(api, "build_report_pdf", lambda *args: release.wait(20) and b"%PDF-late")
    started = time.monotonic()
    response = client.get("/api/v1/operations/reports/pdf")
    elapsed = time.monotonic() - started
    release.set()
    assert response.status_code == 503 and response.headers["retry-after"] == "30" and elapsed < 5
    assert "дольше допустимого" in response.json()["detail"]

