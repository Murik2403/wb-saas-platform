"""Small browser policy for gateway documents, without buffering media.

Framing applies to this document's ancestors, not its outgoing demo iframe.
Script/style/media source restrictions need a separate nonce/analytics inventory;
do not accidentally disable the inline theme boot script or public video lessons.
TLS/HSTS is managed at the reverse proxy, not inferred from client headers here.
"""
from starlette.datastructures import MutableHeaders


HTML_POLICY = "frame-ancestors 'self'; object-src 'none'; base-uri 'self'"


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def protected_send(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": list(message.get("headers", []))}
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type in {"text/html", "application/xhtml+xml"}:
                    headers.setdefault("X-Frame-Options", "SAMEORIGIN")
                    headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                    # Multiple CSP headers are all enforced: keep a route's
                    # stricter policy while also applying the common baseline.
                    headers.append("Content-Security-Policy", HTML_POLICY)
            await send(message)

        await self.app(scope, receive, protected_send)
