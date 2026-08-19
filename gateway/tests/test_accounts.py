from __future__ import annotations

from logic import accounts

from .base import GatewayDbTestCase


class PasswordHashingTests(GatewayDbTestCase):
    def test_hash_and_verify_roundtrip(self) -> None:
        h, salt = accounts.hash_password("correct horse battery staple")
        self.assertTrue(accounts.verify_password("correct horse battery staple", h, salt))

    def test_verify_rejects_wrong_password(self) -> None:
        h, salt = accounts.hash_password("correct horse battery staple")
        self.assertFalse(accounts.verify_password("wrong password", h, salt))

    def test_same_password_different_salt_each_time(self) -> None:
        h1, salt1 = accounts.hash_password("same password")
        h2, salt2 = accounts.hash_password("same password")
        self.assertNotEqual(salt1, salt2)
        self.assertNotEqual(h1, h2)  # different salts -> different hashes even for identical password

    def test_password_issues_flags_short_password(self) -> None:
        self.assertTrue(accounts.password_issues("short"))
        self.assertEqual(accounts.password_issues("a long enough password"), [])


class EmailValidationTests(GatewayDbTestCase):
    def test_valid_emails(self) -> None:
        for email in ["a@b.ru", "seller.one@wb-shop.com", "x.y+z@sub.domain.io"]:
            self.assertTrue(accounts.validate_email(email), email)

    def test_invalid_emails(self) -> None:
        for email in ["", "not-an-email", "a@b", "a @b.ru", None]:
            self.assertFalse(accounts.validate_email(email), email)


class SlugTests(GatewayDbTestCase):
    def test_slugify_base_strips_unsafe_chars(self) -> None:
        self.assertEqual(accounts.slugify_base("Seller.One+Test@wb-shop.com"), "seller-one-test")

    def test_slugify_base_empty_local_part_falls_back(self) -> None:
        self.assertEqual(accounts.slugify_base("@@@@@@wb-shop.com"), "client")

    def test_generate_unique_slug_avoids_collision(self) -> None:
        self.init_db()
        with self.connect() as conn:
            account_id_1 = accounts.create_account(conn, "seller@shop.ru", "password1234")
            account_id_2 = accounts.create_account(conn, "seller@other.ru", "password1234")
            slug1 = accounts.generate_unique_slug(conn, "seller@shop.ru")
            accounts.create_tenant_instance(conn, account_id_1, slug1)
            # a different email with the exact same local-part must not collide
            slug2 = accounts.generate_unique_slug(conn, "seller@another-domain.ru")
        self.assertEqual(slug1, "seller")
        self.assertNotEqual(slug1, slug2)
        self.assertTrue(slug2.startswith("seller-"))


class AccountLifecycleTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()

    def test_create_account_then_authenticate(self) -> None:
        with self.connect() as conn:
            account_id = accounts.create_account(conn, "Seller@Shop.RU", "password1234")
        with self.connect() as conn:
            account = accounts.authenticate_account(conn, "seller@shop.ru", "password1234")
        self.assertIsNotNone(account)
        self.assertEqual(account["id"], account_id)
        self.assertEqual(account["status"], "pending")

    def test_authenticate_wrong_password_fails(self) -> None:
        with self.connect() as conn:
            accounts.create_account(conn, "seller@shop.ru", "password1234")
        with self.connect() as conn:
            self.assertIsNone(accounts.authenticate_account(conn, "seller@shop.ru", "wrong-password"))

    def test_authenticate_unknown_email_fails(self) -> None:
        with self.connect() as conn:
            self.assertIsNone(accounts.authenticate_account(conn, "nobody@shop.ru", "password1234"))

    def test_duplicate_email_rejected_case_insensitively(self) -> None:
        with self.connect() as conn:
            accounts.create_account(conn, "seller@shop.ru", "password1234")
        with self.connect() as conn:
            with self.assertRaises(ValueError):
                accounts.create_account(conn, "Seller@SHOP.ru", "another-password")

    def test_invalid_email_rejected(self) -> None:
        with self.connect() as conn:
            with self.assertRaises(ValueError):
                accounts.create_account(conn, "not-an-email", "password1234")

    def test_weak_password_rejected(self) -> None:
        with self.connect() as conn:
            with self.assertRaises(ValueError):
                accounts.create_account(conn, "seller@shop.ru", "short")


class SessionTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "seller@shop.ru", "password1234")

    def test_create_and_resolve_session(self) -> None:
        with self.connect() as conn:
            token = accounts.create_session(conn, self.account_id)
        with self.connect() as conn:
            account = accounts.resolve_session(conn, token)
        self.assertIsNotNone(account)
        self.assertEqual(account["id"], self.account_id)

    def test_resolve_unknown_token_returns_none(self) -> None:
        with self.connect() as conn:
            self.assertIsNone(accounts.resolve_session(conn, "not-a-real-token"))

    def test_resolve_expired_session_returns_none_and_cleans_up(self) -> None:
        with self.connect() as conn:
            token = accounts.create_session(conn, self.account_id, ttl_hours=-1)  # already expired
        with self.connect() as conn:
            self.assertIsNone(accounts.resolve_session(conn, token))
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM sessions").fetchone()
        self.assertIsNone(row)  # expired session was purged, not just ignored

    def test_revoke_session_invalidates_it(self) -> None:
        with self.connect() as conn:
            token = accounts.create_session(conn, self.account_id)
        with self.connect() as conn:
            accounts.revoke_session(conn, token)
        with self.connect() as conn:
            self.assertIsNone(accounts.resolve_session(conn, token))

    def test_revoke_all_sessions_for_account(self) -> None:
        with self.connect() as conn:
            t1 = accounts.create_session(conn, self.account_id)
            t2 = accounts.create_session(conn, self.account_id)
        with self.connect() as conn:
            accounts.revoke_all_sessions_for_account(conn, self.account_id)
        with self.connect() as conn:
            self.assertIsNone(accounts.resolve_session(conn, t1))
            self.assertIsNone(accounts.resolve_session(conn, t2))

    def test_two_sessions_for_same_account_have_different_tokens(self) -> None:
        with self.connect() as conn:
            t1 = accounts.create_session(conn, self.account_id)
            t2 = accounts.create_session(conn, self.account_id)
        self.assertNotEqual(t1, t2)


class TenantProvisioningStateMachineTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "seller@shop.ru", "password1234")

    def test_new_account_has_no_access_before_provisioning(self) -> None:
        with self.connect() as conn:
            account = accounts.get_account_by_id(conn, self.account_id)
        self.assertFalse(accounts.account_has_access(account))

    def test_mark_tenant_provisioned_only_flips_infra_status_not_billing(self) -> None:
        """mark_tenant_provisioned() is infra-only (container is healthy);
        it deliberately does NOT grant account access on its own -- that's
        billing.start_trial()'s job, called separately by the same caller
        (see provisioning.py). See logic/billing.py's test suite for the
        full provisioning-grants-access-via-trial flow."""
        with self.connect() as conn:
            slug = accounts.generate_unique_slug(conn, "seller@shop.ru")
            tenant_id = accounts.create_tenant_instance(conn, self.account_id, slug)
            tenant = accounts.get_tenant_for_account(conn, self.account_id)
            self.assertEqual(tenant["status"], "provisioning")
            self.assertEqual(tenant["container_name"], f"wb-tenant-{slug}")

            accounts.mark_tenant_provisioned(conn, tenant_id, self.account_id)

            tenant_after = accounts.get_tenant_for_account(conn, self.account_id)
            account_after = accounts.get_account_by_id(conn, self.account_id)
        self.assertEqual(tenant_after["status"], "running")
        self.assertEqual(account_after["status"], "pending")
        self.assertFalse(accounts.account_has_access(account_after))

    def test_cannot_create_second_tenant_instance_for_same_account(self) -> None:
        with self.connect() as conn:
            slug = accounts.generate_unique_slug(conn, "seller@shop.ru")
            accounts.create_tenant_instance(conn, self.account_id, slug)
        with self.connect() as conn:
            with self.assertRaises(ValueError):
                accounts.create_tenant_instance(conn, self.account_id, "another-slug")

    def test_failed_provisioning_does_not_grant_access(self) -> None:
        with self.connect() as conn:
            slug = accounts.generate_unique_slug(conn, "seller@shop.ru")
            tenant_id = accounts.create_tenant_instance(conn, self.account_id, slug)
            accounts.set_tenant_status(conn, tenant_id, "failed", note="Docker: no space left on device")
            account = accounts.get_account_by_id(conn, self.account_id)
        self.assertFalse(accounts.account_has_access(account))

    def test_invalid_tenant_status_rejected(self) -> None:
        with self.connect() as conn:
            slug = accounts.generate_unique_slug(conn, "seller@shop.ru")
            tenant_id = accounts.create_tenant_instance(conn, self.account_id, slug)
            with self.assertRaises(ValueError):
                accounts.set_tenant_status(conn, tenant_id, "not-a-real-status")


if __name__ == "__main__":
    import unittest
    unittest.main()
