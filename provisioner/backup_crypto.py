"""Streaming authenticated encryption for host-side tenant archives."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from collections.abc import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC_V1 = b"WBSAASBK1"
MAGIC = b"WBSAASBK2"
DEFAULT_KEY_ID = "legacy"
MAX_KEYRING_KEYS = 16
MAX_KEYRING_BYTES = 65536
_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z", re.ASCII)
SALT_SIZE = 16
NONCE_SIZE = 12
TAG_SIZE = 16
CHUNK_SIZE = 1024 * 1024


def validate_key_id(value: str) -> str:
    if not isinstance(value, str) or not _KEY_ID.fullmatch(value):
        raise ValueError("Backup key ID must be 1-64 ASCII letters, digits, dots, underscores or hyphens")
    return value


def _validate_keyring(value: Mapping | None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or len(value) > MAX_KEYRING_KEYS:
        raise ValueError("Backup keyring must contain at most 16 identified keys")
    result = {}
    size = 0
    for identifier, secret in value.items():
        validate_key_id(identifier)
        if not isinstance(secret, str) or not 32 <= len(secret) <= 4096:
            raise ValueError("Backup keyring secrets must contain 32-4096 characters")
        size += len(identifier) + len(secret.encode("utf-8"))
        if size > MAX_KEYRING_BYTES:
            raise ValueError("Backup keyring configuration is too large")
        result[identifier] = secret
    return result


def parse_keyring(raw: str) -> dict[str, str]:
    """Parse a bounded operator-provided mapping without exposing secret values."""
    if not raw:
        return {}
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_KEYRING_BYTES:
        raise ValueError("Backup keyring configuration is too large")
    def unique(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError("Duplicate backup key ID")
            result[name] = value
        return result
    try:
        parsed = json.loads(raw, object_pairs_hook=unique)
        if not isinstance(parsed, dict):
            raise ValueError("Backup keyring must be an object")
        return _validate_keyring(parsed)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("Invalid backup keyring configuration") from None


def decryption_options_from_environment() -> dict:
    """Keep the existing three-argument API when no rotation config is present."""
    result = {}
    identifier = os.environ.get("WB_SAAS_BACKUP_ENCRYPTION_KEY_ID", "")
    raw = os.environ.get("WB_SAAS_BACKUP_ENCRYPTION_KEYRING", "")
    legacy = os.environ.get("WB_SAAS_BACKUP_LEGACY_KEY_ID", "")
    if identifier:
        result["key_id"] = validate_key_id(identifier)
    if raw:
        result["keyring"] = parse_keyring(raw)
    if legacy:
        result["legacy_key_id"] = validate_key_id(legacy)
    return result


def _key(secret: str, salt: bytes) -> bytes:
    if len(secret) < 32:
        raise ValueError("Backup encryption key must contain at least 32 characters")
    return hashlib.scrypt(secret.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)


def encrypt_file(source: Path, destination: Path, secret: str, *, key_id: str = DEFAULT_KEY_ID) -> None:
    """Write AES-256-GCM ciphertext atomically; source is never loaded wholly."""
    salt, nonce = os.urandom(SALT_SIZE), os.urandom(NONCE_SIZE)
    identifier = validate_key_id(key_id).encode("ascii")
    header = MAGIC + bytes([len(identifier)]) + identifier + salt + nonce
    encryptor = Cipher(algorithms.AES(_key(secret, salt)), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    temp = destination.with_name(f".{destination.name}.tmp")
    destination.parent.mkdir(parents=True, exist_ok=True)
    created=False
    deadline=time.monotonic()+120
    try:
        descriptor=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        created=True
        with os.fdopen(descriptor,"wb") as outgoing, source.open("rb") as incoming:
            outgoing.write(header)
            while chunk := incoming.read(CHUNK_SIZE):
                if time.monotonic()>deadline:raise TimeoutError('Backup encryption exceeded its deadline')
                outgoing.write(encryptor.update(chunk))
            outgoing.write(encryptor.finalize())
            outgoing.write(encryptor.tag)
            outgoing.flush(); os.fsync(outgoing.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, destination)
    finally:
        if created:temp.unlink(missing_ok=True)


def decrypt_file(source: Path, destination: Path, secret: str, *, key_id: str = DEFAULT_KEY_ID,
                 keyring: Mapping[str, str] | None = None, legacy_key_id: str | None = None) -> None:
    """Verify and decrypt one archive atomically; wrong keys leave no output."""
    validate_key_id(key_id)
    ring = _validate_keyring(keyring)
    if key_id in ring and ring[key_id] != secret:
        raise ValueError("Current backup key ID conflicts with the keyring")
    if legacy_key_id is not None:
        validate_key_id(legacy_key_id)
    def select(identifier):
        if identifier == key_id:
            return secret
        if identifier in ring:
            return ring[identifier]
        raise ValueError("Unknown backup key ID: " + identifier)
    temp = destination.with_name(f".{destination.name}.tmp")
    created=False
    deadline=time.monotonic()+120
    try:
        with source.open("rb") as incoming:
            magic = incoming.read(len(MAGIC))
            if magic not in (MAGIC, MAGIC_V1):
                raise ValueError("Unsupported encrypted backup format")
            header = magic
            selected = secret
            if magic == MAGIC:
                raw_length = incoming.read(1)
                if not raw_length or not 1 <= raw_length[0] <= 64:
                    raise ValueError("Invalid backup key ID length")
                raw_identifier = incoming.read(raw_length[0])
                try:
                    identifier = validate_key_id(raw_identifier.decode("ascii"))
                except (UnicodeError, ValueError):
                    raise ValueError("Invalid backup key ID") from None
                selected = select(identifier)
                header += raw_length + raw_identifier
            elif legacy_key_id is not None:
                # v1 has no identifier: never guess or try every key.
                selected = select(legacy_key_id)
            salt, nonce = incoming.read(SALT_SIZE), incoming.read(NONCE_SIZE)
            header += salt + nonce
            header_size = len(header)
            size = os.fstat(incoming.fileno()).st_size
            if len(salt) != SALT_SIZE or len(nonce) != NONCE_SIZE or size < header_size + TAG_SIZE:
                raise ValueError("Encrypted backup is truncated")
            incoming.seek(-TAG_SIZE, os.SEEK_END)
            tag = incoming.read(TAG_SIZE)
            decryptor = Cipher(algorithms.AES(_key(selected, salt)), modes.GCM(nonce, tag)).decryptor()
            if magic == MAGIC:
                decryptor.authenticate_additional_data(header)
            remaining = size - header_size - TAG_SIZE
            incoming.seek(header_size)
            descriptor=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            created=True
            with os.fdopen(descriptor,"wb") as outgoing:
                while remaining:
                    if time.monotonic()>deadline:raise TimeoutError('Backup decryption exceeded its deadline')
                    chunk = incoming.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise ValueError("Encrypted backup is truncated")
                    remaining -= len(chunk)
                    outgoing.write(decryptor.update(chunk))
                outgoing.write(decryptor.finalize())
                outgoing.flush(); os.fsync(outgoing.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, destination)
    except InvalidTag as exc:
        raise ValueError("Backup authentication failed: wrong key or changed archive") from exc
    finally:
        if created:temp.unlink(missing_ok=True)
