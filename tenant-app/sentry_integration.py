"""Opt-in Sentry setup for one tenant dashboard instance."""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("marketshelper.tenant.sentry")

# The gateway and tenant are separate images; keep this standalone policy in
# sync with gateway.logging_config (cross-runtime regression tests cover both).
_REDACTED = "[REDACTED]"
_BOT_TOKEN = re.compile(r"\bbot\d+(?::|%3[aA])[\w%-]+|\b\d{5,}:[\w-]{20,}", re.IGNORECASE)
_BEARER = re.compile(r"\bBearer\s+[^\s\"'<>,;}]+", re.IGNORECASE)
_SECRET_FIELD = re.compile(r"(?i)\b(authorization|x[-_]?mh[-_]?gateway[-_]?auth|api[-_]?key|client[-_]?id|password|access_token|refresh_token|bot_token|token|secret)[\"']?\s*[:=]\s*[\"']?([^\"'\r\n,;}]+)")
_SECRET_KEYS = frozenset({"authorization", "proxyauthorization", "xmhgatewayauth", "apikey", "clientid", "password", "passwd", "secret", "token", "bottoken", "accesstoken", "refreshtoken", "cookie", "setcookie", "credentials", "encryptionkey", "dataencryptionkey", "keyring"})


def _secret_key(key):
    text = key.decode("utf-8", errors="replace") if isinstance(key, bytes) else str(key)
    normalized = re.sub(r"[^a-z0-9]", "", text.lower())
    return normalized in _SECRET_KEYS or normalized.endswith(("password", "secret", "token", "apikey", "clientid", "encryptionkey", "encryptionkeyring"))


def redact_text(value):
    text = _BOT_TOKEN.sub(_REDACTED, value)
    text = _BEARER.sub("Bearer " + _REDACTED, text)
    return _SECRET_FIELD.sub(lambda match: match.group(1) + "=" + _REDACTED, text)


def redact_data(value, _seen=None, _depth=0):
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    seen = set() if _seen is None else _seen
    if _depth >= 30 or id(value) in seen:
        return _REDACTED
    seen.add(id(value))
    try:
        if isinstance(value, dict):
            return {redact_text(str(key)): _REDACTED if _secret_key(key) else redact_data(item, seen, _depth + 1) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            if len(value) == 2 and isinstance(value[0], (str, bytes)) and _secret_key(value[0]):
                return [str(value[0]), _REDACTED]
            return [redact_data(item, seen, _depth + 1) for item in value]
        return redact_text(str(value))
    finally:
        seen.remove(id(value))


def before_send(event, hint):
    return redact_data(event)


def before_breadcrumb(breadcrumb, hint):
    return redact_data(breadcrumb)


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
            include_local_variables=False,
            before_send=before_send,
            before_send_transaction=before_send,
            before_breadcrumb=before_breadcrumb,
        )
        sentry_sdk.set_tag("service", service_name)
    except Exception:
        logger.exception("Sentry initialization failed; tenant dashboard will continue without error reporting")
