"""HTTP contract tests for read-only preview and explicit internal confirmation."""
import sys
from contextlib import closing
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR.parents[1] / "tenant-app"))
sys.path.insert(0, str(API_DIR))
import db.core as core
from production_permissions import write_allowed
from supply_planning_api import router


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "DB_PATH", tmp_path / "supply-planning.sqlite3")
    core.init_db()
    with core.connect() as conn:
        conn.execute("INSERT INTO execution_tasks(task_key,task_type,task_date,nm_id,chrt_id,planned_units,actual_units,status,updated_at) VALUES ('task:one','production','2026-09-12',10,0,5,0,'Не начато','2026-09-01')")
        conn.execute("INSERT INTO manufacturing_boms(nm_id,chrt_id,version,materials_json,operations_json,packaging_per_unit,other_per_unit,note,created_at) VALUES (10,0,1,?, '[]',0,0,'','2026-09-01')", ('[{"material_key":"fabric-a","material_name":"Ткань A","quantity_per_unit":2,"unit":"м"}]',))
        conn.execute("INSERT INTO material_inventory_color(material_key,material_name,balance_known,full_rolls,partial_meters,roll_length,unit,tracking_mode,updated_at) VALUES ('fabric-a','Ткань A',1,0,0,10,'м','packaged','2026-09-01')")
        conn.execute("INSERT INTO suppliers(name,default_currency,lead_time_days,active,created_at,updated_at) VALUES ('Supplier','CNY',4,1,'2026-09-01','2026-09-01')")
    app = FastAPI()
    app.include_router(router)
    with closing(TestClient(app, base_url="http://127.0.0.1")) as client:
        yield client


def preview_body():
    return {"start_date": "2026-09-01", "end_date": "2026-09-30", "variant_allocations": []}


def test_preview_is_read_only_and_uses_remaining_task_units(env):
    with core.connect() as conn:
        conn.execute("UPDATE execution_tasks SET actual_units=2 WHERE task_key='task:one'")
    with core.connect() as conn:
        before = tuple(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("procurement_orders", "procurement_items", "inventory_movements", "execution_tasks"))
    response = env.post("/api/v1/supply-planning/preview", json=preview_body())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["lines"][0]["required_base_qty"] == 6
    with core.connect() as conn:
        after = tuple(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("procurement_orders", "procurement_items", "inventory_movements", "execution_tasks"))
    assert before == after


def test_confirm_requires_preview_fingerprint_and_replays_idempotently(env):
    preview = env.post("/api/v1/supply-planning/preview", json=preview_body()).json()
    body = {**preview_body(), "confirmation_key": "00000000-0000-4000-8000-000000000001", "preview_fingerprint": preview["preview_fingerprint"],
            "edits": [{"material_key": "fabric-a", "unit": "м", "quantity": 1, "supplier_id": 1, "expected_date": "2026-09-11"}]}
    first = env.post("/api/v1/supply-planning/confirm", json=body)
    assert first.status_code == 200, first.text
    second = env.post("/api/v1/supply-planning/confirm", json=body)
    assert second.status_code == 200, second.text
    assert first.json()["order_ids"] == second.json()["order_ids"]
    assert second.json()["replayed"] is True
    with core.connect() as conn:
        order = conn.execute("SELECT status,currency FROM procurement_orders").fetchone()
        item = conn.execute("SELECT quantity,unit,roll_length FROM procurement_items").fetchone()
        assert tuple(order) == ("Запланировано", "CNY")
        assert tuple(item) == (1.0, "рулон", 10.0)
        assert conn.execute("SELECT COUNT(*) FROM procurement_orders").fetchone()[0] == 1


def test_stale_confirm_and_changed_idempotency_body_write_nothing(env):
    preview = env.post("/api/v1/supply-planning/preview", json=preview_body()).json()
    with core.connect() as conn:
        conn.execute("UPDATE manufacturing_boms SET materials_json=? WHERE id=1", ('[{"material_key":"fabric-a","material_name":"Ткань A","quantity_per_unit":3,"unit":"м"}]',))
    body = {**preview_body(), "confirmation_key": "00000000-0000-4000-8000-000000000002", "preview_fingerprint": preview["preview_fingerprint"],
            "edits": [{"material_key": "fabric-a", "unit": "м", "quantity": 2, "supplier_id": 1, "expected_date": "2026-09-11"}]}
    stale = env.post("/api/v1/supply-planning/confirm", json=body)
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_preview"
    with core.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM procurement_orders").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM procurement_items").fetchone()[0] == 0


def test_reused_key_with_changed_edits_returns_idempotency_conflict(env):
    preview = env.post("/api/v1/supply-planning/preview", json=preview_body()).json()
    body = {**preview_body(), "confirmation_key": "00000000-0000-4000-8000-000000000004", "preview_fingerprint": preview["preview_fingerprint"],
            "edits": [{"material_key": "fabric-a", "unit": "м", "quantity": 1, "supplier_id": 1, "expected_date": "2026-09-11"}]}
    assert env.post("/api/v1/supply-planning/confirm", json=body).status_code == 200
    body["edits"][0]["quantity"] = 2
    conflict = env.post("/api/v1/supply-planning/confirm", json=body)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"
    with core.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM procurement_orders").fetchone()[0] == 1


def test_confirm_does_not_allow_client_to_change_material_or_currency(env):
    preview = env.post("/api/v1/supply-planning/preview", json=preview_body()).json()
    body = {**preview_body(), "confirmation_key": "00000000-0000-4000-8000-000000000003", "preview_fingerprint": preview["preview_fingerprint"],
            "currency": "USD", "edits": [{"material_key": "different-material", "unit": "м", "quantity": 1, "supplier_id": 1, "expected_date": "2026-09-11"}]}
    response = env.post("/api/v1/supply-planning/confirm", json=body)
    assert response.status_code == 422
    with core.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM procurement_orders").fetchone()[0] == 0


@pytest.mark.parametrize("role,confirm", [("owner", True), ("manager", True), ("viewer", False), ("warehouse", False), ("integration_reader", False)])
def test_planning_permissions_allow_read_preview_but_owner_manager_only_confirm(role, confirm):
    assert write_allowed(role, "POST", "/api/v1/supply-planning/preview") is (role != "integration_reader")
    assert write_allowed(role, "POST", "/api/v1/supply-planning/confirm") is confirm
