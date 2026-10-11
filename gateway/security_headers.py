"""Nonce-based browser policy for gateway documents, without buffering media.

Framing applies to this document's ancestors, not its outgoing demo iframe.
Trusted Jinja script tags share an unpredictable response nonce. Dynamic scripts
inherit trust; inline event attributes and eval are not authorized. Style, media
and outgoing frame sources are unaffected.
HSTS is emitted only for HTTPS scopes; the reverse proxy establishes the scheme.
"""
import secrets
from starlette.datastructures import MutableHeaders


HTML_POLICY = "frame-ancestors 'self'; object-src 'none'; base-uri 'self'"


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Populate before Jinja rendering. Never reuse a client-provided value or
        # derive this nonce from an account, cookie, timestamp or cached HTML.
        nonce = secrets.token_urlsafe(24)
        scope.setdefault("state", {})["csp_nonce"] = nonce
        policy = (HTML_POLICY + f"; script-src 'nonce-{nonce}' 'strict-dynamic'; "
                  "script-src-attr 'none'")

        async def protected_send(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": list(message.get("headers", []))}
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                if scope.get("scheme") == "https":
                    headers.setdefault("Strict-Transport-Security", "max-age=31536000")
                content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type in {"text/html", "application/xhtml+xml"}:
                    headers.setdefault("X-Frame-Options", "SAMEORIGIN")
                    headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                    # Multiple CSP headers are all enforced: keep a route's
                    # stricter policy while also applying the common baseline.
                    headers.append("Content-Security-Policy", policy)
            await send(message)

        await self.app(scope, receive, protected_send)
