"""HTTP contracts exercised against a fresh real tenant SQLite ledger."""
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tenant-app"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db.core as core
from production_workspace_api import router
import production_workspace_api as workspace_api


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "DB_PATH", tmp_path / "workspace.sqlite")
    monkeypatch.setattr(workspace_api.config, "IS_DEMO", False)
    core.init_db()
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client


def test_read_only_reflects_demo_or_viewer_without_blocking_reads(client, monkeypatch):
    assert client.get("/api/v1/production-workspace").json()["read_only"] is False
    assert client.get("/api/v1/production-workspace", headers={"x-tenant-role": "viewer"}).json()["read_only"] is True
    monkeypatch.setattr(workspace_api.config, "IS_DEMO", True)
    result = client.get("/api/v1/production-workspace", headers={"x-tenant-role": "owner"})
    assert result.status_code == 200
    assert result.json()["read_only"] is True


def seed():
    from db.fifo_materials import _create_material_layer
    with core.connect() as conn:
        conn.execute("INSERT INTO material_inventory_color(material_key,material_name,balance_known,partial_meters,roll_length,opening_rate_rub) VALUES ('ткань','Ткань',1,20,25,100)")
        conn.execute("INSERT INTO production_settings(nm_id,supplier_article,enabled,material_name,blank_type,pack_size) VALUES (101,'QA',1,'Ткань','Круг',2)")
        _create_material_layer(conn, "ткань", "Ткань", "opening", "qa-opening", "2026-09-01", 20, 100)


def issue(client, **changes):
    payload = dict(request_id=str(uuid4()), batch_date="2026-09-23", material_name="Ткань", blank_type="Круг", meters_used=5, note="test")
    payload.update(changes)
    response = client.post("/api/v1/production-workspace/wip/issue", json=payload)
    return payload, response


def test_issue_close_package_and_block_consumed_batch_reversal(client):
    seed()
    # Packaging at 0 ₽ is allowed only after an explicit user confirmation.
    with core.connect() as conn:
        conn.execute(
            "INSERT INTO costs(nm_id,packaging_cost_rub,packaging_cost_status,packaging_cost_source,packaging_cost_confirmed_at) "
            "VALUES (101,0,'confirmed','manual','2026-09-23T12:00:00')"
        )
    payload, response = issue(client)
    assert response.status_code == 200, response.text
    batch = response.json()["batch_id"]
    assert client.post("/api/v1/production-workspace/wip/issue", json=payload).json()["skipped"] == 1
    assert client.post("/api/v1/production-workspace/wip/issue", json={**payload, "meters_used": 6}).status_code == 409
    closed = client.post(f"/api/v1/production-workspace/wip/{batch}/complete", json={"produced_units": 10, "scrap_units": 2})
    assert closed.status_code == 200, closed.text
    package = dict(request_id=str(uuid4()), nm_id=101, qty_sets=3, packaging_date="2026-09-23")
    receipt = client.post("/api/v1/production-workspace/wip/package", json=package)
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["wip_units"] == 6
    assert client.post("/api/v1/production-workspace/wip/package", json=package).json()["skipped"] == 1
    assert client.post(f"/api/v1/production-workspace/wip/{batch}/reverse", json={"stage": "completion"}).status_code == 409
    result = client.get("/api/v1/production-workspace").json()
    assert result["wip"]["batches"][0]["remaining_units"] == 4
    assert result["materials"][0]["available_meters"] == 15
    assert result["wip"]["batches"][0]["unit_cost_rub"] == 50


def test_completion_reversal_can_be_closed_again(client):
    seed()
    _, response = issue(client)
    batch = response.json()["batch_id"]
    url = f"/api/v1/production-workspace/wip/{batch}"
    assert client.post(url + "/complete", json={"produced_units": 10}).status_code == 200
    assert client.post(url + "/reverse", json={"stage": "completion"}).status_code == 200
    reclose = client.post(url + "/complete", json={"produced_units": 8})
    assert reclose.status_code == 200, reclose.text
    assert client.post(url + "/reverse", json={"stage": "completion"}).status_code == 200
    assert client.post(url + "/reverse", json={"stage": "issue"}).status_code == 200
    assert client.get("/api/v1/production-workspace").json()["materials"][0]["available_meters"] == 20
    with core.connect() as conn:
        receipts = conn.execute("SELECT event_key,reversed_at FROM inventory_movements WHERE movement_type='wip_blank_receipt' ORDER BY id").fetchall()
        assert len(receipts) == 2
        assert receipts[0]["event_key"].endswith("|v1")
        assert receipts[1]["event_key"].endswith("|v2")
        assert all(row["reversed_at"] for row in receipts)


@pytest.mark.parametrize("changes", [{"meters_used": -1}, {"meters_used": "NaN"}, {"material_name": " "}, {"batch_date": "bad"}, {"request_id": "bad"}])
def test_invalid_issue_does_not_change_stock(client, changes):
    seed()
    assert issue(client, **changes)[1].status_code == 422
    assert client.get("/api/v1/production-workspace").json()["materials"][0]["available_meters"] == 20


def test_insufficient_stock_and_unknown_material_are_rejected(client):
    seed()
    assert issue(client, meters_used=21)[1].status_code == 422
    assert issue(client, material_name="Нет в справочнике")[1].status_code == 422
    with core.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM inventory_movements").fetchone()[0] == 0


@pytest.mark.parametrize("unit", ["кг", "шт", "м²", "л"])
def test_non_meter_material_preserves_quantity_cost_and_unit_through_wip_and_undo(client, unit):
    seed()
    with core.connect() as conn:
        conn.execute("UPDATE material_inventory_color SET unit=?,tracking_mode='bulk'", (unit,))
    _, response = issue(client, meters_used=2.5)
    assert response.status_code == 200, response.text
    batch_id = response.json()["batch_id"]
    assert response.json()["cost_rub"] == 250
    view = client.get("/api/v1/production-workspace").json()
    assert view["materials"][0]["available_meters"] == 17.5
    assert view["materials"][0]["unit"] == unit
    assert view["wip"]["batches"][0]["unit"] == unit
    assert view["wip"]["summary"][0]["unit"] == unit
    assert view["wip"]["batches"][0]["issued_meters"] == 2.5
    url = f"/api/v1/production-workspace/wip/{batch_id}"
    assert client.post(url + "/complete", json={"produced_units": 5}).status_code == 200
    assert client.get("/api/v1/production-workspace").json()["wip"]["batches"][0]["unit_cost_rub"] == 50
    assert client.post(url + "/reverse", json={"stage": "completion"}).status_code == 200
    assert client.post(url + "/reverse", json={"stage": "issue"}).status_code == 200
    assert client.get("/api/v1/production-workspace").json()["materials"][0]["available_meters"] == 20
    with core.connect() as conn:
        note = conn.execute("SELECT note FROM inventory_movements WHERE movement_type='wip_material_issue'").fetchone()[0]
        assert f"2.500 {unit}" in note
        layer = conn.execute("SELECT remaining_meters,unit_cost_rub_m FROM material_cost_layers").fetchone()
        assert tuple(layer) == (20, 100)


def test_capacity_uses_real_kit_tasks_and_preserves_lead_settings(client):
    seed()
    payload = dict(capacity_known=True, pieces_per_day=10, workdays=[0, 1, 2, 3, 4], horizon_days=30,
                   fulfillment_lead_days=4, emergency_cover_days=6, expedited_fbo_lead_days=2, fbs_lead_days=1, note="shop")
    assert client.put("/api/v1/production-workspace/capacity", json=payload).status_code == 200
    with core.connect() as conn:
        conn.execute("INSERT INTO execution_tasks(task_key,task_type,task_date,nm_id,planned_units,actual_units,status) VALUES ('p','production','2026-09-23',101,8,3,'В работе')")
        conn.execute("INSERT INTO execution_tasks(task_key,task_type,task_date,nm_id,planned_units,status) VALUES ('d','dispatch','2026-09-23',101,100,'Не начато')")
    result = client.get("/api/v1/production-workspace?start=2026-09-23&days=1").json()
    assert result["capacity"]["fulfillment_lead_days"] == 4
    day = result["calendar"][0]
    assert (day["planned_pieces"], day["actual_pieces"], day["capacity_pieces"], day["overload_pieces"]) == (16, 6, 10, 6)
    assert client.put("/api/v1/production-workspace/capacity", json={**payload, "pieces_per_day": 0}).status_code == 422
    assert client.put("/api/v1/production-workspace/capacity", json={**payload, "workdays": []}).status_code == 422
    assert client.put("/api/v1/production-workspace/capacity", json={**payload, "workdays": [0, 0]}).status_code == 422
    assert client.put("/api/v1/production-workspace/capacity", json={**payload, "pieces_per_day": 1.5}).status_code == 422


def test_calendar_unknown_norm_and_missing_date_do_not_fabricate_capacity(client):
    with core.connect() as conn:
        conn.execute("INSERT INTO execution_tasks(task_key,task_type,task_date,nm_id,planned_units) VALUES ('unknown','production','2026-09-23',999,8)")
        conn.execute("INSERT INTO execution_tasks(task_key,task_type,nm_id,planned_units) VALUES ('undated','production',999,2)")
    result = client.get("/api/v1/production-workspace?start=2026-09-23&days=1").json()
    assert result["calendar"][0]["planned_pieces"] is None
    assert result["calendar"][0]["capacity_pieces"] is None
    assert result["calendar"][0]["overload_pieces"] is None
    assert result["calendar"][0]["missing_pack_size"]
    assert len(result["undated_tasks"]) == 1


def test_completion_rejects_fractional_and_negative_counts(client):
    seed()
    _, result = issue(client)
    url = f"/api/v1/production-workspace/wip/{result.json()['batch_id']}/complete"
    assert client.post(url, json={"produced_units": 1.5}).status_code == 422
    assert client.post(url, json={"produced_units": 3, "scrap_units": -1}).status_code == 422
    assert client.get("/api/v1/production-workspace").json()["wip"]["batches"][0]["status"] == "open"
