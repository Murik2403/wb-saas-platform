import sys
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
API_DIR=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(API_DIR));sys.path.insert(0,str(API_DIR.parents[1]/"tenant-app"))
import db.core as core
import ozon_accounting_api as api


@pytest.fixture
def env(monkeypatch,tmp_path):
    monkeypatch.setattr(core,"DB_PATH",tmp_path/"ozon.sqlite")
    core.init_db()
    monkeypatch.setattr(api,"get_ozon_credentials",lambda:("not-real","not-real"))
    app=FastAPI();app.include_router(api.router)
    with TestClient(app) as client:yield client


def test_workspace_without_snapshot_does_not_show_zero_profit(env):
    result=env.get("/api/v1/ozon-accounting/workspace?start_date=2026-09-01&end_date=2026-09-02")
    assert result.status_code==200
    assert result.json()["net_accrual_rub"] is None
    assert result.json()["profit_rub"] is None


@pytest.mark.parametrize("role",["viewer","warehouse","technologist","integration_reader",""])
def test_non_manager_cannot_save_cost_or_trigger_sync(env,role):
    headers={"x-tenant-role":role}
    assert env.put("/api/v1/ozon-accounting/cost",headers=headers,json={"sku":"123","mode":"manual","unit_cost":"10"}).status_code==403
    assert env.post("/api/v1/ozon-accounting/sync",headers=headers,json={"start_date":"2026-09-01","end_date":"2026-09-02"}).status_code==403


def test_invalid_period_does_not_call_remote_client(env,monkeypatch):
    monkeypatch.setattr(api,"OzonAccountingClient",lambda *args:pytest.fail("remote request forbidden"))
    result=env.post("/api/v1/ozon-accounting/sync",headers={"x-tenant-role":"owner"},json={"start_date":"2026-08-01","end_date":"2026-09-30"})
    assert result.status_code==422


def test_remote_failure_does_not_create_partial_ledger(env,monkeypatch):
    class Failed:
        def __init__(self,*args):pass
        def snapshot(self,*args):raise ValueError("Ozon HTTP 429")
    monkeypatch.setattr(api,"OzonAccountingClient",Failed)
    assert env.post("/api/v1/ozon-accounting/sync",headers={"x-tenant-role":"owner"},json={"start_date":"2026-09-01","end_date":"2026-09-02"}).status_code==422
    result=env.get("/api/v1/ozon-accounting/workspace?start_date=2026-09-01&end_date=2026-09-02").json()
    assert not result["loaded"]


def test_owner_mapping_is_validated(env):
    headers={"x-tenant-role":"owner"}
    assert env.put("/api/v1/ozon-accounting/cost",headers=headers,json={"sku":"123","mode":"manual","unit_cost":"NaN"}).status_code==422
    assert env.put("/api/v1/ozon-accounting/cost",headers=headers,json={"sku":"123","mode":"manual","unit_cost":"12.50"}).status_code==200
