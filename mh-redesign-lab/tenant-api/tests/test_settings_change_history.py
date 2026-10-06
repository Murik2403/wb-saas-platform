from __future__ import annotations
from contextlib import closing
import importlib.util
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch
import pandas as pd
import pytest
from fastapi.testclient import TestClient
API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR.parents[1] / "tenant-app"))
sys.path.insert(0, str(API_DIR))
spec = importlib.util.spec_from_file_location("settings_history_test_api", API_DIR / "api_server.py")
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)

@pytest.fixture
def client(monkeypatch, tmp_path):
    import db.core as core
    database = tmp_path / "history.sqlite3"
    monkeypatch.setattr(core, "DB_PATH", database)
    monkeypatch.setattr(api, "DB_PATH", database)
    monkeypatch.setattr(api, "IS_DEMO", False)
    core.init_db()
    with closing(TestClient(api.app, base_url="http://127.0.0.1",headers={"x-tenant-role":"owner","x-tenant-account":"1"})) as client:
        yield client

def norm(nm=100, value=2):
    return {"nm_id": nm, "material_per_unit": value, "min_batch": 1, "pack_size": 4, "enabled": True}

def seed():
    from db.production import save_production_settings
    save_production_settings(pd.DataFrame([{**norm(), "target_days": 21}]))

def history(client, **params):
    response = client.get("/api/v1/settings/change-history", params=params)
    assert response.status_code == 200, response.text
    return response.json()

def test_cost_and_norm_changes_metadata_noop_and_pagination(client):
    seed()
    headers = {"x-tenant-account": "owner-account", "x-tenant-role": "owner"}
    cost = {"items": [{"nm_id": 100, "cost_per_wb_unit": 20}]}
    for _ in range(2):
        assert client.patch("/api/v1/settings/product-costs", json=cost, headers=headers).status_code == 200
    assert client.patch("/api/v1/settings/production-norms", json={"items": [norm(value=3)]}, headers=headers).status_code == 200
    assert client.patch("/api/v1/settings/production-norms", json={"items": [norm(value=3)]}, headers=headers).status_code == 200
    all_rows = history(client)
    assert all_rows["total"] == 3
    event = all_rows["items"][0]
    assert event["before"]["material_per_unit"] == 2
    assert event["after"]["material_per_unit"] == 3
    assert event["actor"] == "owner-account" and event["source"] == "tenant_api"
    costs = history(client, kind="cost", nm_id=100)
    assert costs["total"] == 1 and costs["items"][0]["before"] is None
    assert history(client, limit=1, offset=1)["items"][0]["id"] == all_rows["items"][1]["id"]
    assert history(client, nm_id=999)["items"] == []

@pytest.mark.parametrize("items", [[norm(), norm()], [norm(value=3), norm(999)], [norm(value=-2)]])
def test_invalid_norm_batch_changes_nothing(client, items):
    seed()
    assert client.patch("/api/v1/settings/production-norms", json={"items": items}).status_code == 422
    assert history(client)["total"] == 1
    assert client.get("/api/v1/settings/production-norms").json()["items"][0]["material_per_unit"] == 2

def test_norm_journal_failure_rolls_back(client):
    seed()
    with patch("db.settings_history.append_settings_change", side_effect=sqlite3.OperationalError("failure")):
        with pytest.raises(sqlite3.OperationalError):
            client.patch("/api/v1/settings/production-norms", json={"items": [norm(value=5)]})
    assert client.get("/api/v1/settings/production-norms").json()["items"][0]["material_per_unit"] == 2
    assert history(client)["total"] == 1

def test_viewer_can_read_cannot_write(client):
    seed()
    headers = {"x-tenant-account": "viewer", "x-tenant-role": "viewer"}
    assert client.get("/api/v1/settings/change-history", headers=headers).status_code == 200
    assert client.patch("/api/v1/settings/production-norms", json={"items": [norm(value=5)]}, headers=headers).status_code == 403
    assert client.patch("/api/v1/settings/product-costs", json={"items": [{"nm_id": 100, "cost_per_wb_unit": 7}]}, headers=headers).status_code == 403

@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"nm_id": 0}, {"kind": "token"}])
def test_history_filter_bounds(client, params):
    assert client.get("/api/v1/settings/change-history", params=params).status_code == 422


def test_second_norm_journal_failure_rolls_back_entire_batch(client):
    from db.production import save_production_settings
    seed()
    save_production_settings(pd.DataFrame([{**norm(200), "target_days": 21}]))
    import db.core as core
    with core.connect() as conn:
        conn.executescript("CREATE TRIGGER history_fail_norm BEFORE INSERT ON settings_change_history WHEN NEW.nm_id=200 BEGIN SELECT RAISE(ABORT, 'second event blocked'); END;")
    with pytest.raises(sqlite3.IntegrityError):
        client.patch("/api/v1/settings/production-norms", json={"items": [norm(value=5), norm(200, 6)]})
    rows = client.get("/api/v1/settings/production-norms").json()["items"]
    assert [r["material_per_unit"] for r in rows] == [2, 2]
    assert history(client)["total"] == 2


@pytest.mark.parametrize("path",["product-costs","production-norms"])
def test_reason_saved_with_actual_change_and_actor(client,path):
    seed()
    item={"nm_id":100,"cost_per_wb_unit":22} if path=="product-costs" else norm(value=3)
    payload={"items":[item],"reason":"  Уточнение по документу закупки  "}
    headers={"x-tenant-role":"owner","x-tenant-account":"42"}
    assert client.patch('/api/v1/settings/'+path,json=payload,headers=headers).status_code==200
    event=history(client)['items'][0]
    assert event['reason']=='Уточнение по документу закупки' and event['actor']=='42'
    before=history(client)['total']
    assert client.patch('/api/v1/settings/'+path,json={**payload,'reason':'Другой текст без изменения'},headers=headers).status_code==200
    assert history(client)['total']==before
    assert client.patch('/api/v1/settings/'+path,json={**payload,'reason':'x'*1001},headers=headers).status_code==422
    assert history(client)['total']==before


def test_legacy_changes_have_no_invented_reason(client):
    seed()
    assert history(client)['items'][0]['reason'] is None
