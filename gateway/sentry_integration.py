"""Opt-in Sentry setup; remains a no-op until WB_SAAS_SENTRY_DSN is set."""
from __future__ import annotations

import logging

import config

logger = logging.getLogger("wb_saas_gateway.sentry")


def initialize() -> None:
    if not config.SENTRY_DSN:
        return
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=config.SENTRY_DSN,
            environment=config.SENTRY_ENVIRONMENT,
            send_default_pii=False,
        )
        sentry_sdk.set_tag("service", "gateway")
    except Exception:
        logger.exception("Sentry initialization failed; gateway will continue without error reporting")
