"""Unit tests for logic/password_reset.py -- "forgot password" links.

The riskiest module in the gateway from a pure security standpoint: get it
wrong and either (a) an attacker resets someone else's password, or (b) the
route built on top of it leaks whether a given email is registered. Both
are covered here at the logic layer; mailer.py (the actual SMTP send) is
intentionally out of scope, same as yookassa_client.py elsewhere.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from logic import accounts, password_reset

from .base import GatewayDbTestCase


class RequestPasswordResetTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "seller@shop.ru", "password1234")

    def test_known_email_returns_a_token(self) -> None:
        with self.connect() as conn:
            token = password_reset.request_password_reset(conn, "seller@shop.ru")
        self.assertTrue(token)

    def test_unknown_email_returns_none(self) -> None:
        with self.connect() as conn:
            token = password_reset.request_password_reset(conn, "nobody@shop.ru")
        self.assertIsNone(token)

    def test_email_lookup_is_case_insensitive(self) -> None:
        with self.connect() as conn:
            token = password_reset.request_password_reset(conn, "SELLER@Shop.RU")
        self.assertTrue(token)

    def test_requesting_twice_produces_two_independent_usable_tokens(self) -> None:
        with self.connect() as conn:
            first = password_reset.request_password_reset(conn, "seller@shop.ru")
            second = password_reset.request_password_reset(conn, "seller@shop.ru")
        self.assertNotEqual(first, second)
        with self.connect() as conn:
            password_reset.consume_reset_token(conn, first, "newpassword1")
        with self.connect() as conn:
            # the second, still-unconsumed token for the same account still works
            password_reset.consume_reset_token(conn, second, "newpassword2")


class ConsumeResetTokenTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "seller@shop.ru", "password1234")
            self.token = password_reset.create_reset_token(conn, self.account_id)

    def test_valid_token_changes_the_password(self) -> None:
        with self.connect() as conn:
            account_id = password_reset.consume_reset_token(conn, self.token, "brandnewpass1")
        self.assertEqual(account_id, self.account_id)
        with self.connect() as conn:
            authenticated = accounts.authenticate_account(conn, "seller@shop.ru", "brandnewpass1")
            still_old = accounts.authenticate_account(conn, "seller@shop.ru", "password1234")
        self.assertIsNotNone(authenticated)
        self.assertIsNone(still_old)

    def test_token_is_single_use(self) -> None:
        with self.connect() as conn:
            password_reset.consume_reset_token(conn, self.token, "brandnewpass1")
        with self.connect() as conn:
            with self.assertRaises(ValueError):
                password_reset.consume_reset_token(conn, self.token, "anotherpass2")

    def test_unknown_token_is_rejected(self) -> None:
        with self.connect() as conn:
            with self.assertRaises(ValueError):
                password_reset.consume_reset_token(conn, "totally-made-up-token", "brandnewpass1")

    def test_empty_token_is_rejected(self) -> None:
        with self.connect() as conn:
            with self.assertRaises(ValueError):
                password_reset.consume_reset_token(conn, "", "brandnewpass1")

    def test_expired_token_is_rejected(self) -> None:
        with self.connect() as conn:
            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
            conn.execute(
                "UPDATE password_reset_tokens SET expires_at=? WHERE account_id=?",
                (expired, self.account_id),
            )
        with self.connect() as conn:
            with self.assertRaises(ValueError):
                password_reset.consume_reset_token(conn, self.token, "brandnewpass1")

    def test_weak_new_password_is_rejected_and_token_stays_usable(self) -> None:
        with self.connect() as conn:
            with self.assertRaises(ValueError):
                password_reset.consume_reset_token(conn, self.token, "short")
        # A rejected (too weak) password must not burn the token -- the user
        # should be able to immediately retry with a better password using
        # the same link, not be forced to request a brand new email.
        with self.connect() as conn:
            account_id = password_reset.consume_reset_token(conn, self.token, "goodenoughpass1")
        self.assertEqual(account_id, self.account_id)

    def test_consuming_revokes_all_existing_sessions(self) -> None:
        with self.connect() as conn:
            token_a = accounts.create_session(conn, self.account_id)
            token_b = accounts.create_session(conn, self.account_id)
        with self.connect() as conn:
            password_reset.consume_reset_token(conn, self.token, "brandnewpass1")
        with self.connect() as conn:
            self.assertIsNone(accounts.resolve_session(conn, token_a))
            self.assertIsNone(accounts.resolve_session(conn, token_b))

    def test_reset_token_for_one_account_cannot_touch_another(self) -> None:
        with self.connect() as conn:
            other_id = accounts.create_account(conn, "other@shop.ru", "password5678")
        with self.connect() as conn:
            account_id = password_reset.consume_reset_token(conn, self.token, "brandnewpass1")
        self.assertEqual(account_id, self.account_id)
        self.assertNotEqual(account_id, other_id)
        with self.connect() as conn:
            other_still_original = accounts.authenticate_account(conn, "other@shop.ru", "password5678")
        self.assertIsNotNone(other_still_original)


class DeleteExpiredResetTokensTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "seller@shop.ru", "password1234")

    def test_deletes_only_expired_tokens(self) -> None:
        with self.connect() as conn:
            fresh_token = password_reset.create_reset_token(conn, self.account_id)
            expired_token = password_reset.create_reset_token(conn, self.account_id)
            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
            conn.execute(
                "UPDATE password_reset_tokens SET expires_at=? WHERE token_hash=?",
                (expired, password_reset._hash_token(expired_token)),
            )
        with self.connect() as conn:
            removed = password_reset.delete_expired_reset_tokens(conn)
        self.assertEqual(removed, 1)
        with self.connect() as conn:
            # the still-valid token must still work after cleanup
            password_reset.consume_reset_token(conn, fresh_token, "brandnewpass1")


if __name__ == "__main__":
    import unittest
    unittest.main()
