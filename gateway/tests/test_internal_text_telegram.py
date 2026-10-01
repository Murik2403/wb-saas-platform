"""POST /internal/send-text-telegram: short plain-text notification to the linked chat."""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException

import config
import internal_routes
import telegram_bot
from internal_auth import tenant_credential
from logic import accounts

from .base import GatewayDbTestCase


class SendTextTelegramTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "owner@shop.ru", "password1234")
            self.slug = accounts.generate_unique_slug(conn, "owner@shop.ru")
            accounts.create_tenant_instance(conn, self.account_id, self.slug)
        patch = mock.patch.object(internal_routes, "control_db", mock.Mock(connect=self.connect))
        patch.start()
        self.addCleanup(patch.stop)

    def link(self) -> None:
        with self.connect() as conn:
            code = accounts.create_telegram_link_code(conn, self.account_id)
            accounts.consume_telegram_link_code(conn, code, "555")

    def call(self, text="Привет", secret=None, slug=None):
        slug = slug or self.slug
        request = SimpleNamespace(headers={"x-internal-secret": secret if secret is not None else tenant_credential("s3cr3t", slug)})
        with mock.patch.object(config, "INTERNAL_API_SECRET", "s3cr3t"):
            return internal_routes.send_text_telegram(request, slug=slug, text=text)

    def test_wrong_secret_is_403_and_nothing_sent(self) -> None:
        self.link()
        with mock.patch.object(telegram_bot, "send_message") as send:
            with self.assertRaises(HTTPException) as ctx:
                self.call(secret="wrong")
        self.assertEqual(ctx.exception.status_code, 403)
        send.assert_not_called()

    def test_unknown_slug_is_404(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self.call(slug="nope")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_unlinked_is_409(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self.call()
        self.assertEqual(ctx.exception.status_code, 409)

    def test_empty_text_is_400(self) -> None:
        self.link()
        with self.assertRaises(HTTPException) as ctx:
            self.call(text="   ")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_success_sends_to_linked_chat_and_caps_length(self) -> None:
        self.link()
        with mock.patch.object(telegram_bot, "send_message", return_value={"ok": True}) as send:
            response = self.call(text="я" * 5000)
        self.assertEqual(response.status_code, 200)
        chat_id, text = send.call_args.args
        self.assertEqual(chat_id, "555")
        self.assertEqual(len(text), internal_routes.MAX_NOTIFY_TEXT_CHARS)

    def test_telegram_error_and_exception_are_502(self) -> None:
        self.link()
        for effect in ({"return_value": {"ok": False}}, {"side_effect": RuntimeError("boom")}):
            with mock.patch.object(telegram_bot, "send_message", **effect):
                with self.assertRaises(HTTPException) as ctx:
                    self.call()
            self.assertEqual(ctx.exception.status_code, 502)

    def test_send_message_posts_plain_text_without_parse_mode(self) -> None:
        with mock.patch.object(telegram_bot.requests, "post") as post:
            post.return_value.json.return_value = {"ok": True}
            telegram_bot.send_message("555", "<b>x</b>")
        self.assertEqual(post.call_args.kwargs["json"], {"chat_id": "555", "text": "<b>x</b>"})
        self.assertEqual(post.call_args.kwargs["timeout"], 15)
