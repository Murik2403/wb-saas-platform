"""Scoped credentials through actual HTTP routes, isolated control-plane DB."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import re
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as gateway
from logic import accounts, csrf, db, integration_tokens


@pytest.fixture
def state(monkeypatch, tmp_path):
    real_connect = db.connect
    path = tmp_path / "gateway.sqlite3"
    monkeypatch.setattr(db, "connect", lambda *args, **kwargs: real_connect(path))
    monkeypatch.setattr(gateway.config, "PARENT_DOMAIN", "example.test")
    monkeypatch.setattr(gateway.config, "TENANT_SUBDOMAIN_PREFIX", "app")
    monkeypatch.setattr(gateway.config, "COOKIE_SECURE", True)
    # Exercise account creation directly: no registration endpoints or mail jobs.
    with db.connect() as conn:
        db.init_db(conn)
        owner = accounts.create_account(conn, "owner@example.test", "tests-only-password")
        other = accounts.create_account(conn, "other@example.test", "tests-only-password")
        member = accounts.create_account(conn, "member@example.test", "tests-only-password")
        tenant = accounts.create_tenant_instance(conn, owner, "seller")
        foreign = accounts.create_tenant_instance(conn, other, "foreign")
        for account in (owner, other, member):
            accounts.set_account_status(conn, account, "active")
        for tenant_id in (tenant, foreign):
            accounts.set_tenant_status(conn, tenant_id, "running")
        invitation = accounts.create_membership_invitation(conn, tenant, "member@example.test", "manager")
        accounts.consume_membership_invitation(conn, invitation, member)
        owner_session = accounts.create_session(conn, owner)
        member_session = accounts.create_session(conn, member)
        token = integration_tokens.issue(conn, tenant)
    client = TestClient(gateway.app, base_url="https://example.test", follow_redirects=False)
    yield {"client": client, "tenant": tenant, "owner": owner, "member": member, "token": token,
           "owner_session": owner_session, "member_session": member_session}
    client.close()


def verify(state, path="/api/v1/integration/products", method="GET", host="seller.app.example.test", token=None):
    return state["client"].get("/auth/verify", headers={
        "authorization": "Bearer " + (token or state["token"]),
        "x-forwarded-host": host, "x-forwarded-uri": path, "x-forwarded-method": method,
    })


@pytest.mark.parametrize("endpoint", ["products", "materials", "releases"])
def test_valid_token_only_grants_scoped_reader_for_its_tenant(state, endpoint):
    response = verify(state, f"/api/v1/integration/{endpoint}?limit=1&offset=0")
    assert response.status_code == 200
    assert response.headers["x-tenant-role"] == "integration_reader"
    assert response.headers["x-tenant-account"] == "integration"
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM integration_tokens WHERE tenant_id=?", (state["tenant"],)).fetchone()
    assert row["token_hash"] == hashlib.sha256(state["token"].encode()).hexdigest()
    assert state["token"] not in dict(row).values()


@pytest.mark.parametrize("host,expected", [("foreign.app.example.test", 401), ("missing.app.example.test", 401), ("seller.app.attacker.test", 403), ("example.test", 403)])
def test_token_cannot_cross_tenant_host(state, host, expected):
    response = verify(state, host=host)
    assert response.status_code == expected
    assert "x-tenant-role" not in response.headers


@pytest.mark.parametrize("method,path", [("POST", "/api/v1/integration/products"), ("DELETE", "/api/v1/integration/releases"), ("HEAD", "/api/v1/integration/products"), ("GET", "/api/v1/settings"), ("GET", "/api/v1/production-workspace"), ("GET", "/api/v1/integration/products/"), ("GET", "/api/v1/integration/products/../settings"), ("GET", "/api/v1/integration/products%2f..%2fsettings")])
def test_method_and_path_allowlist_is_exact_even_with_owner_cookie(state, method, path):
    state["client"].cookies.set(gateway.config.SESSION_COOKIE_NAME, state["owner_session"])
    response = verify(state, path=path, method=method)
    assert response.status_code == 403
    assert "x-tenant-role" not in response.headers


@pytest.mark.parametrize("reason", ["expired", "revoked", "rotated", "canceled", "stopped"])
def test_expired_revoked_rotated_and_inaccessible_tenants_fail_closed(state, reason):
    with db.connect() as conn:
        if reason == "expired":
            conn.execute("UPDATE integration_tokens SET expires_at=?", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
        elif reason == "revoked":
            integration_tokens.revoke(conn, state["tenant"])
        elif reason == "rotated":
            replacement = integration_tokens.issue(conn, state["tenant"])
            assert replacement != state["token"]
        elif reason == "canceled":
            accounts.set_account_status(conn, state["owner"], "canceled")
        else:
            accounts.set_tenant_status(conn, state["tenant"], "stopped")
    response = verify(state)
    assert response.status_code == 401
    assert "x-tenant-role" not in response.headers


def login(state, member=False):
    client = state["client"]
    client.cookies.set(gateway.config.SESSION_COOKIE_NAME, state["member_session" if member else "owner_session"])
    response = client.get("/account")
    assert response.status_code == 200
    return client.cookies.get(csrf.CSRF_COOKIE_NAME)


def test_owner_can_rotate_once_view_secret_then_revoke_with_csrf(state):
    token = login(state)
    response = state["client"].post("/account/integration-token", data={"action": "issue", "csrf_token": token})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    match = re.search(r"mh_read_[A-Za-z0-9_-]{43}", response.text)
    assert match is not None, "Issued credential must be visible once to its owner"
    replacement = match.group()
    assert verify(state).status_code == 401
    assert verify(state, token=replacement).status_code == 200
    assert replacement not in state["client"].get("/account").text
    response = state["client"].post("/account/integration-token", data={"action": "revoke", "csrf_token": token})
    assert response.status_code == 200
    assert verify(state, token=replacement).status_code == 401


@pytest.mark.parametrize("action", ["issue", "revoke"])
def test_manager_cannot_issue_or_revoke_even_with_valid_csrf(state, action):
    token = login(state, member=True)
    response = state["client"].post("/account/integration-token", data={"action": action, "csrf_token": token})
    assert response.status_code == 403
    assert verify(state).status_code == 200


@pytest.mark.parametrize("action", ["issue", "revoke"])
@pytest.mark.parametrize("submitted", ["", "forged-token"])
def test_missing_or_wrong_csrf_never_rotates_or_revokes(state, action, submitted):
    login(state)
    response = state["client"].post("/account/integration-token", data={"action": action, "csrf_token": submitted})
    assert "Форма устарела" in response.text
    assert not re.search(r"mh_read_[A-Za-z0-9_-]{43}", response.text)
    assert verify(state).status_code == 200


def test_unauthenticated_user_and_unknown_action_do_not_mutate(state):
    assert state["client"].post("/account/integration-token", data={"action": "issue"}).status_code == 303
    csrf_token = login(state)
    assert state["client"].post("/account/integration-token", data={"action": "erase", "csrf_token": csrf_token}).status_code == 422
    assert verify(state).status_code == 200
