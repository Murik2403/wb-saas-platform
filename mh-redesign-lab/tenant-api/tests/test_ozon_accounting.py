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


def test_document_pnl_manual_import_replay_and_actor(env):
    import csv,io,uuid,json
    from db.ozon_pnl import COLUMNS
    out=io.StringIO();writer=csv.DictWriter(out,fieldnames=COLUMNS);writer.writeheader()
    writer.writerow(dict(external_event_id="sale",accrued_at="2026-09-02T12:00:00+03:00",movement_at="2026-09-02T11:00:00+03:00",kind="sale",sku="123",posting_ref="post",return_of="",quantity="1",revenue_rub="100",commission_rub="-10",logistics_rub="-5",other_rub="0",source_total_rub="85",currency="RUB",document_ref="report"))
    body={"namespace":"main","csv_text":out.getvalue()};headers={"x-tenant-role":"owner","x-tenant-account":"verified-owner"}
    assert env.post("/api/v1/ozon-accounting/movements/preview",json=body,headers={"x-tenant-role":"viewer"}).status_code==403
    preview=env.post("/api/v1/ozon-accounting/movements/preview",json=body,headers=headers).json()
    body["expected_fingerprint"]=preview["fingerprint"]
    assert env.post("/api/v1/ozon-accounting/movements/confirm",json=body,headers=headers).json()["imported"]==1
    assert env.post("/api/v1/ozon-accounting/movements/confirm",json=body,headers=headers).json()["imported"]==0
    path="/api/v1/ozon-accounting/pnl?start_date=2026-09-01&end_date=2026-09-03"
    assert env.get(path).json()["result_after_fifo_rub"] is None
    lot=dict(namespace="main",external_lot_id="receipt-line",sku="123",received_at="2026-09-01T00:00:00+03:00",quantity_units=1,unit_cost_rub="10",document_ref="receipt",reason="document",request_key=str(uuid.uuid4()))
    assert env.post("/api/v1/ozon-accounting/cost-lots",json=lot,headers=headers).status_code==200
    changed={**lot,"external_lot_id":"another"}
    assert env.post("/api/v1/ozon-accounting/cost-lots",json=changed,headers=headers).status_code==422
    assert env.get(path).json()["result_after_fifo_rub"]=="75.00"
    with core.connect() as conn:assert conn.execute("SELECT actor FROM ozon_document_events").fetchone()[0]=="verified-owner"


def test_document_invalid_preview_cannot_confirm_and_template_safe(env):
    body={"namespace":"main","csv_text":"wrong,columns\n1,2","expected_fingerprint":"bad"};headers={"x-tenant-role":"manager"}
    assert env.post("/api/v1/ozon-accounting/movements/confirm",json=body,headers=headers).status_code==422
    with core.connect() as conn:assert conn.execute("SELECT COUNT(*) FROM ozon_document_events").fetchone()[0]==0
    r=env.get("/api/v1/ozon-accounting/movements/template");assert r.headers["x-content-type-options"]=="nosniff";assert "attachment" in r.headers["content-disposition"]


def test_csv_oversized_single_field_is_validation_error(env):
    from db.ozon_pnl import COLUMNS
    body={"namespace":"main","csv_text":",".join(COLUMNS)+"\n"+"x"*140000}
    assert env.post("/api/v1/ozon-accounting/movements/preview",json=body,headers={"x-tenant-role":"owner"}).status_code==422


def test_real_tenant_middleware_blocks_foreign_origin_demo_and_read_roles(monkeypatch,tmp_path):
    from test_management_economics import api as application
    monkeypatch.setattr(core,"DB_PATH",tmp_path/"private.sqlite");core.init_db()
    monkeypatch.setattr(application,"IS_DEMO",False)
    payload={"namespace":"main","csv_text":"invalid"}
    with TestClient(application.app,base_url="http://127.0.0.1") as client:
        url="/api/v1/ozon-accounting/movements/preview"
        for role in ["viewer","warehouse","production"]:
            assert client.post(url,json=payload,headers={"x-tenant-role":role}).status_code==403
        assert client.post(url,json=payload,headers={"x-tenant-role":"owner","origin":"https://evil.example"}).status_code==403
        monkeypatch.setattr(application,"IS_DEMO",True)
        assert client.post(url,json=payload,headers={"x-tenant-role":"owner"}).status_code==403
