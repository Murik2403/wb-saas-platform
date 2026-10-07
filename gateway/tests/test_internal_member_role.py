"""Actual HTTP acceptance of fresh tenant-scoped delegation role reads."""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from fastapi import FastAPI
import config
import internal_routes
from internal_auth import tenant_credential
from logic import accounts, erasure_ledger
from .base import GatewayDbTestCase


class TenantMemberRoleTests(GatewayDbTestCase):
    def setUp(self):
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.owner = accounts.create_account(conn, "owner@synthetic.invalid", "synthetic-password-123")
            self.tenant = accounts.create_tenant_instance(conn, self.owner, "synthetic-a")
            accounts.set_account_status(conn, self.owner, "active")
            accounts.set_tenant_status(conn, self.tenant, "running")
            self.other = accounts.create_account(conn, "other@synthetic.invalid", "synthetic-password-123")
            self.other_tenant = accounts.create_tenant_instance(conn, self.other, "synthetic-b")
            accounts.set_account_status(conn, self.other, "active")
            accounts.set_tenant_status(conn, self.other_tenant, "running")
        self.secret_patch = mock.patch.object(config, "INTERNAL_API_SECRET", "synthetic-master-not-live")
        self.db_patch = mock.patch.object(internal_routes, "control_db", mock.Mock(connect=self.connect))
        self.secret_patch.start(); self.db_patch.start()
        app = FastAPI(); app.include_router(internal_routes.router)
        self.app = app

    def tearDown(self):
        self.db_patch.stop(); self.secret_patch.stop()
        super().tearDown()

    def read(self, account=None, slug="synthetic-a", secret=None):
        return self.post(json={"slug": slug, "account": str(account or self.owner)},
            headers={"X-Internal-Secret": secret or tenant_credential("synthetic-master-not-live", slug)})

    def post(self, *, json, headers=None):
        """Exercise real ASGI HTTP routing without a test-only transport dependency.

        Production Gateway does not ship httpx/httpx2. No route handler or
        authentication method is replaced; only the isolated DB/config fixture is supplied.
        """
        encoded = __import__('json').dumps(json).encode()
        events = []
        async def receive():
            return {"type": "http.request", "body": encoded, "more_body": False}
        async def send(message):
            events.append(message)
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                 "method": "POST", "scheme": "http", "path": "/internal/tenant-member-role",
                 "raw_path": b"/internal/tenant-member-role", "query_string": b"",
                 "headers": [(b"host", b"127.0.0.1"), (b"content-type", b"application/json")]+
                            [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()],
                 "client": ("127.0.0.1", 10001), "server": ("127.0.0.1", 8000)}
        asyncio.run(self.app(scope, receive, send))
        started = next(event for event in events if event['type'] == 'http.response.start')
        body = b''.join(event.get('body', b'') for event in events if event['type'] == 'http.response.body')
        return SimpleNamespace(status_code=started['status'],
                               headers={key.decode(): value.decode() for key, value in started['headers']},
                               json=lambda: __import__('json').loads(body))

    def test_current_role_and_revocation_visible_without_cache(self):
        response = self.read()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"role": "owner"})
        self.assertEqual(response.headers["cache-control"], "no-store")
        with self.connect() as conn:
            conn.execute("UPDATE tenant_memberships SET role='viewer' WHERE tenant_id=? AND account_id=?", (self.tenant, self.owner))
        self.assertEqual(self.read().json(), {"role": "viewer"})
        with self.connect() as conn:
            conn.execute("DELETE FROM tenant_memberships WHERE tenant_id=? AND account_id=?", (self.tenant, self.owner))
        self.assertEqual(self.read().json(), {"role": None})

    def test_other_tenant_or_unknown_account_do_not_disclose_membership(self):
        self.assertEqual(self.read(self.other).json(), {"role": None})
        self.assertEqual(self.read(999999).json(), {"role": None})
        self.assertEqual(self.read(slug="unknown-synthetic").json(), {"role": None})
        wrong = tenant_credential("synthetic-master-not-live", "synthetic-b")
        self.assertEqual(self.read(secret=wrong).status_code, 403)
        self.assertEqual(self.read(secret="invalid").status_code, 403)

    def test_stopped_or_expired_tenant_and_unverified_member_fail_closed(self):
        with self.connect() as conn:
            accounts.set_tenant_status(conn, self.tenant, "stopped")
        self.assertEqual(self.read().json(), {"role": None})
        with self.connect() as conn:
            accounts.set_tenant_status(conn, self.tenant, "running")
            accounts.set_account_status(conn, self.owner, "canceled")
        self.assertEqual(self.read().json(), {"role": None})
        with self.connect() as conn:
            accounts.set_account_status(conn, self.owner, "active")
            conn.execute("UPDATE accounts SET email_verified_at='' WHERE id=?", (self.owner,))
        self.assertEqual(self.read().json(), {"role": None})

    def test_strict_account_and_tenant_dto(self):
        for account in (True, 1, "0", "01", "-1", "owner@example.com", "9223372036854775808"):
            with self.subTest(account=account):
                response = self.post(json={"slug": "synthetic-a", "account": account})
                self.assertEqual(response.status_code, 422)
        for payload in ({"slug": "../other", "account": "1"}, {"slug": "synthetic-a", "account": "1", "role": "owner"}):
            self.assertEqual(self.post(json=payload).status_code, 422)

    def test_unconfigured_secret_never_authenticates(self):
        with mock.patch.object(config, "INTERNAL_API_SECRET", ""):
            self.assertEqual(self.read().status_code, 403)

    def test_erased_manager_from_snapshot_never_gets_fresh_delegation(self):
        ledger_path = Path(self._tmpdir.name) / "independent-erasures.sqlite3"
        erasure_ledger.initialize(ledger_path)
        with mock.patch.dict(os.environ, {
            "WB_SAAS_ERASURE_LEDGER_PATH": str(ledger_path),
            "WB_SAAS_ERASURE_LEDGER_REQUIRED": "1",
        }):
            with self.connect() as conn:
                conn.execute("INSERT INTO tenant_memberships(tenant_id,account_id,role,created_at,updated_at) VALUES (?,?,'manager','2026-10-08','2026-10-08')", (self.tenant, self.other))
                manager = dict(accounts.get_account_by_id(conn, self.other))
            self.assertEqual(self.read(self.other).json(), {"role": "manager"})
            erasure_ledger.record(manager)
            with self.connect() as conn:
                restored = accounts.get_account_by_id(conn, self.other)
                self.assertEqual(restored["deletion_requested_at"], "")
                self.assertEqual(restored["status"], "active")
            self.assertEqual(self.read(self.other).json(), {"role": None})
            self.assertEqual(self.read(self.owner).json(), {"role": "owner"})
