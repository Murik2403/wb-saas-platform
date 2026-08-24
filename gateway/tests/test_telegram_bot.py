"""Unit tests for telegram_bot.py -- only the pure formatting logic and
the "not configured" fallback path. Actually polling api.telegram.org is
not exercised here (no network in this sandbox) -- verify on a real host
with a real bot token before relying on the support relay in production.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

import config  # noqa: E402
import telegram_bot  # noqa: E402
from logic import accounts  # noqa: E402

from .base import GatewayDbTestCase  # noqa: E402


class FormatRelayTests(unittest.TestCase):
    def test_public_username_included_as_at_mention(self) -> None:
        message = {
            "from": {"first_name": "Иван", "last_name": "Петров", "username": "ivanpetrov", "id": 111},
            "text": "Как подключить токен WB?",
        }
        text = telegram_bot.format_relay(message)
        self.assertIn("Иван Петров", text)
        self.assertIn("@ivanpetrov", text)
        self.assertIn("Как подключить токен WB?", text)

    def test_missing_username_falls_back_to_numeric_id(self) -> None:
        message = {"from": {"first_name": "Аноним", "id": 222}, "text": "Привет"}
        text = telegram_bot.format_relay(message)
        self.assertIn("id 222", text)
        self.assertNotIn("@", text.split("\n")[1])  # no stray @ when there's no username

    def test_missing_name_falls_back_to_placeholder(self) -> None:
        message = {"from": {"id": 333}, "text": "Тест"}
        text = telegram_bot.format_relay(message)
        self.assertIn("Без имени", text)

    def test_non_text_message_gets_placeholder_body(self) -> None:
        message = {"from": {"first_name": "Аноним", "id": 444}, "photo": [{"file_id": "abc"}]}
        text = telegram_bot.format_relay(message)
        self.assertIn("без текста", text)


class HandleLinkCommandTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "seller@shop.ru", "password1234")
            self.slug = accounts.generate_unique_slug(conn, "seller@shop.ru")
            accounts.create_tenant_instance(conn, self.account_id, self.slug)
            self.code = accounts.create_telegram_link_code(conn, self.account_id)

    def test_valid_code_links_chat_and_replies_success(self) -> None:
        sent = {}

        def fake_call(method, **params):
            sent["method"] = method
            sent["params"] = params
            return {}

        with mock.patch.object(telegram_bot, "control_db", mock.Mock(connect=self.connect)), \
             mock.patch.object(telegram_bot, "_call", fake_call):
            telegram_bot._handle_link_command(987654321, f"/link {self.code}")

        self.assertEqual(sent["params"]["chat_id"], 987654321)
        self.assertIn("привязан", sent["params"]["text"])
        with self.connect() as conn:
            self.assertEqual(accounts.get_telegram_chat_id_for_slug(conn, self.slug), "987654321")

    def test_invalid_code_does_not_link_and_replies_failure(self) -> None:
        sent = {}

        def fake_call(method, **params):
            sent["params"] = params
            return {}

        with mock.patch.object(telegram_bot, "control_db", mock.Mock(connect=self.connect)), \
             mock.patch.object(telegram_bot, "_call", fake_call):
            telegram_bot._handle_link_command(987654321, "/link WRONGCODE")

        self.assertIn("не найден", sent["params"]["text"])
        with self.connect() as conn:
            self.assertEqual(accounts.get_telegram_chat_id_for_slug(conn, self.slug), "")

    def test_handle_update_routes_link_text_to_link_command_not_support_relay(self) -> None:
        calls = []

        def fake_call(method, **params):
            calls.append((method, params))
            return {}

        update = {"message": {"chat": {"id": 987654321}, "text": f"/link {self.code}", "from": {"id": 987654321}}}
        with mock.patch.object(telegram_bot, "control_db", mock.Mock(connect=self.connect)), \
             mock.patch.object(telegram_bot, "_call", fake_call), \
             mock.patch.object(config, "TELEGRAM_OWNER_CHAT_ID", "999"):
            telegram_bot._handle_update(update)

        # Exactly one sendMessage (the link result) -- no auto-reply, no
        # relay-to-operator, since /link is a self-service action.
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "sendMessage")
        self.assertEqual(calls[0][1]["chat_id"], 987654321)


class RunWithoutTokenConfiguredTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_token = config.TELEGRAM_BOT_TOKEN
        config.TELEGRAM_BOT_TOKEN = ""

    def tearDown(self) -> None:
        config.TELEGRAM_BOT_TOKEN = self._original_token

    def test_run_returns_immediately_without_raising(self) -> None:
        telegram_bot.run()  # must not raise, must not hang polling forever


if __name__ == "__main__":
    unittest.main()
