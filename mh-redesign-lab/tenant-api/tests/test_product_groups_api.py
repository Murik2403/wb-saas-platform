import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR.parents[1] / "tenant-app"))
sys.path.insert(0, str(API_DIR))
spec = importlib.util.spec_from_file_location("product_groups_test_api", API_DIR / "api_server.py")
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)
from db import core  # noqa: E402
from production_permissions import write_allowed  # noqa: E402

URL = "/api/v1/product-groups"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", tmp_path / "main.sqlite")
    monkeypatch.setattr(api, "IS_DEMO", False)
    return TestClient(api.app, base_url="http://127.0.0.1", headers={"x-tenant-role": "owner"})


def test_crud_and_dedup(client):
    assert client.get(URL).json() == {"groups": []}
    r = client.post(URL, json={"name": "  Скатерти  ", "nm_ids": [5, 3, 5]})
    assert r.status_code == 201
    g = r.json()
    assert g["name"] == "Скатерти" and g["nm_ids"] == [3, 5] and g["count"] == 2 and g["archived"] is False
    r = client.put(f"{URL}/{g['id']}", json={"name": "Новое", "nm_ids": [9]})
    assert r.json()["name"] == "Новое" and r.json()["nm_ids"] == [9]
    assert client.get(URL).json()["groups"][0]["count"] == 1


def test_name_unique_case_insensitive_cyrillic(client):
    client.post(URL, json={"name": "Скатерти"})
    r = client.post(URL, json={"name": "скАТЕРТИ"})
    assert r.status_code == 409 and "уже есть" in r.json()["detail"]
    other = client.post(URL, json={"name": "Другая"}).json()
    assert client.put(f"{URL}/{other['id']}", json={"name": "СКАТЕРТИ"}).status_code == 409


def test_validation(client):
    for bad in ({"name": ""}, {"name": "   "}, {"name": "x" * 61}, {"name": "a", "nm_ids": [-1]}, {"name": "a", "nm_ids": [0]}, {"name": "a", "nm_ids": ["7"]}):
        assert client.post(URL, json=bad).status_code == 422, bad
    assert client.post(URL, json={"name": "x" * 60}).status_code == 201
    assert client.put(f"{URL}/1", json={}).status_code == 422
    assert client.put(f"{URL}/999", json={"name": "q"}).status_code == 404


def test_limits(client, monkeypatch):
    monkeypatch.setattr(sys.modules["product_groups_api"], "MAX_GROUPS", 2)
    assert client.post(URL, json={"name": "a"}).status_code == 201
    assert client.post(URL, json={"name": "b"}).status_code == 201
    assert client.post(URL, json={"name": "c"}).status_code == 422
    assert client.post(URL, json={"name": "m", "nm_ids": list(range(1, 2002))}).status_code == 422


def test_archive_hides_keeps_members_and_frees_name(client):
    g = client.post(URL, json={"name": "Зима", "nm_ids": [1, 2]}).json()
    r = client.delete(f"{URL}/{g['id']}")
    assert r.status_code == 200 and r.json()["archived"] is True
    assert client.get(URL).json()["groups"] == []
    archived = client.get(URL, params={"include_archived": True}).json()["groups"]
    assert archived[0]["nm_ids"] == [1, 2]
    assert client.post(URL, json={"name": "зима"}).status_code == 201  # name is free again
    assert client.put(f"{URL}/{g['id']}", json={"archived": False}).status_code == 409  # restore collides


def test_permissions():
    for role in ("owner", "manager"):
        for m in ("POST", "PUT", "DELETE"):
            assert write_allowed(role, m, URL + "/1") == (role == "owner" or m != "DELETE")
    for role in ("viewer", "technologist", "cutter", "seamstress", "warehouse", "integration_reader"):
        for m in ("POST", "PUT", "DELETE"):
            assert not write_allowed(role, m, URL + "/1")


def test_http_roles(client):
    assert client.post(URL, json={"name": "a"}, headers={"x-tenant-role": "viewer"}).status_code == 403
    assert client.post(URL, json={"name": "a"}, headers={"x-tenant-role": "cutter"}).status_code == 403
    assert client.post(URL, json={"name": "a"}, headers={"x-tenant-role": "manager"}).status_code == 201
    assert client.get(URL, headers={"x-tenant-role": "viewer"}).status_code == 200
