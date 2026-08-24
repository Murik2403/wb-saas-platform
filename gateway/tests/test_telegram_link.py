"""Unit tests for the Telegram link-code flow: logic/accounts.py's
create_telegram_link_code/consume_telegram_link_code/get_telegram_chat_id_for_slug,
and the equivalent security-risk shape as password_reset.py -- a bad actor
guessing or reusing a code must never link someone else's chat.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from logic import accounts

from .base import GatewayDbTestCase


class CreateTelegramLinkCodeTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "seller@shop.ru", "password1234")

    def test_returns_a_code(self) -> None:
        with self.connect() as conn:
            code = accounts.create_telegram_link_code(conn, self.account_id)
        self.assertTrue(code)

    def test_two_calls_produce_different_codes(self) -> None:
        with self.connect() as conn:
            first = accounts.create_telegram_link_code(conn, self.account_id)
            second = accounts.create_telegram_link_code(conn, self.account_id)
        self.assertNotEqual(first, second)


class ConsumeTelegramLinkCodeTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "seller@shop.ru", "password1234")
            self.slug = accounts.generate_unique_slug(conn, "seller@shop.ru")
            accounts.create_tenant_instance(conn, self.account_id, self.slug)
            self.code = accounts.create_telegram_link_code(conn, self.account_id)

    def test_valid_code_links_the_chat_id(self) -> None:
        with self.connect() as conn:
            ok = accounts.consume_telegram_link_code(conn, self.code, "123456789")
        self.assertTrue(ok)
        with self.connect() as conn:
            self.assertEqual(accounts.get_telegram_chat_id_for_slug(conn, self.slug), "123456789")

    def test_code_is_single_use(self) -> None:
        with self.connect() as conn:
            accounts.consume_telegram_link_code(conn, self.code, "111")
        with self.connect() as conn:
            ok = accounts.consume_telegram_link_code(conn, self.code, "222")
        self.assertFalse(ok)
        with self.connect() as conn:
            # the second (rejected) attempt must not have overwritten the first link
            self.assertEqual(accounts.get_telegram_chat_id_for_slug(conn, self.slug), "111")

    def test_unknown_code_is_rejected(self) -> None:
        with self.connect() as conn:
            ok = accounts.consume_telegram_link_code(conn, "NOSUCH", "123")
        self.assertFalse(ok)

    def test_empty_code_is_rejected(self) -> None:
        with self.connect() as conn:
            ok = accounts.consume_telegram_link_code(conn, "", "123")
        self.assertFalse(ok)

    def test_code_lookup_is_case_insensitive(self) -> None:
        # A user typing the code by hand on a phone keyboard may not match
        # the exact case it was displayed in.
        with self.connect() as conn:
            ok = accounts.consume_telegram_link_code(conn, self.code.lower(), "123")
        self.assertTrue(ok)

    def test_expired_code_is_rejected(self) -> None:
        with self.connect() as conn:
            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
            conn.execute("UPDATE telegram_link_codes SET expires_at=? WHERE code=?", (expired, self.code))
        with self.connect() as conn:
            ok = accounts.consume_telegram_link_code(conn, self.code, "123")
        self.assertFalse(ok)

    def test_link_code_for_one_account_cannot_touch_another(self) -> None:
        with self.connect() as conn:
            other_account_id = accounts.create_account(conn, "other@shop.ru", "password5678")
            other_slug = accounts.generate_unique_slug(conn, "other@shop.ru")
            accounts.create_tenant_instance(conn, other_account_id, other_slug)
        with self.connect() as conn:
            accounts.consume_telegram_link_code(conn, self.code, "123")
        with self.connect() as conn:
            self.assertEqual(accounts.get_telegram_chat_id_for_slug(conn, self.slug), "123")
            self.assertEqual(accounts.get_telegram_chat_id_for_slug(conn, other_slug), "")


class GetTelegramChatIdForSlugTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()

    def test_unlinked_tenant_returns_empty_string(self) -> None:
        with self.connect() as conn:
            account_id = accounts.create_account(conn, "seller@shop.ru", "password1234")
            slug = accounts.generate_unique_slug(conn, "seller@shop.ru")
            accounts.create_tenant_instance(conn, account_id, slug)
        with self.connect() as conn:
            self.assertEqual(accounts.get_telegram_chat_id_for_slug(conn, slug), "")

    def test_unknown_slug_returns_empty_string(self) -> None:
        with self.connect() as conn:
            self.assertEqual(accounts.get_telegram_chat_id_for_slug(conn, "no-such-slug"), "")


if __name__ == "__main__":
    import unittest
    unittest.main()
