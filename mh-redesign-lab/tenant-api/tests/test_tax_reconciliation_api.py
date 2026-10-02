import importlib.util
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR.parents[1] / "tenant-app"))
sys.path.insert(0, str(API_DIR))
spec = importlib.util.spec_from_file_location("tax_reconciliation_test_api", API_DIR / "api_server.py")
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)
from db import core

URL = "/api/v1/settings/tax-reconciliation"

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", tmp_path / "main.sqlite")
    monkeypatch.setattr(api, "IS_DEMO", False)
    return TestClient(api.app, base_url="http://127.0.0.1", headers={"x-tenant-role": "owner", "x-tenant-account": "owner"})

def payload():
    return dict(month="2026-09", revision=0, source="bank", confirmed=True, received=100, offset=0, compensation=0, other_income=0, refunds=0)

def test_empty_save_conflict_and_gate_stays_off(client):
    assert client.get(URL, params={"month": "2026-09"}).json()["revision"] == 0
    assert client.put(URL, json=payload()).json()["calculation"]["estimate"] == 8
    changed = payload(); changed["received"] = 200
    assert client.put(URL, json=changed).status_code == 409
    assert client.put(URL, json=payload()).json()["revision"] == 1
    assert client.get('/api/v1/settings/tax?year=2026').json()['enabled'] is False

@pytest.mark.parametrize("role", ["viewer", "warehouse", "technologist", "cutter", "seamstress", "integration_reader"])
def test_roles_cannot_write(client, role):
    assert client.put(URL, json=payload(), headers={"x-tenant-role": role}).status_code == 403

def test_bad_month_money_and_confirmation(client):
    assert client.get(URL, params={"month": "2026-13"}).status_code == 422
    for key, value in [("received", -1), ("received", .001), ("confirmed", False), ("source", " ")]:
        data = payload(); data[key] = value
        assert client.put(URL, json=data).status_code == 422

def test_routes_are_shop_local():
    import shop_context
    for method in ['GET', 'PUT']:
        assert shop_context.route_scope(method, URL)['scope'] == 'shop_local'

@pytest.mark.parametrize("regime,expected",[("ausn_income",8000),("ausn_income_expense",3000),("usn_income",6000),("usn_income_expense",1500),("osno_ip",1300),("osno_company",2500)])
def test_all_regimes_roundtrip_and_history(client,regime,expected):
    body=payload();body.update(regime=regime,received=100000,expenses=90000,loss=0,insurance=0,other_ndfl_base=0,
        period_mode="month" if regime.startswith("ausn") else "ytd",vat_mode="exempt",advances=0)
    saved=client.put(URL,json=body);assert saved.status_code==200,saved.text
    assert saved.json()["calculation"]["estimate"]==expected
    loaded=client.get(URL,params={"month":"2026-09"}).json()
    assert loaded["values"]["regime"]==regime
    assert loaded["period"]["start"]==("2026-09-01" if regime.startswith("ausn") else "2026-01-01")
    assert loaded["history"][0]["values"]["regime"]==regime
    assert client.put(URL,json=body).json()["revision"]==1

@pytest.mark.parametrize("changes",[dict(regime="usn_income",period_mode="month"),dict(regime="ausn_income",rate=6),
    dict(regime="osno_ip",rate=13),dict(regime="usn_income",vat_mode="special5",vat_input=1),
    dict(regime="usn_income_expense",loss=1),dict(regime="ausn_income",insurance=1)])
def test_invalid_regime_combinations_do_not_write(client,changes):
    body=payload();body.update(changes)
    assert client.put(URL,json=body).status_code==422
    assert client.get(URL,params={"month":"2026-09"}).json()["revision"]==0

def test_vat_and_minimum_and_advance_reconciliation(client):
    body=payload();body.update(month="2026-12",regime="usn_income_expense",received=100000,expenses=100000,loss=0,
        vat_mode="general22",vat_output=22000,vat_input=20000,advances=800,profit_before_tax=5000,fns_assessed=3000,paid=2900)
    saved=client.put(URL,json=body);assert saved.status_code==200,saved.text
    c=saved.json()["calculation"]
    assert c["income_tax"]==1000 and c["vat_to_pay"]==2000 and c["estimate"]==3000
    assert c["income_tax_to_pay"]==200 and c["after_estimate"]==2000 and c["assessed_minus_paid"]==100
