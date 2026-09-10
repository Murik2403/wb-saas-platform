"""Streaming authenticated encryption for host-side tenant archives."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b"WBSAASBK1"
SALT_SIZE = 16
NONCE_SIZE = 12
TAG_SIZE = 16
CHUNK_SIZE = 1024 * 1024


def _key(secret: str, salt: bytes) -> bytes:
    if len(secret) < 32:
        raise ValueError("Backup encryption key must contain at least 32 characters")
    return hashlib.scrypt(secret.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)


def encrypt_file(source: Path, destination: Path, secret: str) -> None:
    """Write AES-256-GCM ciphertext atomically; source is never loaded wholly."""
    salt, nonce = os.urandom(SALT_SIZE), os.urandom(NONCE_SIZE)
    encryptor = Cipher(algorithms.AES(_key(secret, salt)), modes.GCM(nonce)).encryptor()
    temp = destination.with_name(f".{destination.name}.tmp")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open("rb") as incoming, temp.open("wb") as outgoing:
            outgoing.write(MAGIC + salt + nonce)
            while chunk := incoming.read(CHUNK_SIZE):
                outgoing.write(encryptor.update(chunk))
            outgoing.write(encryptor.finalize())
            outgoing.write(encryptor.tag)
        os.chmod(temp, 0o600)
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


def decrypt_file(source: Path, destination: Path, secret: str) -> None:
    """Verify and decrypt one archive atomically; wrong keys leave no output."""
    header_size = len(MAGIC) + SALT_SIZE + NONCE_SIZE
    if source.stat().st_size <= header_size + TAG_SIZE:
        raise ValueError("Encrypted backup is truncated")
    with source.open("rb") as incoming:
        if incoming.read(len(MAGIC)) != MAGIC:
            raise ValueError("Unsupported encrypted backup format")
        salt, nonce = incoming.read(SALT_SIZE), incoming.read(NONCE_SIZE)
        incoming.seek(-TAG_SIZE, os.SEEK_END)
        tag = incoming.read(TAG_SIZE)
    decryptor = Cipher(algorithms.AES(_key(secret, salt)), modes.GCM(nonce, tag)).decryptor()
    temp = destination.with_name(f".{destination.name}.tmp")
    remaining = source.stat().st_size - header_size - TAG_SIZE
    try:
        with source.open("rb") as incoming, temp.open("wb") as outgoing:
            incoming.seek(header_size)
            while remaining:
                chunk = incoming.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    raise ValueError("Encrypted backup is truncated")
                remaining -= len(chunk)
                outgoing.write(decryptor.update(chunk))
            outgoing.write(decryptor.finalize())
        os.chmod(temp, 0o600)
        os.replace(temp, destination)
    except InvalidTag as exc:
        raise ValueError("Backup authentication failed: wrong key or changed archive") from exc
    finally:
        temp.unlink(missing_ok=True)
