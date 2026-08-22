"""System notification helper via Telegram Bot API.

Used to send real-time operational alerts (provisioning failures, billing errors,
backup issues) to an admin Telegram chat.
"""
from __future__ import annotations

import logging
import os
import urllib.parse
import urllib.request

logger = logging.getLogger("wb_saas_gateway.telegram")

TELEGRAM_BOT_TOKEN = os.environ.get("WB_SAAS_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("WB_SAAS_TELEGRAM_CHAT_ID", "")


def send_telegram_alert(message: str) -> bool:
    """Sends a text message to the configured admin Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.debug("Telegram notification skipped: BOT_TOKEN or CHAT_ID not set")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": f"🚨 [MARKETSHELPER Alert]\n\n{message}",
        "parse_mode": "HTML",
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                logger.info("Telegram alert sent successfully")
                return True
            logger.warning("Telegram API returned status %d", resp.status)
    except Exception as exc:
        logger.error("Failed to send Telegram alert: %s", exc)

    return False
