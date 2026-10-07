"""Bound request headers and bodies before application parsers run.

Responses (including file streaming / byte ranges) are forwarded untouched.
Request chunks are retained only up to the route limit and replayed without
joining them into a second full-size allocation.
"""
from __future__ import annotations

DEFAULT_BODY_BYTES = 1024 * 1024
MAX_HEADER_BYTES = 32 * 1024
MAX_HEADERS = 100
MAX_BODY_CHUNKS = 4096


def body_limit(path: str, service: str) -> int:
    if service == "gateway" and path in {
        "/internal/send-report-email", "/internal/send-report-telegram",
    }:
        return 24 * 1024 * 1024
    if service == "tenant" and (
        path.startswith("/api/v1/settings/store-expenses/import/")
        or path.startswith("/api/v1/migration-import/")
        or path.startswith("/api/v1/ozon-accounting/movements/")
    ):
        return 10 * 1024 * 1024
    if service == "tenant" and path.startswith("/api/v1/incident-workflow/"):
        return 4 * 1024 * 1024
    # Marketplace selection grants permit up to 2 MiB, as its route contract.
    if service == "tenant" and path.startswith("/api/v1/marketplace-context/"):
        return 2 * 1024 * 1024
    return DEFAULT_BODY_BYTES


async def reject(send, status: int):
    body = b"Request headers too large" if status == 431 else b"Request body too large"
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                            (b"cache-control", b"no-store"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class RequestLimitsMiddleware:
    def __init__(self, app, *, service: str):
        self.app = app
        self.service = service

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = scope.get("headers", [])
        if len(headers) > MAX_HEADERS or sum(len(k) + len(v) + 4 for k,v in headers) > MAX_HEADER_BYTES:
            return await reject(send, 431)
        limit = body_limit(scope.get("path", ""), self.service)
        lengths = [value for key,value in headers if key.lower() == b"content-length"]
        if len(lengths) > 1:
            return await reject(send, 413)
        if lengths:
            try:
                if not lengths[0].isdigit() or int(lengths[0]) > limit:
                    return await reject(send, 413)
            except (ValueError, OverflowError):
                return await reject(send, 413)
        chunks = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                return await reject(send, 413)
            total += len(message.get("body", b""))
            if total > limit or len(chunks) >= MAX_BODY_CHUNKS:
                return await reject(send, 413)
            chunks.append(message)
            if not message.get("more_body", False):
                break
        iterator = iter(chunks)
        async def replay():
            return next(iterator, None) or await receive()
        await self.app(scope, replay, send)


def development_cors_origins(tenant_slug: str, enabled: str) -> list[str]:
    if tenant_slug or str(enabled).strip() != "1":
        return []
    return ["http://localhost:3000", "http://127.0.0.1:3000",
            "http://localhost:3001", "http://127.0.0.1:3001"]
