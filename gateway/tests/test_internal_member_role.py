"""Actual HTTP acceptance of fresh tenant-scoped delegation role reads."""
from unittest import mock
from fastapi import FastAPI
from fastapi.testclient import TestClient
import config
import internal_routes
from internal_auth import tenant_credential
from logic import accounts
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
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.db_patch.stop(); self.secret_patch.stop()
        super().tearDown()

    def read(self, account=None, slug="synthetic-a", secret=None):
        return self.client.post("/internal/tenant-member-role", json={"slug": slug, "account": str(account or self.owner)},
            headers={"X-Internal-Secret": secret or tenant_credential("synthetic-master-not-live", slug)})

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
                response = self.client.post("/internal/tenant-member-role", json={"slug": "synthetic-a", "account": account})
                self.assertEqual(response.status_code, 422)
        for payload in ({"slug": "../other", "account": "1"}, {"slug": "synthetic-a", "account": "1", "role": "owner"}):
            self.assertEqual(self.client.post("/internal/tenant-member-role", json=payload).status_code, 422)

    def test_unconfigured_secret_never_authenticates(self):
        with mock.patch.object(config, "INTERNAL_API_SECRET", ""):
            self.assertEqual(self.read().status_code, 403)
