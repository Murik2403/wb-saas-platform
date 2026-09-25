from __future__ import annotations

from contextlib import closing
import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR.parents[1] / "tenant-app"))
sys.path.insert(0, str(API_DIR))
spec = importlib.util.spec_from_file_location("store_expense_workflows_test_api", API_DIR / "api_server.py")
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)


@pytest.fixture
def workflow_client(monkeypatch, tmp_path):
    import db.core as core

    database = tmp_path / "store-expense-workflows.sqlite3"
    monkeypatch.setattr(core, "DB_PATH", database)
    monkeypatch.setattr(api, "DB_PATH", database)
    monkeypatch.setattr(api, "IS_DEMO", False)
    core.init_db()
    with closing(TestClient(api.app, base_url="http://127.0.0.1")) as client:
        yield client


def test_recurring_store_expense_templates_generate_month_once(workflow_client):
    client = workflow_client
    created = client.post("/api/v1/settings/store-expense-templates", json={
        "article": "storage", "amount_rub": 15000,
        "note": "Склад", "active": True,
    })
    assert created.status_code == 200, created.text
    template_id = created.json()["id"]
    templates = client.get("/api/v1/settings/store-expense-templates")
    assert templates.status_code == 200
    assert templates.json()["items"][0]["id"] == template_id
    paused = client.patch(
        f"/api/v1/settings/store-expense-templates/{template_id}/active",
        json={"active": False},
    )
    assert paused.status_code == 200
    skipped = client.post("/api/v1/settings/store-expense-templates/generate", json={
        "year": 2024, "month": 2,
    })
    assert skipped.json()["created"] == 0
    resumed = client.patch(
        f"/api/v1/settings/store-expense-templates/{template_id}/active",
        json={"active": True},
    )
    assert resumed.status_code == 200
    blocked_commission = client.post("/api/v1/settings/store-expense-templates", json={
        "article": "wb_commission", "amount_rub": 100,
    })
    assert blocked_commission.status_code == 422

    first = client.post("/api/v1/settings/store-expense-templates/generate", json={
        "year": 2024, "month": 2,
    })
    assert first.status_code == 200, first.text
    assert first.json()["created"] == 1
    second = client.post("/api/v1/settings/store-expense-templates/generate", json={
        "year": 2024, "month": 2,
    })
    assert second.status_code == 200
    assert second.json()["created"] == 0
    assert second.json()["already_exists"] == 1
    expenses = client.get("/api/v1/settings/store-expenses").json()["items"]
    assert len(expenses) == 1
    assert expenses[0]["period_start"] == "2024-02-01"
    assert expenses[0]["period_end"] == "2024-02-29"
    assert expenses[0]["include_in_management_result"] is False


def test_deleting_generated_recurring_expense_does_not_recreate_same_month(workflow_client):
    client = workflow_client
    template = client.post("/api/v1/settings/store-expense-templates", json={
        "article": "storage", "amount_rub": 15000, "note": "Склад", "active": True,
    })
    assert template.status_code == 200, template.text
    first = client.post("/api/v1/settings/store-expense-templates/generate", json={
        "year": 2026, "month": 9,
    })
    assert first.status_code == 200, first.text
    assert first.json()["created"] == 1
    expense = client.get("/api/v1/settings/store-expenses").json()["items"][0]

    deleted = client.delete(f"/api/v1/settings/store-expenses/{expense['id']}")
    assert deleted.status_code == 200, deleted.text
    assert client.get("/api/v1/settings/store-expenses").json()["items"] == []
    same_month = client.post("/api/v1/settings/store-expense-templates/generate", json={
        "year": 2026, "month": 9,
    })
    assert same_month.status_code == 200
    assert same_month.json()["created"] == 0
    assert same_month.json()["already_exists"] == 1
    assert client.get("/api/v1/settings/store-expenses").json()["items"] == []

    next_month = client.post("/api/v1/settings/store-expense-templates/generate", json={
        "year": 2026, "month": 10,
    })
    assert next_month.status_code == 200
    assert next_month.json()["created"] == 1
    rows = client.get("/api/v1/settings/store-expenses").json()["items"]
    assert len(rows) == 1
    assert rows[0]["period_start"] == "2026-10-01"


def test_csv_preview_does_not_write_then_confirmation_is_idempotent(workflow_client):
    client = workflow_client
    content = (
        "article;amount_rub;period_start;period_end;note\n"
        "taxes;300,50;2026-09-01;2026-09-30;USN\n"
    ).encode()
    preview = client.post("/api/v1/settings/store-expenses/import/preview", files={
        "file": ("expenses.csv", content, "text/csv"),
    })
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["valid_count"] == 1
    assert body["invalid_count"] == 0
    assert client.get("/api/v1/settings/store-expenses").json()["items"] == []

    confirmed = client.post(f"/api/v1/settings/store-expenses/import/{body['batch_id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["created_count"] == 1
    repeated = client.post(f"/api/v1/settings/store-expenses/import/{body['batch_id']}/confirm")
    assert repeated.status_code == 200
    assert repeated.json()["already_confirmed"] is True
    assert repeated.json()["expense_ids"] == confirmed.json()["expense_ids"]
    expense = client.get("/api/v1/settings/store-expenses").json()["items"][0]
    assert expense["source"] == "import"
    assert expense["include_in_management_result"] is False

    duplicate_preview = client.post("/api/v1/settings/store-expenses/import/preview", files={
        "file": ("expenses.csv", content, "text/csv"),
    })
    assert duplicate_preview.status_code == 200
    assert duplicate_preview.json()["already_imported"] is True
    assert duplicate_preview.json()["batch_id"] == body["batch_id"]


def test_reuploaded_pending_csv_preview_remains_confirmable(workflow_client):
    client = workflow_client
    content = b"article,amount_rub,period_start,period_end\ntaxes,100,2026-09-01,2026-09-30\n"
    first = client.post("/api/v1/settings/store-expenses/import/preview", files={
        "file": ("expenses.csv", content, "text/csv"),
    })
    assert first.status_code == 200, first.text
    repeated = client.post("/api/v1/settings/store-expenses/import/preview", files={
        "file": ("expenses.csv", content, "text/csv"),
    })
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["already_imported"] is False
    assert repeated.json()["batch_id"] == first.json()["batch_id"]
    confirmed = client.post(
        f"/api/v1/settings/store-expenses/import/{repeated.json()['batch_id']}/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["created_count"] == 1


def test_csv_import_skips_invalid_and_duplicate_rows_and_rejects_unknown_file(workflow_client):
    client = workflow_client
    content = (
        "article,amount_rub,period_start,period_end,note\n"
        "taxes,100,2026-09-01,2026-09-30,tax\n"
        "taxes,100,2026-09-01,2026-09-30,tax\n"
        "wb_commission,10,2026-09-01,2026-09-30,commission\n"
        "taxes,NaN,2026-09-01,2026-09-30,bad\n"
    ).encode()
    preview = client.post("/api/v1/settings/store-expenses/import/preview", files={
        "file": ("expenses.csv", content, "text/csv"),
    })
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["valid_count"] == 1
    assert body["invalid_count"] == 2
    assert body["duplicate_count"] == 1
    confirmed = client.post(f"/api/v1/settings/store-expenses/import/{body['batch_id']}/confirm")
    assert confirmed.json()["created_count"] == 1
    assert len(client.get("/api/v1/settings/store-expenses").json()["items"]) == 1

    invalid = client.post("/api/v1/settings/store-expenses/import/preview", files={
        "file": ("bad.csv", b"name,value\ntax,100\n", "text/csv"),
    })
    assert invalid.status_code == 422


def test_csv_import_rejects_oversize_file(workflow_client):
    response = workflow_client.post("/api/v1/settings/store-expenses/import/preview", files={
        "file": ("large.csv", b"x" * (5 * 1024 * 1024 + 1), "text/csv"),
    })
    assert response.status_code == 422


def test_deleting_generated_recurring_row_keeps_month_tombstone(workflow_client):
    client = workflow_client
    template = client.post("/api/v1/settings/store-expense-templates", json={
        "article": "storage", "amount_rub": 1200, "note": "Склад",
    })
    assert template.status_code == 200
    generated = client.post("/api/v1/settings/store-expense-templates/generate", json={
        "year": 2026, "month": 9,
    })
    assert generated.status_code == 200
    expense_id = generated.json()["expense_ids"][0]
    deleted = client.delete(f"/api/v1/settings/store-expenses/{expense_id}")
    assert deleted.status_code == 200, deleted.text

    repeated_month = client.post("/api/v1/settings/store-expense-templates/generate", json={
        "year": 2026, "month": 9,
    })
    assert repeated_month.status_code == 200
    assert repeated_month.json()["created"] == 0
    assert client.get("/api/v1/settings/store-expenses").json()["items"] == []

    next_month = client.post("/api/v1/settings/store-expense-templates/generate", json={
        "year": 2026, "month": 10,
    })
    assert next_month.status_code == 200
    assert next_month.json()["created"] == 1
