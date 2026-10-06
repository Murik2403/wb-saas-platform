"""Structured (JSON) logging for the gateway.

Every existing logger.info/error/exception/warning(...) call across the
codebase keeps working unchanged -- this module only replaces *how* a log
record is rendered (JSONFormatter) and adds request_id/account_id/
tenant_slug to every record automatically via contextvars, rather than
requiring every call site to pass them explicitly.

request_id is set once per HTTP request by StructuredLoggingMiddleware.
account_id/tenant_slug are set by _current_account() in app.py/
admin_routes.py/billing_routes.py the moment a session cookie resolves to
an account -- so any log line emitted anywhere downstream of that (in
logic/*.py, provisioning.py, etc.) picks them up for free, without those
modules importing this one or knowing it exists.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from contextvars import ContextVar
from typing import Optional

request_id_ctx: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
account_id_ctx: ContextVar[Optional[str]] = ContextVar("account_id", default=None)
tenant_slug_ctx: ContextVar[Optional[str]] = ContextVar("tenant_slug", default=None)

_REDACTED = "[REDACTED]"
_BOT_TOKEN = re.compile(r"\bbot\d+(?::|%3[aA])[\w%-]+|\b\d{5,}:[\w-]{20,}", re.IGNORECASE)
_BEARER = re.compile(r"\bBearer\s+[^\s\"'<>,;}]+", re.IGNORECASE)
_SECRET_FIELD = re.compile(r"(?i)\b(authorization|api[-_]?key|client[-_]?id|password|access_token|refresh_token|bot_token|token|secret)[\"']?\s*[:=]\s*[\"']?([^\"'\r\n,;}]+)")
_SECRET_KEYS = frozenset({"authorization", "proxyauthorization", "apikey", "clientid", "password", "passwd", "secret", "token", "bottoken", "accesstoken", "refreshtoken", "cookie", "setcookie", "credentials", "encryptionkey", "dataencryptionkey", "keyring"})


def _secret_key(key) -> bool:
    text = key.decode("utf-8", errors="replace") if isinstance(key, bytes) else str(key)
    normalized = re.sub(r"[^a-z0-9]", "", text.lower())
    return normalized in _SECRET_KEYS or normalized.endswith(("password", "secret", "token", "apikey", "clientid", "encryptionkey", "encryptionkeyring"))


def redact_text(value: str) -> str:
    """Sanitize rendered messages too: HTTP libraries put credentials in URLs."""
    text = _BOT_TOKEN.sub(_REDACTED, value)
    text = _BEARER.sub("Bearer " + _REDACTED, text)
    return _SECRET_FIELD.sub(lambda match: match.group(1) + "=" + _REDACTED, text)


def redact_data(value, _seen=None, _depth=0):
    """Copy telemetry with credential keys redacted at every nesting level.

    Header pairs (Sentry sometimes uses lists) and arbitrary object reprs are
    covered too. Cyclic/overdeep extras cannot disable logging or leak values.
    """
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

# LogRecord attributes that are never useful as extra JSON fields -- either
# already surfaced explicitly below (message, levelname, ...) or internal
# bookkeeping (args, msg) that's already baked into getMessage().
_STANDARD_RECORD_ATTRS = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime",
    }
)


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = request_id_ctx.get()
        if request_id:
            payload["request_id"] = request_id
        account_id = account_id_ctx.get()
        if account_id:
            payload["account_id"] = account_id
        tenant_slug = tenant_slug_ctx.get()
        if tenant_slug:
            payload["tenant_slug"] = tenant_slug

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        extra = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_RECORD_ATTRS and not key.startswith("_")
        }
        if extra:
            payload["extra"] = extra

        return json.dumps(redact_data(payload), ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers = [handler]


def bind_account_context(account_id, tenant_slug: str | None = None) -> None:
    """Called once an incoming request's session cookie resolves to an
    account (see app.py/admin_routes.py/billing_routes.py's
    _current_account()) so every log line for the rest of this request
    carries who it was for, without threading account_id through every
    intervening function call."""
    if account_id is not None:
        account_id_ctx.set(str(account_id))
    if tenant_slug:
        tenant_slug_ctx.set(tenant_slug)


class StructuredLoggingMiddleware:
    """ASGI middleware: assigns a request_id (from an inbound X-Request-ID
    header if present, else a fresh uuid4) for every request, logs the
    outcome, and resets all three contextvars afterwards -- required
    because contextvars set during a request would otherwise leak into
    whichever request/task runs next on the same worker.

    Plain ASGI (not Starlette's BaseHTTPMiddleware) so streaming responses
    aren't buffered in memory just to log a status code.
    """

    def __init__(self, app):
        self.app = app
        self.logger = logging.getLogger("wb_saas_gateway.access")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        request_id = headers.get(b"x-request-id", b"").decode() or str(uuid.uuid4())

        request_id_token = request_id_ctx.set(request_id)
        account_id_token = account_id_ctx.set(None)
        tenant_slug_token = tenant_slug_ctx.set(None)

        status_code = 500
        start = time.perf_counter()

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers_list = list(message.get("headers", []))
                headers_list.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers_list}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            self.logger.exception(
                "%s %s failed after %sms", scope.get("method"), scope.get("path"), duration_ms
            )
            raise
        else:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            self.logger.info(
                "%s %s %s %sms", scope.get("method"), scope.get("path"), status_code, duration_ms
            )
        finally:
            request_id_ctx.reset(request_id_token)
            account_id_ctx.reset(account_id_token)
            tenant_slug_ctx.reset(tenant_slug_token)
