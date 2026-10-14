"""Public signups must never occupy service or backup namespaces."""
from unittest import mock

from logic import accounts

from .base import GatewayDbTestCase


class ReservedTenantSlugTests(GatewayDbTestCase):
    def setUp(self):
        super().setUp()
        self.init_db()

    def test_unused_service_names_always_get_a_suffix_after_normalization(self):
        for email, base in (
            ("demo@example.test", "demo"),
            ("DEMO@example.test", "demo"),
            ("control-plane@example.test", "control-plane"),
            ("Control.Plane@example.test", "control-plane"),
            ("training-media@example.test", "training-media"),
            ("training_media@example.test", "training-media"),
        ):
            with self.subTest(email=email), self.connect() as conn:
                with mock.patch.object(accounts.secrets, "token_hex", return_value="a1b2"):
                    slug = accounts.generate_unique_slug(conn, email, max_attempts=1)
                self.assertEqual(slug, f"{base}-a1b2")
                self.assertIsNone(accounts.get_tenant_by_slug(conn, base))

    def test_occupied_suffix_retries_and_preserves_existing_tenant_and_owner(self):
        with self.connect() as conn:
            owner = accounts.create_account(conn, "operator@example.test", "synthetic password123")
            accounts.create_tenant_instance(conn, owner, "training-media-a1b2")
            tenant = accounts.get_tenant_by_slug(conn, "training-media-a1b2")
            before = dict(tenant)
            membership = dict(accounts.get_tenant_membership(conn, tenant["id"], owner))
            with mock.patch.object(accounts.secrets, "token_hex", side_effect=["a1b2", "c3d4"]):
                slug = accounts.generate_unique_slug(conn, "training-media@example.test", max_attempts=2)
            self.assertEqual(slug, "training-media-c3d4")
            self.assertEqual(dict(accounts.get_tenant_by_slug(conn, "training-media-a1b2")), before)
            self.assertEqual(dict(accounts.get_tenant_membership(conn, tenant["id"], owner)), membership)
            self.assertEqual(membership["role"], "owner")

    def test_existing_legacy_reserved_tenant_is_not_renamed(self):
        with self.connect() as conn:
            owner = accounts.create_account(conn, "legacy@example.test", "synthetic password123")
            accounts.create_tenant_instance(conn, owner, "demo")
            before = dict(accounts.get_tenant_by_slug(conn, "demo"))
            with mock.patch.object(accounts.secrets, "token_hex", return_value="a1b2"):
                self.assertEqual(accounts.generate_unique_slug(conn, "demo@example.test"), "demo-a1b2")
            self.assertEqual(dict(accounts.get_tenant_by_slug(conn, "demo")), before)

    def test_exhausted_suffix_collision_fails_without_returning_service_name(self):
        with self.connect() as conn:
            owner = accounts.create_account(conn, "operator@example.test", "synthetic password123")
            accounts.create_tenant_instance(conn, owner, "control-plane-a1b2")
            with mock.patch.object(accounts.secrets, "token_hex", return_value="a1b2"):
                with self.assertRaises(RuntimeError):
                    accounts.generate_unique_slug(conn, "control-plane@example.test", max_attempts=2)
            self.assertIsNone(accounts.get_tenant_by_slug(conn, "control-plane"))

    def test_nonreserved_free_name_keeps_existing_behavior_without_randomness(self):
        with self.connect() as conn, mock.patch.object(accounts.secrets, "token_hex", side_effect=AssertionError("unneeded suffix")):
            self.assertEqual(accounts.generate_unique_slug(conn, "seller@example.test"), "seller")
