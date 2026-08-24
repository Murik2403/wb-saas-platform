"""Minimal Telegram support relay -- no bot framework, just plain long
polling against the Bot API (same "boring is fine" reasoning as
mailer.py: this is the entire feature, not something worth a dependency
for).

Flow: a customer messages the bot -> gets an immediate canned reply ->
their message is relayed to the operator's own Telegram
(TELEGRAM_OWNER_CHAT_ID) with enough context (name, @username if public,
numeric id) that the operator can just open a normal DM with them and
answer directly. This bot is the doorbell, not the actual support
channel -- there is deliberately no reply-through-the-bot flow.

If TELEGRAM_BOT_TOKEN is empty, run() returns immediately (same
graceful-no-op pattern as mailer.send_email with no SMTP_HOST configured)
so the gateway container doesn't need Telegram set up to start.
"""
from __future__ import annotations

import logging
import time

import requests

import config
from logic import accounts, db as control_db

# Note on connectivity: this host's network path to api.telegram.org over
# plain IPv4 was observed to be unreliable (confirmed live: connect()
# timeouts straight from the bare host, not just this container) --
# apparently intermittent, not a hard block. wbsaas_net is dual-stack
# (see docker-compose.yml) specifically so getaddrinfo()'s default
# ordering (IPv6 first) gives this process a second path to fall back on;
# don't force either family here, since forcing IPv4 removes exactly the
# fallback that makes this resilient to that intermittency.

logger = logging.getLogger("wb_saas_gateway.telegram_bot")

API_URL = "https://api.telegram.org/bot{token}/{method}"

AUTO_REPLY = (
    "Спасибо за сообщение! Это служба поддержки MARKETSHELPER — "
    "мы отвечаем в этом же чате в течение нескольких часов. "
    "Опишите ваш вопрос, и мы свяжемся с вами."
)


def _call(method: str, **params) -> dict:
    url = API_URL.format(token=config.TELEGRAM_BOT_TOKEN, method=method)
    response = requests.post(url, json=params, timeout=35)
    response.raise_for_status()
    return response.json()


def send_document(chat_id: str, file_bytes: bytes, filename: str, caption: str = "") -> dict:
    """Separate from _call() because sendDocument needs multipart/form-data
    (a file part), not the plain JSON body every other method here uses."""
    url = API_URL.format(token=config.TELEGRAM_BOT_TOKEN, method="sendDocument")
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
    files = {"document": (filename, file_bytes, "application/pdf")}
    response = requests.post(url, data=data, files=files, timeout=60)
    response.raise_for_status()
    return response.json()


def format_relay(message: dict) -> str:
    """Pure formatting, kept separate from _call so it's testable without
    a network mock."""
    sender = message.get("from") or {}
    name = " ".join(filter(None, [sender.get("first_name"), sender.get("last_name")])) or "Без имени"
    username = sender.get("username")
    contact = f"@{username}" if username else f"id {sender.get('id')} (нет публичного юзернейма — ответить можно только через самого бота)"
    text = message.get("text") or "<сообщение без текста: фото/файл/стикер и т.п.>"
    return f"Новое сообщение в поддержку MARKETSHELPER\nОт: {name} ({contact})\n\n{text}"


LINK_SUCCESS_REPLY = (
    "Готово! Этот чат привязан к вашему аккаунту MARKETSHELPER — отчёты с "
    "включённой доставкой в Telegram (страница «Отчёты» в кабинете) теперь "
    "будут приходить сюда."
)


def _link_failure_reply() -> str:
    return (
        "Код не найден, уже использован или истёк "
        f"(код действует {accounts.TELEGRAM_LINK_CODE_TTL_MINUTES} минут). "
        "Получите новый код на странице «Отчёты» в кабинете и отправьте его "
        "снова: /link <код>"
    )


def _handle_link_command(chat_id, text: str) -> None:
    """/link <code> is how a user proves "I am this account" to the bot --
    Telegram chat ids carry no account/email identity on their own, so this
    one-time code (generated on the dashboard, see
    logic/accounts.create_telegram_link_code) is the only bridge between the
    two. Never relayed to the operator -- this is a self-service action, not
    a support message."""
    parts = text.split(maxsplit=1)
    code = parts[1].strip() if len(parts) > 1 else ""
    with control_db.connect() as conn:
        ok = accounts.consume_telegram_link_code(conn, code, str(chat_id))
    reply = LINK_SUCCESS_REPLY if ok else _link_failure_reply()
    try:
        _call("sendMessage", chat_id=chat_id, text=reply)
    except Exception:
        logger.exception("Failed to send Telegram /link result to chat %s", chat_id)


def _handle_update(update: dict) -> None:
    message = update.get("message")
    if not message:
        return
    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()
    if text.startswith("/link"):
        _handle_link_command(chat_id, text)
        return
    try:
        _call("sendMessage", chat_id=chat_id, text=AUTO_REPLY)
    except Exception:
        logger.exception("Failed to send Telegram auto-reply to chat %s", chat_id)
    if config.TELEGRAM_OWNER_CHAT_ID:
        try:
            _call("sendMessage", chat_id=config.TELEGRAM_OWNER_CHAT_ID, text=format_relay(message))
        except Exception:
            logger.exception("Failed to relay Telegram message to owner")
    else:
        logger.warning("WB_SAAS_TELEGRAM_OWNER_CHAT_ID not set -- message received but not relayed to anyone.")


def run() -> None:
    """Long-polls getUpdates forever. Never raises -- a Telegram outage or
    misconfiguration must not take the gateway process down with it (this
    runs as a sibling process to uvicorn, see docker-entrypoint.sh)."""
    if not config.TELEGRAM_BOT_TOKEN:
        logger.warning("WB_SAAS_TELEGRAM_BOT_TOKEN is not configured -- Telegram support relay disabled.")
        return
    logger.info("Telegram support relay started.")
    offset = None
    while True:
        try:
            params = {"timeout": 30}
            if offset is not None:
                params["offset"] = offset
            result = _call("getUpdates", **params)
            for update in result.get("result", []):
                offset = update["update_id"] + 1
                _handle_update(update)
        except Exception:
            logger.exception("Telegram polling loop error, retrying in 5s")
            time.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
