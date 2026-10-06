"""Reject oversized POST/PUT/PATCH bodies before any form/multipart parsing.

Checks Content-Length up front and counts streamed bytes (chunked uploads).
"""
from starlette.responses import PlainTextResponse

INTERNAL_LIMIT = 25 * 1024 * 1024
DEFAULT_LIMIT = 1024 * 1024
_METHODS = {"POST", "PUT", "PATCH"}


def limit_for(path: str) -> int:
    return INTERNAL_LIMIT if path.startswith("/internal/") else DEFAULT_LIMIT


class _TooLarge(Exception):
    pass


class BodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in _METHODS:
            await self.app(scope, receive, send)
            return
        limit = limit_for(scope["path"])
        declared = dict(scope["headers"]).get(b"content-length")
        too_big = PlainTextResponse("Request body too large", status_code=413)
        if declared is not None:
            try:
                if int(declared) > limit:
                    await too_big(scope, receive, send)
                    return
            except ValueError:
                await PlainTextResponse("Bad Content-Length", status_code=400)(scope, receive, send)
                return
        seen = 0
        started = False

        async def counting_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise _TooLarge
            return message

        async def tracking_send(message):
            nonlocal started
            started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _TooLarge:
            if not started:
                await too_big(scope, receive, send)
