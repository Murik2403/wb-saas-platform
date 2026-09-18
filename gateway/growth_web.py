"""Consent-gated, signed acquisition metadata for public pages.

No anonymous visitor identifier and no raw URL/referrer is persisted. A
missing signing secret disables optional analytics instead of silently
accepting client-controlled cookies. Lifecycle facts remain server-owned.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

import config

CONSENT_COOKIE = "mh_analytics_consent"
SOURCE_COOKIE = "mh_acquisition"
CONSENT_VERSION = 2
SOURCE_TTL = 30 * 86400
CONSENT_TTL = 180 * 86400
UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
PUBLIC_ANALYTICS_PATHS = {
    "/", "/pricing", "/dlya-proizvoditeley-wb", "/sebestoimost-wb",
    "/fifo-uchet", "/calculator", "/guides", "/guides/fifo-po-partiyam",
    "/guides/pribyl-i-rashody-wb", "/guides/iz-excel-v-uchet",
}
router = APIRouter()


def _secret() -> bytes:
    value = config.GROWTH_COOKIE_SECRET or config.INTERNAL_API_SECRET
    return value.encode("utf-8") if value else b""


def optional_analytics_available() -> bool:
    return bool(_secret())


def _sign(payload: dict, purpose: str, ttl: int) -> str:
    key = _secret()
    if not key:
        return ""
    envelope = {"v": CONSENT_VERSION, "exp": int(time.time()) + ttl, **payload}
    raw = base64.urlsafe_b64encode(json.dumps(envelope, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(key, f"growth:{purpose}:{raw}".encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{signature}"


def _read(value: str, purpose: str) -> dict:
    key = _secret()
    if not key or not value or len(value) > 4096:
        return {}
    try:
        raw, signature = value.rsplit(".", 1)
        expected = hmac.new(key, f"growth:{purpose}:{raw}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return {}
        data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        if not isinstance(data, dict) or data.get("v") != CONSENT_VERSION or data.get("exp", 0) <= time.time():
            return {}
        return data
    except (ValueError, TypeError, UnicodeError):
        return {}


def consent_choice(request: Request) -> str:
    choice = _read(request.cookies.get(CONSENT_COOKIE, ""), "consent").get("choice", "")
    return choice if choice in {"analytics", "essential"} else ""


def metrika_counter(request: Request) -> str:
    counter = str(config.YANDEX_METRIKA_ID)
    if optional_analytics_available() and request.url.path in PUBLIC_ANALYTICS_PATHS and counter.isdigit():
        return counter
    return ""


def _source(values) -> dict[str, str]:
    from logic.growth import sanitize_attribution
    return sanitize_attribution({key: values.get(key, "") for key in UTM_KEYS})


def _set_cookie(response, name: str, value: str, ttl: int) -> None:
    response.set_cookie(name, value, max_age=ttl, httponly=True, secure=config.COOKIE_SECURE, samesite="lax", path="/")


def registration_source(request: Request) -> tuple[dict, datetime | None]:
    if consent_choice(request) != "analytics":
        return {}, None
    data = _read(request.cookies.get(SOURCE_COOKIE, ""), "source")
    try:
        touched_at = datetime.fromtimestamp(float(data["touched_at"]), timezone.utc)
    except (KeyError, TypeError, ValueError, OverflowError):
        return {}, None
    if touched_at.timestamp() > time.time() + 60 or time.time() - touched_at.timestamp() > SOURCE_TTL:
        return {}, None
    return _source(data.get("utm", {})), touched_at


class AcquisitionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.method == "GET" and "text/html" in response.headers.get("content-type", ""):
            # Consent-specific markup must never be reused by a shared cache.
            response.headers["Cache-Control"] = "private, no-store"
        if request.method == "GET" and request.url.path in PUBLIC_ANALYTICS_PATHS | {"/register"} and response.status_code == 200 and consent_choice(request) == "analytics":
            source = _source(request.query_params)
            if source.get("utm_source"):
                _set_cookie(response, SOURCE_COOKIE, _sign({"utm": source, "touched_at": time.time()}, "source", SOURCE_TTL), SOURCE_TTL)
        return response


@router.post("/privacy/analytics-consent")
async def save_consent(request: Request):
    # JSON + exact same origin blocks cross-site form submission. No CORS
    # permission is exposed by the gateway for this endpoint.
    origin = request.headers.get("origin", "").rstrip("/")
    allowed = {str(request.base_url).rstrip("/"), config.base_url().rstrip("/")}
    if origin not in allowed or urlsplit(origin).scheme not in {"http", "https"}:
        raise HTTPException(403, "same-origin request required")
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
        raise HTTPException(415, "JSON required")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 2048:
            raise HTTPException(413, "request too large")
        body.extend(chunk)
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(400, "invalid JSON")
    if not isinstance(payload, dict) or payload.get("choice") not in {"analytics", "essential"}:
        raise HTTPException(400, "invalid choice")
    if not optional_analytics_available():
        raise HTTPException(503, "optional analytics is disabled")
    choice = payload["choice"]
    response = JSONResponse({"choice": choice, "version": CONSENT_VERSION})
    response.headers["Cache-Control"] = "no-store"
    _set_cookie(response, CONSENT_COOKIE, _sign({"choice": choice}, "consent", CONSENT_TTL), CONSENT_TTL)
    if choice == "essential":
        response.delete_cookie(SOURCE_COOKIE, path="/")
    else:
        source = _source(payload)
        if source.get("utm_source"):
            _set_cookie(response, SOURCE_COOKIE, _sign({"utm": source, "touched_at": time.time()}, "source", SOURCE_TTL), SOURCE_TTL)
    return response
