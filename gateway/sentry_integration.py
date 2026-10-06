"""Opt-in Sentry setup; remains a no-op until WB_SAAS_SENTRY_DSN is set."""
from __future__ import annotations

import logging

import config
from logging_config import redact_data

logger = logging.getLogger("wb_saas_gateway.sentry")


def before_send(event, hint):
    return redact_data(event)


def before_breadcrumb(breadcrumb, hint):
    return redact_data(breadcrumb)


def initialize() -> None:
    if not config.SENTRY_DSN:
        return
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=config.SENTRY_DSN,
            environment=config.SENTRY_ENVIRONMENT,
            send_default_pii=False,
            include_local_variables=False,
            before_send=before_send,
            before_send_transaction=before_send,
            before_breadcrumb=before_breadcrumb,
        )
        sentry_sdk.set_tag("service", "gateway")
    except Exception:
        logger.exception("Sentry initialization failed; gateway will continue without error reporting")
