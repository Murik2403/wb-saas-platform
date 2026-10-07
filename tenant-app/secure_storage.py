"""Authenticated token-file storage for SaaS tenant volumes."""
from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

PREFIX = "enc:v1:"


def _fernet(key: str) -> Fernet:
    if not key:
        raise RuntimeError("WB_SAAS_DATA_ENCRYPTION_KEY is required for tenant credential storage")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError, UnicodeError) as exc:
        raise RuntimeError("WB_SAAS_DATA_ENCRYPTION_KEY has an invalid format") from exc


def write(path: Path, value: str, key: str) -> None:
    ciphertext = PREFIX + _fernet(key).encrypt(value.encode("utf-8")).decode("ascii")
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(ciphertext, encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)


def read(path: Path, key: str) -> tuple[str | None, bool]:
    """Return ``(value, migrated)``; unprefixed data is the legacy format."""
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None, False
    if not raw:
        return None, False
    if not raw.startswith(PREFIX):
        # A legacy token is usable only when its immediate migration can use
        # the existing valid key. Never fall back to plaintext without a key.
        _fernet(key)
        return raw, True
    try:
        return _fernet(key).decrypt(raw[len(PREFIX):].encode("ascii")).decode("utf-8"), False
    except InvalidToken as exc:
        raise RuntimeError("Credential file cannot be authenticated or decrypted") from exc
