import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tenant-app"))
import marketplace_context_api as api
from db import core, shared_warehouse


def headers(role="owner", account="owner-id", marketplace="ozon", **extra):
    return {"x-tenant-role": role, "x-tenant-account": account, "x-mh-marketplace": marketplace, **extra}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "DB_PATH", tmp_path / "test.sqlite3")
    core.init_db()
    monkeypatch.setattr(api.shop_context, "authorize", Mock(return_value=None))
    monkeypatch.setattr(api, "IS_DEMO", False)
    app = FastAPI(); app.include_router(api.router)
    reached = Mock()
    @app.middleware("http")
    async def guard(request, call_next):
        failure = await api.marketplace_guard(request)
        if failure is not None:
            return failure
        result = await call_next(request)
        if request.headers.get("x-mh-marketplace") in {"wb", "ozon"}:
            result.headers["X-MH-Marketplace"] = request.headers["x-mh-marketplace"]
        return result
    @app.get("/api/v1/dashboard/finance")
    @app.get("/api/v1/ozon-accounting/workspace")
    @app.get("/api/v1/ozon-insights/products")
    @app.get("/api/v1/ozon-prices")
    @app.get("/api/v1/cashflow")
    async def read():
        reached(); return {"loaded": False}
    @app.post("/api/v1/ozon-accounting/test-body")
    @app.post("/api/v1/cashflow")
    async def body(request: Request):
        reached(); return await request.json()
    with TestClient(app) as result:
        result.reached = reached
        yield result


def test_wb_explicit_context_rejects_ozon_query_and_body_before_handler(client):
    assert client.get("/api/v1/dashboard/finance?marketplace=ozon", headers=headers(marketplace="wb")).status_code == 400
    assert client.post("/api/v1/cashflow", headers=headers(marketplace="wb"), json={"marketplace": "ozon"}).status_code == 409
    client.reached.assert_not_called()
    payload = {"marketplace": "wb", "amount": 123}
    assert client.post("/api/v1/cashflow", headers=headers(marketplace="wb"), json=payload).json() == payload


def test_stale_wb_snapshot_revision_cannot_be_reused_for_primary_ozon(client):
    assert client.get("/api/v1/ozon-prices", headers=headers(**{"x-mh-shop-revision": "4"})).status_code == 409
    client.reached.assert_not_called()


@pytest.mark.parametrize("marketplace,path", [("wb", "/api/v1/cashflow"), ("ozon", "/api/v1/ozon-accounting/test-body")])
def test_explicit_marketplace_json_body_is_bounded_and_invalid_body_fails_closed(client, marketplace, path):
    selected = headers(marketplace=marketplace)
    assert client.post(path, headers={**selected, "content-type": "application/json"}, content="{").status_code == 400
    assert client.post(path, headers={**selected, "content-type": "application/json"}, content='{"large":"' + "x" * (2 * 1024 * 1024) + '"}').status_code == 413
    client.reached.assert_not_called()


def grant(client, account="member", can_write=True, active=True):
    response = client.put("/api/v1/marketplace-context/ozon/access", headers=headers(), json={"account": account, "can_write": can_write, "active": active})
    assert response.status_code == 200


def test_owner_context_is_independent_of_wb_binding_and_wb_requests_are_blocked(client):
    response = client.get("/api/v1/marketplace-context/session?marketplace=ozon", headers=headers())
    assert response.status_code == 200
    assert response.json()["authorized"] is True and response.json()["can_write"] is True
    api.shop_context.authorize.assert_called_with(0, "owner-id", "owner")
    assert client.get("/api/v1/dashboard/finance", headers=headers()).status_code == 409
    client.reached.assert_not_called()


@pytest.mark.parametrize("marketplace", ["ozon", "bad", "WB", ""])
def test_header_marketplace_mismatch_or_unknown_never_calls_wb(client, marketplace):
    assert client.get("/api/v1/dashboard/finance", headers=headers(marketplace=marketplace)).status_code in {400, 409}
    client.reached.assert_not_called()


def test_secondary_wb_shop_cannot_be_used_as_ozon_seller(client):
    assert client.get("/api/v1/marketplace-context/session?marketplace=ozon", headers=headers(**{"x-mh-shop-id": "2"})).status_code == 409
    assert client.get("/api/v1/ozon-accounting/workspace?shop_id=2", headers=headers()).status_code == 409
    client.reached.assert_not_called()


def test_wb_primary_grant_does_not_implicitly_grant_ozon(client):
    shared_warehouse.register_shop(0, "verified-primary", [("member", True)])
    response = client.get("/api/v1/marketplace-context/session?marketplace=ozon", headers=headers("manager", "member"))
    assert response.status_code == 403
    grant(client)
    assert client.get("/api/v1/marketplace-context/session?marketplace=ozon", headers=headers("manager", "member")).json()["can_write"] is True


@pytest.mark.parametrize("role,writable", [("manager", True), ("viewer", False), ("warehouse", False)])
def test_ozon_grant_cannot_exceed_global_role(client, role, writable):
    grant(client)
    result = client.get("/api/v1/marketplace-context/session?marketplace=ozon", headers=headers(role, "member"))
    assert result.status_code == 200 and result.json()["can_write"] is writable


def test_revoked_readonly_and_integration_grants_fail_closed(client):
    grant(client, can_write=False)
    response = client.post("/api/v1/ozon-accounting/test-body", headers=headers("manager", "member"), json={})
    assert response.status_code == 403
    grant(client, active=False)
    assert client.get("/api/v1/ozon-accounting/workspace", headers=headers("manager", "member")).status_code == 403
    grant(client)
    assert client.get("/api/v1/marketplace-context/session?marketplace=ozon", headers=headers("integration_reader", "member")).status_code == 403
    client.reached.assert_not_called()


def test_trusted_actor_headers_are_required_even_for_owner(client):
    for values in ({}, {"x-tenant-role": "owner"}, headers("unknown")):
        assert client.get("/api/v1/marketplace-context/session?marketplace=ozon", headers=values).status_code == 403


def test_demo_cannot_write_or_advertise_writable_shared_routes(client, monkeypatch):
    monkeypatch.setattr(api, "IS_DEMO", True)
    result = client.get("/api/v1/marketplace-context/session?marketplace=ozon", headers=headers()).json()
    assert result["is_demo"] and not result["can_write"]
    assert all(r["method"] in {"GET", "HEAD", "OPTIONS"} for r in result["shared_routes"])
    assert client.post("/api/v1/ozon-accounting/test-body", headers=headers(), json={}).status_code == 403


def test_anonymous_demo_handshake_read_only_and_forged_owner_never_writes(client, monkeypatch):
    monkeypatch.setattr(api, "IS_DEMO", True)
    for marketplace in ("wb", "ozon"):
        result = client.get("/api/v1/marketplace-context/session?marketplace=" + marketplace).json()
        assert result["authorized"] and result["role"] == "viewer" and not result["can_write"] and result["is_demo"]
    assert client.get("/api/v1/ozon-prices", headers={"x-mh-marketplace": "ozon"}).status_code == 200
    for selected in ({"x-mh-marketplace": "ozon"}, headers()):
        assert client.post("/api/v1/ozon-accounting/test-body", headers=selected, json={}).status_code == 403
    assert client.put("/api/v1/marketplace-context/ozon/access", headers=headers(), json={"account":"forged", "can_write":True}).status_code == 403


@pytest.mark.parametrize("data", [{"marketplace": "wb"}, {"shop_id": 2}, {"shop_id": False}])
def test_wrong_body_selectors_are_rejected_before_handler(client, data):
    assert client.post("/api/v1/ozon-accounting/test-body", headers=headers(), json=data).status_code == 409
    client.reached.assert_not_called()


def test_valid_json_body_survives_guard_and_legacy_wb_is_compatible(client):
    value = {"marketplace": "ozon", "shop_id": 0, "payload": "kept"}
    result = client.post("/api/v1/ozon-accounting/test-body", headers=headers(), json=value)
    assert result.status_code == 200 and result.json() == value
    assert client.get("/api/v1/dashboard/finance").status_code == 200
    client.reached.reset_mock()
    assert client.get("/api/v1/ozon-accounting/workspace", headers={"x-tenant-role": "manager", "x-tenant-account": "no-grant"}).status_code == 403
    client.reached.assert_not_called()


def test_wb_header_cannot_reach_ozon_and_new_prefixes_are_covered(client):
    assert client.get("/api/v1/ozon-accounting/workspace", headers=headers(marketplace="wb")).status_code == 409
    for path in ("/api/v1/ozon-insights/products", "/api/v1/ozon-prices", "/api/v1/cashflow"):
        assert client.get(path, headers=headers()).status_code == 200
    assert client.get("/api/v1/settings/costs", headers=headers()).status_code == 409
    assert client.get("/api/v1/production-workspace", headers=headers()).status_code == 409


def test_only_owner_manages_grants_and_inactive_accounts_are_listed(client):
    grant(client, active=False)
    assert client.get("/api/v1/marketplace-context/ozon/access", headers=headers()).json()["items"][0]["active"] is False
    grant(client)
    result = client.put("/api/v1/marketplace-context/ozon/access", headers=headers("manager", "member"), json={"account": "someone", "can_write": True})
    assert result.status_code == 403
