"""Report delivery beyond the in-dashboard download button.

Both email and Telegram go through the gateway's internal API (see
gateway/internal_routes.py) rather than talking to SMTP/the Telegram Bot
API directly from here, because this container has no SMTP/bot credentials
of its own and is deliberately not on the gateway's docker network
(per-tenant network isolation) -- the only path back to the gateway is its
public HTTPS domain, authenticated with a shared secret baked into this
container's environment at provision time.
"""
from __future__ import annotations

import logging

import requests

import config

logger = logging.getLogger("wb_dashboard.reports.delivery")


def internal_api_is_configured() -> bool:
    return bool(config.INTERNAL_API_URL and config.INTERNAL_API_SECRET and config.TENANT_SLUG)


# Kept as a separate name (rather than callers using internal_api_is_configured
# directly) so the UI/scheduler read as "is email available", not "is the
# plumbing available" -- today they happen to be the same check, but email
# delivery specifically could grow its own precondition later (e.g. a
# per-tenant opt-out) without every call site needing to change.
email_is_configured = internal_api_is_configured


def _post_to_gateway(path: str, report_name: str, pdf_bytes: bytes, filename: str, timeout: int = 30) -> bool:
    response = requests.post(
        f"{config.INTERNAL_API_URL}{path}",
        headers={"X-Internal-Secret": config.INTERNAL_API_SECRET},
        data={"slug": config.TENANT_SLUG, "report_name": report_name},
        files={"file": (filename, pdf_bytes, "application/pdf")},
        timeout=timeout,
    )
    if response.status_code != 200:
        logger.error("Gateway rejected %s: %s %s", path, response.status_code, response.text[:500])
        return False
    return True


def send_report_email(report_name: str, pdf_bytes: bytes, filename: str) -> bool:
    """Returns False (never raises) on any failure -- a delivery hiccup must
    not break the scheduled generation run itself; the PDF is already saved
    to disk and downloadable regardless of whether email delivery worked."""
    if not internal_api_is_configured():
        logger.warning("Email delivery requested but gateway internal API is not configured -- skipping.")
        return False
    try:
        return _post_to_gateway("/internal/send-report-email", report_name, pdf_bytes, filename)
    except Exception:
        logger.exception("Failed to reach gateway internal API for report email delivery")
        return False


def telegram_is_linked() -> bool:
    """False both when unconfigured and when configured-but-not-linked --
    callers only ever need "can I send to Telegram right now", not why not."""
    if not internal_api_is_configured():
        return False
    try:
        response = requests.get(
            f"{config.INTERNAL_API_URL}/internal/telegram-status",
            headers={"X-Internal-Secret": config.INTERNAL_API_SECRET},
            params={"slug": config.TENANT_SLUG},
            timeout=15,
        )
        if response.status_code != 200:
            return False
        return bool(response.json().get("linked"))
    except Exception:
        logger.exception("Failed to reach gateway internal API for Telegram link status")
        return False


def request_telegram_link_code() -> tuple[str, int] | None:
    """Returns (code, ttl_minutes) on success, None on any failure -- the
    caller (reports_page.py) shows an error message in that case."""
    if not internal_api_is_configured():
        return None
    try:
        response = requests.post(
            f"{config.INTERNAL_API_URL}/internal/telegram-link-code",
            headers={"X-Internal-Secret": config.INTERNAL_API_SECRET},
            data={"slug": config.TENANT_SLUG},
            timeout=15,
        )
        if response.status_code != 200:
            logger.error("Gateway rejected telegram-link-code request: %s %s", response.status_code, response.text[:500])
            return None
        payload = response.json()
        return payload["code"], int(payload["ttl_minutes"])
    except Exception:
        logger.exception("Failed to reach gateway internal API for Telegram link code")
        return None


def send_report_telegram(report_name: str, pdf_bytes: bytes, filename: str) -> bool:
    """Same never-raises contract as send_report_email.

    Longer timeout than the email path: the gateway's own call out to the
    Telegram Bot API (telegram_bot.send_document) uses a 60s timeout on a
    network path documented as intermittently slow (see telegram_bot.py's
    module docstring) -- this must stay comfortably above that, or a
    request that's still succeeding server-side gets reported as a failure
    here just because this client gave up first."""
    if not internal_api_is_configured():
        logger.warning("Telegram delivery requested but gateway internal API is not configured -- skipping.")
        return False
    try:
        return _post_to_gateway("/internal/send-report-telegram", report_name, pdf_bytes, filename, timeout=90)
    except Exception:
        logger.exception("Failed to reach gateway internal API for report Telegram delivery")
        return False
