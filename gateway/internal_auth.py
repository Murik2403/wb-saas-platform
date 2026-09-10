"""Derive scoped tenant credentials without distributing the master key."""
import hashlib
import hmac
import re


def tenant_credential(master: str, slug: str) -> str:
    if not master or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", slug):
        return ""
    return "v1." + hmac.new(master.encode(), ("tenant-internal:" + slug).encode(), hashlib.sha256).hexdigest()


def verify_credential(master: str, slug: str, provided: str) -> bool:
    expected = tenant_credential(master, slug)
    return bool(expected) and hmac.compare_digest(expected.encode(), provided.encode())
