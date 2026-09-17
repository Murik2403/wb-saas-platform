"""Resolve a client address from Traefik's sanitized forwarding chain."""
from __future__ import annotations

import ipaddress

from fastapi import Request

import config


def _parse_forwarded_for(value: str) -> list[str]:
    candidates: list[str] = []
    for part in value.split(","):
        stripped = part.strip()
        if not stripped:
            continue
        try:
            ipaddress.ip_address(stripped)
        except ValueError:
            # Ignore malformed values (can appear in malformed XFF headers) so
            # they don't become the client identifier under any indexing rule.
            continue
        candidates.append(stripped)
    return candidates


def client_ip(request: Request) -> str:
    forwarded = _parse_forwarded_for(request.headers.get("x-forwarded-for", ""))
    if len(forwarded) > config.TRUSTED_PROXY_HOPS:
        return forwarded[-(config.TRUSTED_PROXY_HOPS + 1)]
    return request.client.host if request.client else "unknown"
