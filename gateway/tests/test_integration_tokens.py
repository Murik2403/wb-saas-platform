from datetime import datetime, timedelta, timezone
from logic import accounts, integration_tokens
from .base import GatewayDbTestCase


class IntegrationTokenTests(GatewayDbTestCase):
    def setUp(self):
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.owner = accounts.create_account(conn, "api-owner@example.test", "safe-password-123")
            self.tenant = accounts.create_tenant_instance(conn, self.owner, "api-owner")

    def test_scoped_hashed_rotation_expiry_revocation(self):
        with self.connect() as conn:
            token = integration_tokens.issue(conn, self.tenant)
            assert integration_tokens.valid(conn, token, self.tenant)
            assert not integration_tokens.valid(conn, token, self.tenant + 1)
            assert token not in str(dict(conn.execute("SELECT * FROM integration_tokens").fetchone()))
            replacement = integration_tokens.issue(conn, self.tenant)
            assert not integration_tokens.valid(conn, token, self.tenant)
            assert integration_tokens.valid(conn, replacement, self.tenant)
            conn.execute("UPDATE integration_tokens SET expires_at=?", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
            assert not integration_tokens.valid(conn, replacement, self.tenant)
            token = integration_tokens.issue(conn, self.tenant)
            integration_tokens.revoke(conn, self.tenant)
            assert not integration_tokens.valid(conn, token, self.tenant)

    def test_shop_roles_survive_invitation_acceptance(self):
        with self.connect() as conn:
            for role in ["technologist", "cutter", "seamstress", "warehouse"]:
                email = f"{role}@example.test"
                member = accounts.create_account(conn, email, "safe-password-123")
                token = accounts.create_membership_invitation(conn, self.tenant, email, role)
                accounts.consume_membership_invitation(conn, token, member)
                assert accounts.get_tenant_membership(conn, self.tenant, member)["role"] == role
