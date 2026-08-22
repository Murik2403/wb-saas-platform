"""Unit tests for telegram_bot.py -- only the pure formatting logic and
the "not configured" fallback path. Actually polling api.telegram.org is
not exercised here (no network in this sandbox) -- verify on a real host
with a real bot token before relying on the support relay in production.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

import config  # noqa: E402
import telegram_bot  # noqa: E402


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
