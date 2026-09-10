"""Resolve a client address from Traefik's sanitized forwarding chain."""
from __future__ import annotations

from fastapi import Request

import config


def client_ip(request: Request) -> str:
    forwarded = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
    if len(forwarded) > config.TRUSTED_PROXY_HOPS:
        return forwarded[-(config.TRUSTED_PROXY_HOPS + 1)]
    return request.client.host if request.client else "unknown"
