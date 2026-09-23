"""The real tenant middleware restricts integration_reader before routing."""
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from test_display_contract import api
import db.core as core


@pytest.fixture
def client(monkeypatch, tmp_path):
    database = tmp_path / "reader.sqlite3"
    monkeypatch.setattr(core, "DB_PATH", database)
    monkeypatch.setattr(api, "DB_PATH", database)
    monkeypatch.setattr(api, "IS_DEMO", False)
    core.init_db()
    with closing(TestClient(api.app, base_url="http://127.0.0.1", headers={"x-tenant-role": "integration_reader", "x-tenant-account": "integration"})) as client:
        yield client


@pytest.mark.parametrize("endpoint", ["products", "materials", "releases"])
def test_allowed_read_contract_and_pagination(client, endpoint):
    result = client.get(f"/api/v1/integration/{endpoint}?limit=1&offset=0")
    assert result.status_code == 200, result.text
    assert result.json() == {"items": [], "offset": 0, "limit": 1}
    assert client.get(f"/api/v1/integration/{endpoint}?limit=501").status_code == 422
    assert client.get(f"/api/v1/integration/{endpoint}?offset=-1").status_code == 422


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/v1/production-workspace"), ("GET", "/api/v1/session"),
    ("GET", "/api/v1/settings"), ("GET", "/api/v1/manufacturing-costs"),
    ("GET", "/openapi.json"), ("GET", "/api/v1/operations/reports/pdf"),
    ("POST", "/api/v1/integration/products"), ("POST", "/api/v1/production-workspace/wip/issue"),
    ("PUT", "/api/v1/production-workspace/capacity"), ("HEAD", "/api/v1/integration/products"),
    ("GET", "/api/v1/integration/products/"), ("DELETE", "/api/v1/integration/releases"),
])
def test_reader_cannot_access_broader_routes_or_any_write(client, method, path):
    assert client.request(method, path).status_code == 403


def test_products_projection_excludes_costs_and_obeys_page_offset(client):
    with core.connect() as conn:
        conn.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES (1,'A','First','{}'),(2,'B','Second','{}')")
    response = client.get("/api/v1/integration/products?limit=1&offset=1")
    assert response.status_code == 200
    assert response.json()["items"] == [{"nm_id": 2, "supplier_article": "B", "product_name": "Second"}]


def test_unknown_role_cannot_escalate_to_default_owner(client):
    assert client.get("/api/v1/integration/products", headers={"x-tenant-role": "integration_reader,owner"}).status_code == 403
