"""Real HTTP validation -> manufacturing store -> isolated SQLite, no WB or jobs."""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR.parents[1] / "tenant-app"))
sys.path.insert(0, str(API_DIR))
import db.core as core
from db.fifo_materials import _create_material_layer
from manufacturing_costs_api import router

PREFIX = "/api/v1/manufacturing-costs"


def test_demo_has_no_mutation_actions_even_with_owner_header(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "IS_DEMO", True)
    response = client.get(PREFIX, headers={"x-tenant-role": "owner"})
    assert response.status_code == 200
    assert response.json()["allowed_actions"] == []


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "DB_PATH", tmp_path / "manufacturing.sqlite3")
    core.init_db()
    with core.connect() as conn:
        conn.execute("INSERT INTO products_catalog(nm_id,product_name,raw_json) VALUES (123,'Салфетки','{\"sizes\":[]}')")
        conn.execute("INSERT INTO material_inventory_color(material_key,material_name,balance_known,full_rolls,partial_meters,roll_length,note,updated_at,unit,tracking_mode) VALUES ('ткань','Ткань',1,0,20,1,'','2026-09-23','м²','quantity')")
        _create_material_layer(conn, "ткань", "Ткань", "opening", "test-layer", "2026-09-01", 20, 10.5)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as result:
        yield result


def post(client, path, payload):
    response = client.post(PREFIX + path, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def bom_payload(**changes):
    return {"nm_id": 123, "materials": [{"material_name": "Ткань", "quantity_per_unit": 2, "unit": "м²"}], "operations": [], "packaging_per_unit": 1.25, "other_per_unit": 0, "note": "Тестовая версия", **changes}


def test_complete_http_cost_flow_and_readback(client):
    asset = post(client, "/equipment", {"name": "Швейная машина", "acquisition_cost_rub": 1000, "salvage_value_rub": 100, "resource_quantity": 90, "resource_unit": "hours"})
    bom = post(client, "/boms", bom_payload(operations=[{"name": "Пошив", "rate_per_unit": 3, "equipment_id": asset["id"], "equipment_usage_per_unit": 0.5}]))
    labor = {"source_key": "job-1", "nm_id": 123, "operation_name": "Пошив", "worker": "Мария", "quantity": 3, "rate_per_unit": 125.5, "work_date": "2026-09-23", "event_key": "labor-1"}
    piece = post(client, "/piecework", labor)
    assert post(client, "/piecework", labor)["id"] == piece["id"]
    release = {"nm_id": 123, "quantity": 3, "production_date": "2026-09-23", "source_key": "job-1", "bom_version_id": bom["id"], "supplier_article": "sal", "product_name": "Салфетки"}
    result = post(client, "/releases", release)
    assert result["material_cost_rub"] == 63
    assert result["labor_cost_rub"] == 376.5
    assert result["depreciation_cost_rub"] == 15
    assert result["packaging_cost_rub"] == 3.75
    assert result["total_cost_rub"] == 458.25
    assert result["unit_cost_rub"] == 152.75
    assert post(client, "/releases", release)["posted"] == 0
    workspace = client.get(PREFIX)
    assert workspace.status_code == 200, workspace.text
    data = workspace.json()
    assert set(data) >= {"products", "materials", "material_layers", "boms", "equipment", "piecework", "defects", "releases"}
    assert data["products"][0]["nm_id"] == 123
    assert data["materials"][0]["unit"] == "м²"
    assert data["material_layers"][0]["remaining_quantity"] == 14
    assert data["equipment"][0]["consumed_resource"] == 1.5
    assert len(data["piecework"]) == len(data["releases"]) == 1
    assert data["piecework"][0]["release_movement_id"] == result["movement_id"]
    late = client.post(PREFIX + "/piecework", json={**labor, "event_key": "late"})
    assert late.status_code == 422
    assert "Выпуск уже проведён" in late.json()["detail"]


def test_defect_exact_layer_idempotency_and_overdraft_rollback(client):
    layer = client.get(PREFIX).json()["material_layers"][0]
    body = {"layer_id": layer["id"], "quantity": 1.5, "event_key": "scrap-1", "reason": "Повреждение ткани", "movement_date": "2026-09-23"}
    result = post(client, "/defects", body)
    assert result["cost_rub"] == 15.75
    assert post(client, "/defects", body)["id"] == result["id"]
    response = client.post(PREFIX + "/defects", json={**body, "quantity": 25, "event_key": "scrap-2"})
    assert response.status_code == 422
    workspace = client.get(PREFIX).json()
    assert workspace["material_layers"][0]["remaining_quantity"] == 18.5
    assert workspace["materials"][0]["quantity"] == 18.5
    assert len(workspace["defects"]) == 1


@pytest.mark.parametrize("patch", [
    {"nm_id": 0}, {"materials": []}, {"packaging_per_unit": -1},
    {"materials": [{"material_name": "Ткань", "quantity_per_unit": 2, "unit": "кг"}]},
    {"materials": [{"material_name": "Ткань", "quantity_per_unit": 2, "unit": "м²"}] * 2},
    {"unexpected_field": True},
])
def test_invalid_bom_http_payload_does_not_create_version(client, patch):
    response = client.post(PREFIX + "/boms", json=bom_payload(**patch))
    assert response.status_code == 422, response.text
    assert client.get(PREFIX).json()["boms"] == []


@pytest.mark.parametrize("quantity", [0, -1, 1.5])
def test_invalid_release_quantity_does_not_move_stock(client, quantity):
    bom = post(client, "/boms", bom_payload())
    response = client.post(PREFIX + "/releases", json={"nm_id": 123, "quantity": quantity, "source_key": "bad-job", "production_date": "2026-09-23", "bom_version_id": bom["id"]})
    assert response.status_code == 422, response.text
    data = client.get(PREFIX).json()
    assert data["releases"] == []
    assert data["material_layers"][0]["remaining_quantity"] == 20


def test_bom_version_readback_preserves_previous_specification(client):
    first = post(client, "/boms", bom_payload())
    second = post(client, "/boms", bom_payload(materials=[{"material_name": "Ткань", "quantity_per_unit": 2.5, "unit": "м²"}]))
    assert first["version"] == 1 and second["version"] == 2
    rows = client.get(PREFIX).json()["boms"]
    assert next(row for row in rows if row["id"] == first["id"])["materials"][0]["quantity_per_unit"] == 2
    assert next(row for row in rows if row["id"] == second["id"])["materials"][0]["quantity_per_unit"] == 2.5


def test_typed_semifinished_component_bom_is_validated_and_read_back(client):
    with core.connect() as conn:
        conn.execute(
            "INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES (222,'BLANK','Заготовка','{\"sizes\":[]}')"
        )
    child = post(client, "/boms", {**bom_payload(), "nm_id": 222})
    parent = post(client, "/boms", bom_payload(materials=[{
        "component_type": "semi_finished", "nm_id": 222, "chrt_id": 0,
        "quantity_per_unit": 2, "unit": "шт",
    }]))
    assert child["id"] == parent["materials"][0]["component_bom_version_id"]
    assert parent["materials"][0] == {
        "component_type": "semi_finished", "nm_id": 222, "chrt_id": 0,
        "component_bom_version_id": child["id"], "quantity_per_unit": 2.0, "unit": "шт",
    }


def test_nested_bom_cycle_is_rejected_over_http(client):
    with core.connect() as conn:
        conn.execute(
            "INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES (222,'BLANK','Заготовка','{\"sizes\":[]}')"
        )
    post(client, "/boms", {**bom_payload(), "nm_id": 222})
    post(client, "/boms", bom_payload(materials=[{
        "component_type": "semi_finished", "nm_id": 222, "chrt_id": 0,
        "quantity_per_unit": 1, "unit": "шт",
    }]))
    response = client.post(PREFIX + "/boms", json={
        **bom_payload(nm_id=222), "materials": [{
            "component_type": "semi_finished", "nm_id": 123, "chrt_id": 0,
            "quantity_per_unit": 1, "unit": "шт",
        }],
    })
    assert response.status_code == 422
    assert "цикл" in response.json()["detail"]


def test_workspace_exposes_wb_size_variants_and_keeps_legacy_fallback(client):
    with core.connect() as conn:
        conn.execute("UPDATE products_catalog SET raw_json=? WHERE nm_id=123", ('{"sizes":[{"chrtID":501,"techSize":"S","skus":["111"]},{"chrtID":502,"techSize":"M","skus":["222"]}]}',))
    products = client.get(PREFIX).json()["products"]
    assert [(item["chrt_id"], item["variant_label"], item["barcodes"]) for item in products] == [(501, "S", "111"), (502, "M", "222")]


def test_variant_boms_and_release_keep_variant_identity(client):
    small = post(client, "/boms", bom_payload(chrt_id=501, note="Размер S"))
    medium = post(client, "/boms", bom_payload(chrt_id=502, note="Размер M", operations=[{"name": "Пошив", "rate_per_unit": 3}]))
    legacy = post(client, "/boms", bom_payload(note="Общий состав"))
    assert (small["chrt_id"], small["version"]) == (501, 1)
    assert (medium["chrt_id"], medium["version"]) == (502, 1)
    assert legacy["chrt_id"] == 0
    post(client, "/piecework", {"source_key": "variant-job", "nm_id": 123, "chrt_id": 502, "operation_name": "Пошив", "worker": "Мария", "quantity": 1, "rate_per_unit": 10, "work_date": "2026-09-23", "event_key": "variant-labor"})
    release = post(client, "/releases", {"nm_id": 123, "chrt_id": 502, "quantity": 1, "production_date": "2026-09-23", "source_key": "variant-job", "bom_version_id": medium["id"]})
    assert release["chrt_id"] == 502
    assert release["labor_cost_rub"] == 10
    duplicate_other_variant = client.post(PREFIX + "/releases", json={"nm_id": 123, "chrt_id": 501, "quantity": 1, "production_date": "2026-09-23", "source_key": "variant-job", "bom_version_id": small["id"]})
    assert duplicate_other_variant.status_code == 422
    assert "другого варианта" in duplicate_other_variant.json()["detail"]
    mismatched = client.post(PREFIX + "/releases", json={"nm_id": 123, "chrt_id": 502, "quantity": 1, "production_date": "2026-09-23", "source_key": "bad-variant-job", "bom_version_id": small["id"]})
    assert mismatched.status_code == 422


def test_release_and_defect_reversal_restore_inventory_without_reusing_job_key(client):
    bom = post(client, "/boms", bom_payload())
    body = {"nm_id": 123, "quantity": 3, "production_date": "2026-09-23", "source_key": "reverse-job", "bom_version_id": bom["id"]}
    release = post(client, "/releases", body)
    assert post(client, f"/movements/{release['movement_id']}/reverse", {})["ok"] is True
    data = client.get(PREFIX).json()
    assert data["material_layers"][0]["remaining_quantity"] == 20
    assert data["releases"][0]["status"] == "reversed"
    assert client.post(PREFIX + "/releases", json=body).status_code == 422
    layer = data["material_layers"][0]
    defect = post(client, "/defects", {"layer_id": layer["id"], "quantity": 1, "event_key": "reverse-defect", "reason": "Брак", "movement_date": "2026-09-23"})
    assert post(client, f"/movements/{defect['movement_id']}/reverse", {})["ok"] is True
    result = client.get(PREFIX).json()
    assert result["materials"][0]["quantity"] == 20
    assert result["defects"][0]["status"] == "reversed"
    assert client.post(PREFIX + f"/movements/{defect['movement_id']}/reverse", json={}).status_code == 422
    assert client.post(PREFIX + "/movements/999999/reverse", json={}).status_code == 404


@pytest.mark.parametrize("role,actions", [
    ("owner", ["boms", "equipment", "piecework", "defects", "releases", "reverse"]),
    ("viewer", []), ("seamstress", ["piecework"]), ("technologist", ["boms"]),
    ("warehouse", ["defects", "releases"]), ("cutter", ["defects"]),
])
def test_aggregate_advertises_role_scoped_controls(client, role, actions):
    response = client.get(PREFIX, headers={"x-tenant-role": role})
    assert response.status_code == 200
    assert response.json()["allowed_actions"] == actions
