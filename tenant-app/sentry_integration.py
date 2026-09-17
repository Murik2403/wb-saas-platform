"""Opt-in Sentry setup for one tenant dashboard instance."""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("marketshelper.tenant.sentry")


def initialize(service_name: str = "tenant-app") -> None:
    dsn = os.environ.get("WB_SAAS_SENTRY_DSN", "")
    if not dsn:
        return
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=dsn,
            environment=os.environ.get("WB_SAAS_SENTRY_ENVIRONMENT", "production"),
            send_default_pii=False,
        )
        sentry_sdk.set_tag("service", service_name)
    except Exception:
        logger.exception("Sentry initialization failed; tenant dashboard will continue without error reporting")
