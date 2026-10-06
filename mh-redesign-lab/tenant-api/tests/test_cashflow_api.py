from contextlib import closing
import importlib.util
from pathlib import Path
import sys
import pytest
from fastapi.testclient import TestClient
API_DIR=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(API_DIR.parents[1]/"tenant-app"))
sys.path.insert(0,str(API_DIR))
spec=importlib.util.spec_from_file_location("cashflow_test_api",API_DIR/"api_server.py")
api=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=api
spec.loader.exec_module(api)

@pytest.fixture
def client(monkeypatch,tmp_path):
    from db import core
    monkeypatch.setattr(core,"DB_PATH",tmp_path/"cash.db")
    monkeypatch.setattr(api,"DB_PATH",tmp_path/"cash.db")
    monkeypatch.setattr(api,"IS_DEMO",False)
    core.init_db()
    with closing(TestClient(api.app,base_url="http://127.0.0.1",headers={"x-tenant-role":"owner","x-tenant-account":"1"})) as value:yield value

def payload(**kwargs):
    return {"request_key":"request-001","direction":"outflow","status":"planned","planned_date":"2026-09-10","amount":"37.5","currency":"CNY","exchange_rate":"13",**kwargs}

def test_create_confirm_and_report(client):
    response=client.post("/api/v1/cashflow/entries",json=payload(),headers={"x-tenant-account":"owner"})
    assert response.status_code==200,response.text
    assert response.json()["amount_minor"]==48750
    row_id=response.json()["id"]
    assert client.post("/api/v1/cashflow/entries",json=payload()).json()["id"]==row_id
    assert client.patch(f"/api/v1/cashflow/entries/{row_id}",json={"status":"actual","actual_date":"2026-09-12"}).status_code==200
    report=client.get("/api/v1/cashflow?start=2026-09-01&end=2026-09-19").json()
    assert report["actual_closing_minor"]==report["forecast_closing_minor"]==-48750
    assert report["coverage"]=="manual_entries_only" and not report["procurement_auto_included"]

@pytest.mark.parametrize("role",["viewer","warehouse","technologist","cutter","seamstress"])
def test_read_roles_cannot_write(client,role):
    headers={"x-tenant-role":role}
    assert client.get("/api/v1/cashflow?start=2026-09-01&end=2026-09-19",headers=headers).status_code==200
    assert client.post("/api/v1/cashflow/entries",json=payload(),headers=headers).status_code==403

@pytest.mark.parametrize("changes",[{"amount":"NaN"},{"exchange_rate":"0"},{"currency":"XXX"},{"direction":"hack"},{"planned_date":"bad"},{"actual_date":"2026-09-10"},{"unexpected":1}])
def test_invalid_inputs(client,changes):
    assert client.post("/api/v1/cashflow/entries",json=payload(**changes)).status_code==422

def test_write_origin_protected_and_dates_checked(client):
    assert client.post("/api/v1/cashflow/entries",json=payload(),headers={"origin":"https://evil.test"}).status_code==403
    assert client.get("/api/v1/cashflow?start=2026-09-19&end=2026-09-01").status_code==422
    assert client.patch("/api/v1/cashflow/entries/999",json={"status":"cancelled"}).status_code==404
