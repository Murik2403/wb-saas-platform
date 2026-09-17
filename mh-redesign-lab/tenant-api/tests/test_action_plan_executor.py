from __future__ import annotations

import sqlite3

import pytest

from test_display_contract import api


def _use_plan_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    path = tmp_path / "action-plan.sqlite3"
    monkeypatch.setattr(api, "DB_PATH", path)
    api._init_redesign_schema()
    return path


def test_campaign_check_is_completed_and_records_ai_result(monkeypatch, tmp_path):
    _use_plan_db(monkeypatch, tmp_path)
    monkeypatch.setattr(api, "_inspect_campaign_for_plan", lambda campaign_id: {
        "campaign_id": campaign_id, "spend": "1 200 ₽", "drr": "5,2%", "revenue": "23 000 ₽",
    })
    created = api.create_action_plan_item(api.ActionPlanCreate(title="Проверить", sku="Кампания 34346526"))
    assert created["status"] == "created"
    api._execute_action_plan_item(int(created["id"]))
    with sqlite3.connect(api.DB_PATH) as conn:
        row = conn.execute("SELECT status,assignee,executor,result,started_at,completed_at,history FROM redesign_action_plan WHERE id=?", (created["id"],)).fetchone()
    assert row[0] == "done"
    assert row[1] == "AI-исполнитель"
    assert row[2] == "AI-исполнитель"
    assert "34346526" in row[3]
    assert row[4]
    assert row[5]
    assert '"status": "done"' in row[6]


def test_unknown_campaign_is_marked_blocked_with_explanation(monkeypatch, tmp_path):
    _use_plan_db(monkeypatch, tmp_path)
    monkeypatch.setattr(api, "_inspect_campaign_for_plan", lambda campaign_id: None)
    created = api.create_action_plan_item(api.ActionPlanCreate(title="Проверить", sku="Кампания 34346526"))
    assert created["status"] == "created"
    api._execute_action_plan_item(int(created["id"]))
    with sqlite3.connect(api.DB_PATH) as conn:
        row = conn.execute("SELECT status,result FROM redesign_action_plan WHERE id=?", (created["id"],)).fetchone()
    assert row[0] == "blocked"
    assert "34346526" in row[1]
    assert "не найдена" in row[1]


def test_same_source_action_returns_existing_task_instead_of_duplicate(monkeypatch, tmp_path):
    _use_plan_db(monkeypatch, tmp_path)
    monkeypatch.setattr(api, "_inspect_campaign_for_plan", lambda campaign_id: {
        "campaign_id": campaign_id, "spend": "1 200 ₽", "drr": "5,2%", "revenue": "23 000 ₽",
    })
    payload = api.ActionPlanCreate(
        source_action_id="Проверить:Кампания 34346526",
        title="Проверить",
        sku="Кампания 34346526",
    )

    first = api.create_action_plan_item(payload)
    second = api.create_action_plan_item(payload)

    with sqlite3.connect(api.DB_PATH) as conn:
        count = conn.execute("SELECT COUNT(*) FROM redesign_action_plan").fetchone()[0]
    assert first["id"] == second["id"]
    assert second["status"] == "created"
    assert count == 1


def test_legacy_fifo_placeholder_is_removed_from_open_queue(monkeypatch, tmp_path):
    path = _use_plan_db(monkeypatch, tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """INSERT INTO redesign_action_plan
               (source, source_action_id, title, sku, status, priority, history, created_at, updated_at)
               VALUES ('dashboard', 'Задача:SKU', 'Задача', 'SKU', 'blocked', 'medium', '[]', '2026-09-10T00:00:00', '2026-09-10T00:00:00')"""
        )
    api._init_redesign_schema()
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT status,result FROM redesign_action_plan").fetchone()
    assert row[0] == "done"
    assert "FIFO" in row[1]
