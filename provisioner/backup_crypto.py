"""Streaming authenticated encryption for host-side tenant archives."""
from __future__ import annotations

import hashlib
import os
import time
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
    created=False
    deadline=time.monotonic()+120
    try:
        descriptor=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        created=True
        with os.fdopen(descriptor,"wb") as outgoing, source.open("rb") as incoming:
            outgoing.write(MAGIC + salt + nonce)
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
    created=False
    deadline=time.monotonic()+120
    try:
        descriptor=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        created=True
        with os.fdopen(descriptor,"wb") as outgoing, source.open("rb") as incoming:
            incoming.seek(header_size)
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
