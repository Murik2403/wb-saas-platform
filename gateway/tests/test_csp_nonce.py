"""A nonce must authorize existing scripts without opening inline injection."""
from html.parser import HTMLParser
import re

import pytest
from fastapi.testclient import TestClient
import app as gateway
from logic import db


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.inline_handlers = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.tags.append(dict(attrs))
        self.inline_handlers.extend(name for name, value in attrs if name.startswith("on"))


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    original = db.connect
    monkeypatch.setattr(db, "connect", lambda *a, **kw: original(tmp_path / "control.sqlite3"))
    gateway.auth_rate_limiter.clear()


@pytest.mark.parametrize("path", ["/", "/pricing", "/training", "/login", "/register", "/forgot-password", "/calculator", "/docs", "/redoc", "/docs/oauth2-redirect"])
def test_header_nonce_matches_every_trusted_script(path):
    with TestClient(gateway.app) as client:
        response = client.get(path)
    assert response.status_code == 200
    policy = response.headers["content-security-policy"]
    found = re.search(r"'nonce-([A-Za-z0-9_-]{32})'", policy)
    assert found is not None
    assert "'strict-dynamic'" in policy
    assert "script-src-attr 'none'" in policy
    assert "'unsafe-inline'" not in policy and "'unsafe-eval'" not in policy
    parser = Scripts()
    parser.feed(response.text)
    assert parser.tags
    assert all(tag.get("nonce") == found[1] for tag in parser.tags)
    assert parser.inline_handlers == []
    assert response.headers["cache-control"] == "private, no-store"


def test_nonce_is_unpredictable_per_response_not_reused():
    with TestClient(gateway.app) as client:
        first = client.get("/")
        second = client.get("/")
    a = re.search(r"'nonce-([^']+)'", first.headers["content-security-policy"])
    b = re.search(r"'nonce-([^']+)'", second.headers["content-security-policy"])
    assert a and b and a[1] != b[1]


def test_login_script_policy_does_not_authorize_remote_hosts():
    with TestClient(gateway.app) as client:
        response = client.get("/login")
    policy = response.headers["content-security-policy"]
    assert "'strict-dynamic'" in policy
    assert "https://" not in policy
    assert "https:" not in policy
