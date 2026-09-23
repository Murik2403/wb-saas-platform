"""Revocable tenant-scoped, read-only integration credentials."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone


def init_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS integration_tokens (
        token_hash TEXT PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenant_instances(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL, expires_at TEXT NOT NULL)""")


def issue(conn, tenant_id):
    # One credential per tenant; rotation immediately invalidates the old one.
    token = "mh_read_" + secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    conn.execute("DELETE FROM integration_tokens WHERE tenant_id=?", (tenant_id,))
    conn.execute("INSERT INTO integration_tokens(token_hash,tenant_id,created_at,expires_at) VALUES (?,?,?,?)",
                 (hashlib.sha256(token.encode()).hexdigest(), tenant_id, now.isoformat(), (now + timedelta(days=90)).isoformat()))
    return token


def revoke(conn, tenant_id):
    conn.execute("DELETE FROM integration_tokens WHERE tenant_id=?", (tenant_id,))


def valid(conn, token, tenant_id):
    if not token.startswith("mh_read_") or len(token) > 100:
        return False
    row = conn.execute("SELECT expires_at FROM integration_tokens WHERE token_hash=? AND tenant_id=?",
                       (hashlib.sha256(token.encode()).hexdigest(), tenant_id)).fetchone()
    return bool(row and datetime.fromisoformat(row["expires_at"]) > datetime.now(timezone.utc))
