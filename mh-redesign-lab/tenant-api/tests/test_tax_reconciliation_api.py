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
