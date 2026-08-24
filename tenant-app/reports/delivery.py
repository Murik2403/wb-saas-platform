"""Report delivery beyond the in-dashboard download button.

Email goes through the gateway's internal API (see
gateway/internal_routes.py) rather than straight SMTP from here, because
this container has no SMTP credentials of its own and is deliberately not
on the gateway's docker network (per-tenant network isolation) -- the only
path back to the gateway is its public HTTPS domain, authenticated with a
shared secret baked into this container's environment at provision time.
"""
from __future__ import annotations

import logging

import requests

import config

logger = logging.getLogger("wb_dashboard.reports.delivery")


def email_is_configured() -> bool:
    return bool(config.INTERNAL_API_URL and config.INTERNAL_API_SECRET and config.TENANT_SLUG)


def send_report_email(report_name: str, pdf_bytes: bytes, filename: str) -> bool:
    """Returns False (never raises) on any failure -- a delivery hiccup must
    not break the scheduled generation run itself; the PDF is already saved
    to disk and downloadable regardless of whether email delivery worked."""
    if not email_is_configured():
        logger.warning("Email delivery requested but gateway internal API is not configured -- skipping.")
        return False
    try:
        response = requests.post(
            f"{config.INTERNAL_API_URL}/internal/send-report-email",
            headers={"X-Internal-Secret": config.INTERNAL_API_SECRET},
            data={"slug": config.TENANT_SLUG, "report_name": report_name},
            files={"file": (filename, pdf_bytes, "application/pdf")},
            timeout=30,
        )
        if response.status_code != 200:
            logger.error("Gateway rejected report email: %s %s", response.status_code, response.text[:500])
            return False
        return True
    except Exception:
        logger.exception("Failed to reach gateway internal API for report email delivery")
        return False
