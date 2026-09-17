from __future__ import annotations

from datetime import datetime, timedelta, timezone

from logic import accounts

from .base import GatewayDbTestCase


class TenantMembershipTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.owner_id = accounts.create_account(conn, "owner@example.test", "safe-password-123")
            self.tenant_id = accounts.create_tenant_instance(conn, self.owner_id, "owner")
            accounts.set_account_status(conn, self.owner_id, "active")

    def test_owner_is_backfilled_and_invited_member_can_access_active_tenant(self) -> None:
        with self.connect() as conn:
            owner_membership = accounts.get_tenant_membership(conn, self.tenant_id, self.owner_id)
            self.assertEqual(owner_membership["role"], "owner")
            member_id = accounts.create_account(conn, "manager@example.test", "safe-password-123")
            token = accounts.create_membership_invitation(conn, self.tenant_id, "manager@example.test", "manager")
            self.assertEqual(accounts.consume_membership_invitation(conn, token, member_id), self.tenant_id)
            member_tenant = accounts.get_accessible_tenant_for_account(conn, member_id)
            self.assertEqual(member_tenant["membership_role"], "manager")
            self.assertTrue(accounts.tenant_has_access(conn, member_tenant))

    def test_invitation_is_single_use_and_email_bound(self) -> None:
        with self.connect() as conn:
            member_id = accounts.create_account(conn, "member@example.test", "safe-password-123")
            wrong_id = accounts.create_account(conn, "wrong@example.test", "safe-password-123")
            token = accounts.create_membership_invitation(conn, self.tenant_id, "member@example.test", "viewer")
            with self.assertRaisesRegex(ValueError, "другого email"):
                accounts.consume_membership_invitation(conn, token, wrong_id)
            accounts.consume_membership_invitation(conn, token, member_id)
            with self.assertRaisesRegex(ValueError, "уже использовано"):
                accounts.consume_membership_invitation(conn, token, member_id)

    def test_expired_invitation_is_rejected(self) -> None:
        with self.connect() as conn:
            member_id = accounts.create_account(conn, "expired@example.test", "safe-password-123")
            token = accounts.create_membership_invitation(conn, self.tenant_id, "expired@example.test", "viewer")
            conn.execute(
                "UPDATE tenant_invitations SET expires_at=? WHERE token_hash=?",
                ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds"), accounts._hash_token(token)),
            )
            with self.assertRaisesRegex(ValueError, "истёк"):
                accounts.consume_membership_invitation(conn, token, member_id)
