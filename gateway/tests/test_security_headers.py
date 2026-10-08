"""Browser protection must reach real routes without breaking teaching media."""
import hashlib
import json

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.testclient import TestClient

import app as gateway
import admin_routes
import training_materials
from logic import db


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    real_connect = db.connect
    monkeypatch.setattr(db, "connect", lambda *args, **kwargs: real_connect(tmp_path / "gateway.sqlite3"))
    gateway.auth_rate_limiter.clear()


@pytest.mark.parametrize("path", ["/", "/training", "/login", "/register", "/forgot-password"])
def test_public_and_auth_html_have_browser_protection(path):
    with TestClient(gateway.app) as client:
        response = client.get(path)
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    policy = response.headers["content-security-policy"]
    assert "frame-ancestors 'self'" in policy
    assert "object-src 'none'" in policy
    assert "base-uri 'self'" in policy
    # Existing inline theme code, consent-gated analytics and outgoing demo iframe
    # remain permitted. This is a framing policy, not a script-source rollout.
    assert "script-src" not in policy
    assert "default-src" not in policy
    assert "frame-src" not in policy
    assert response.headers["cache-control"] == "private, no-store"
    if path == "/":
        assert "<iframe" in response.text


def test_invalid_login_keeps_csrf_cookie_and_html_headers():
    with TestClient(gateway.app) as client:
        response = client.post("/login", data={"email": "test@example.test", "password": "invalid"})
    assert response.status_code == 400
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert "csrf" in response.headers["set-cookie"]


def test_admin_html_protected_and_unauthorized_redirect_still_requires_login(monkeypatch):
    with TestClient(gateway.app, follow_redirects=False) as client:
        unauthorized = client.get("/admin")
        assert unauthorized.status_code == 303
        assert unauthorized.headers["location"] == "/login?next=%2Fadmin"
        assert unauthorized.headers["x-content-type-options"] == "nosniff"
        monkeypatch.setattr(admin_routes, "_require_admin", lambda request: {"id": 1})
        response = client.get("/admin")
        assert response.status_code == 200
        assert response.headers["x-frame-options"] == "SAMEORIGIN"
        assert "frame-ancestors 'self'" in response.headers["content-security-policy"]
        assert response.headers["cache-control"] == "private, no-store"


def test_video_range_and_immutable_cache_are_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(training_materials, "MEDIA_ROOT", tmp_path)
    body = bytes(range(128))
    digest = hashlib.sha256(body).hexdigest()
    filename = f"lesson-01-today.{digest}.mp4"
    (tmp_path / filename).write_bytes(body)
    (tmp_path / "manifest.json").write_text(json.dumps({
        "version": 1, "approved": True,
        "videos": {"lesson-01-today": {"filename": filename, "sha256": digest, "bytes": len(body)}},
    }))
    with TestClient(gateway.app) as client:
        response = client.get("/training/media/" + filename, headers={"Range": "bytes=0-31"})
    assert response.status_code == 206
    assert response.content == body[:32]
    assert response.headers["content-range"] == "bytes 0-31/128"
    assert "immutable" in response.headers["cache-control"]
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in response.headers


@pytest.mark.parametrize("path", ["/healthz", "/robots.txt", "/favicon.svg", "/missing-resource"])
def test_non_html_responses_get_nosniff_without_document_policy(path):
    with TestClient(gateway.app) as client:
        response = client.get(path)
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in response.headers


def test_stricter_route_policy_and_streaming_are_preserved():
    from security_headers import SecurityHeadersMiddleware
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/strict")
    def strict():
        return HTMLResponse("protected", headers={
            "X-Frame-Options": "DENY", "Content-Security-Policy": "frame-ancestors 'none'",
            "Referrer-Policy": "no-referrer",
        })

    @app.get("/stream")
    async def stream():
        async def chunks():
            yield b"first"
            yield b"second"
        return StreamingResponse(chunks(), media_type="text/html")

    with TestClient(app) as client:
        response = client.get("/strict")
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers.get_list("content-security-policy") == [
            "frame-ancestors 'none'", "frame-ancestors 'self'; object-src 'none'; base-uri 'self'",
        ]
        streamed = client.get("/stream")
        assert streamed.content == b"firstsecond"
        assert streamed.headers["x-frame-options"] == "SAMEORIGIN"


def test_hsts_only_on_https():
    with TestClient(gateway.app, base_url="https://testserver") as client:
        assert client.get("/healthz").headers["strict-transport-security"] == "max-age=31536000"
    with TestClient(gateway.app, base_url="http://testserver") as client:
        assert "strict-transport-security" not in client.get("/healthz").headers
