"""Independent erasure-ledger snapshots: memory-only plaintext, immutable envelopes.

Recovery returns the union of supplied snapshots, never overwrites a live ledger.
The operator must supply ALL authoritative snapshots; a timestamp or latest.json
is deliberately not an authority to replace deletion history. No network calls.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import struct
import time
from datetime import datetime, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.exceptions import InvalidTag

try:
    from scripts import backup_offsite_envelope as envelope
except ModuleNotFoundError:  # Executed directly from the committed scripts folder.
    import backup_offsite_envelope as envelope

MAX_BYTES = 64 * 1024 * 1024
HEX = re.compile(r"[0-9a-f]{64}\Z")


class BackupInvalid(RuntimeError):
    """Generic errors exclude file names, identity hashes and provider secrets."""


def _path(value, *, existing=True, private=True):
    path = Path(value)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise BackupInvalid("erasure_backup_unsafe_path")
    if existing:
        try:
            mode = path.stat().st_mode
            if not stat.S_ISREG(mode) or (private and os.name != "nt" and mode & 0o077):
                raise BackupInvalid("erasure_backup_unsafe_file")
        except OSError:
            raise BackupInvalid("erasure_backup_source_unavailable") from None
    return path


def _stamp(value, *, empty=False):
    if empty and value == "":
        return
    if not isinstance(value, str) or not value or len(value) > 64:
        raise BackupInvalid("erasure_backup_invalid_timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise BackupInvalid("erasure_backup_invalid_timestamp") from None


def _records(rows):
    result = []
    seen = set()
    for row in rows:
        if not isinstance(row, (tuple, list)) or len(row) != 3:
            raise BackupInvalid("erasure_backup_invalid_rows")
        identity, requested, finalized = row
        if not isinstance(identity, str) or not HEX.fullmatch(identity) or identity in seen:
            raise BackupInvalid("erasure_backup_invalid_identity")
        _stamp(requested)
        _stamp(finalized, empty=True)
        seen.add(identity)
        result.append([identity, requested, finalized])
    return sorted(result)


def _encode(rows):
    raw = json.dumps({"version": 1, "erasures": _records(rows)}, sort_keys=True,
                     separators=(",", ":")).encode()
    if len(raw) > MAX_BYTES:
        raise BackupInvalid("erasure_backup_too_large")
    return raw


def _decode(raw):
    if len(raw) > MAX_BYTES:
        raise BackupInvalid("erasure_backup_too_large")
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {"version", "erasures"} or type(value["version"]) is not int or value["version"] != 1 or not isinstance(value["erasures"], list):
            raise BackupInvalid("erasure_backup_invalid_snapshot")
        return _records(value["erasures"])
    except (ValueError, TypeError, UnicodeError):
        raise BackupInvalid("erasure_backup_invalid_snapshot") from None


def snapshot(ledger_path):
    """Consistent SQLite read-only backup to RAM; canonical record bytes in RAM.

    Missing live source is an error: never initialize an empty replacement.
    Canonical rows make unchanged snapshots deduplicate despite SQLite page churn.
    """
    path = _path(ledger_path)
    source = memory = None
    try:
        source = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
        memory = sqlite3.connect(":memory:")
        deadline = time.monotonic() + 15
        def progress(_status, _remaining, _total):
            if time.monotonic() > deadline:
                raise BackupInvalid("erasure_backup_timeout")
        source.backup(memory, pages=128, progress=progress, sleep=0.01)
        if memory.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise BackupInvalid("erasure_backup_invalid_database")
        tables = {r[0] for r in memory.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables != {"metadata", "erasures"} or memory.execute("SELECT version FROM metadata").fetchall() != [(1,)]:
            raise BackupInvalid("erasure_backup_invalid_schema")
        columns = [r[1] for r in memory.execute("PRAGMA table_info(erasures)")]
        if columns != ["identity", "requested_at", "finalization_requested_at"]:
            raise BackupInvalid("erasure_backup_invalid_schema")
        return _encode(memory.execute("SELECT identity,requested_at,finalization_requested_at FROM erasures").fetchall())
    except (OSError, sqlite3.Error):
        raise BackupInvalid("erasure_backup_source_unavailable_or_invalid") from None
    finally:
        if source is not None:
            source.close()
        if memory is not None:
            memory.close()


def _publish(path, raw):
    """Publish without replace, including concurrent publishers; never plaintext."""
    temporary = path.with_name("." + path.name + "." + os.urandom(8).hex() + ".partial")
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def _header(raw):
    prefix = len(envelope.MAGIC)
    if raw[:prefix] != envelope.MAGIC or len(raw) < prefix + 4:
        raise BackupInvalid("erasure_backup_invalid_envelope")
    length = struct.unpack(">I", raw[prefix:prefix + 4])[0]
    if not 1 <= length <= envelope.MAX_HEADER:
        raise BackupInvalid("erasure_backup_invalid_envelope")
    end = prefix + 4 + length
    try:
        metadata = json.loads(raw[prefix + 4:end])
        if set(metadata) != {"recipient", "wrapped_key", "nonce", "source_sha256", "source_bytes"}:
            raise ValueError()
        if not HEX.fullmatch(metadata["recipient"]) or not HEX.fullmatch(metadata["source_sha256"]) or type(metadata["source_bytes"]) is not int or not 0 <= metadata["source_bytes"] <= MAX_BYTES or len(raw) != end + metadata["source_bytes"] + 16:
            raise ValueError()
        if len(base64.b64decode(metadata["nonce"], validate=True)) != 12:
            raise ValueError()
        base64.b64decode(metadata["wrapped_key"], validate=True)
    except (ValueError, TypeError, KeyError):
        raise BackupInvalid("erasure_backup_invalid_envelope") from None
    return metadata, raw[:end], raw[end:-16], raw[-16:]


def readvalid(manifest_path):
    """Validate immutable pair/hash/header. Cryptographic authenticity needs owner key."""
    path = _path(manifest_path)
    try:
        if path.stat().st_size > 4096:
            raise BackupInvalid("erasure_backup_invalid_manifest")
        value = json.loads(path.read_bytes())
        if not isinstance(value, dict) or set(value) != {"version", "source_sha256", "cipher_sha256", "recipient", "source_bytes", "envelope"} or type(value["version"]) is not int or value["version"] != 1:
            raise BackupInvalid("erasure_backup_invalid_manifest")
        for name in ("source_sha256", "cipher_sha256", "recipient"):
            if not isinstance(value[name], str) or not HEX.fullmatch(value[name]):
                raise BackupInvalid("erasure_backup_invalid_manifest")
        expected = value["source_sha256"] + "-" + value["recipient"] + ".ledger.enc"
        if value["envelope"] != expected or path.name != expected + ".json":
            raise BackupInvalid("erasure_backup_invalid_manifest")
        target = _path(path.parent / expected)
        if target.stat().st_size > MAX_BYTES + envelope.MAX_HEADER + 64:
            raise BackupInvalid("erasure_backup_too_large")
        raw = target.read_bytes()
        info, _aad, _cipher, _tag = _header(raw)
        if hashlib.sha256(raw).hexdigest() != value["cipher_sha256"] or any(value[k] != info[k] for k in ("source_sha256", "recipient", "source_bytes")):
            raise BackupInvalid("erasure_backup_integrity_failed")
        return value
    except (OSError, ValueError, TypeError):
        raise BackupInvalid("erasure_backup_invalid_manifest") from None


def encrypt(ledger_path, output_dir, recipient):
    """Snapshot and encrypt using the existing RSA-OAEP/AES-GCM envelope contract.

    Returns an immutable manifest path. No private key is needed on the server.
    Unchanged source+recipient reuses a fully validated existing pair.
    """
    directory = _path(output_dir, existing=False)
    if not directory.is_dir() or (os.name != "nt" and directory.stat().st_mode & 0o077):
        raise BackupInvalid("erasure_backup_unsafe_directory")
    raw = snapshot(ledger_path)
    key = envelope.public_key(_path(recipient, private=False))
    digest = hashlib.sha256(raw).hexdigest()
    recipient_hash = envelope.recipient_id(key)
    name = digest + "-" + recipient_hash + ".ledger.enc"
    destination = directory / name
    manifest = directory / (name + ".json")
    if manifest.exists():
        readvalid(manifest)
        return manifest
    if destination.exists():
        # An orphan cannot be authenticated using only a public key. Do not
        # bless arbitrary stale/corrupt ciphertext by generating its manifest.
        raise BackupInvalid("erasure_backup_unpaired_envelope")
    if not destination.exists():
        aes, nonce = os.urandom(32), os.urandom(12)
        info = {"recipient": recipient_hash, "wrapped_key": base64.b64encode(key.encrypt(aes, envelope.OAEP)).decode(), "nonce": base64.b64encode(nonce).decode(), "source_sha256": digest, "source_bytes": len(raw)}
        header = json.dumps(info, sort_keys=True, separators=(",", ":")).encode()
        aad = envelope.MAGIC + struct.pack(">I", len(header)) + header
        cipher = Cipher(algorithms.AES(aes), modes.GCM(nonce)).encryptor()
        cipher.authenticate_additional_data(aad)
        encrypted = cipher.update(raw) + cipher.finalize()
        if not _publish(destination, aad + encrypted + cipher.tag):
            raise BackupInvalid("erasure_backup_concurrent_publish_retry")
    target = _path(destination)
    encrypted = target.read_bytes()
    info, *_ = _header(encrypted)
    if info["source_sha256"] != digest or info["recipient"] != recipient_hash or info["source_bytes"] != len(raw):
        raise BackupInvalid("erasure_backup_integrity_failed")
    metadata = {"version": 1, "source_sha256": digest, "cipher_sha256": hashlib.sha256(encrypted).hexdigest(), "recipient": recipient_hash, "source_bytes": len(raw), "envelope": name}
    _publish(manifest, json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode())
    readvalid(manifest)
    return manifest


def recover_union(manifest_paths, private_key, password=None):
    """Owner-only memory recovery: union every supplied immutable snapshot.

    Never writes/replaces/initializes the live ledger, never selects latest by date.
    The caller must independently establish completeness of the snapshot set.
    """
    paths = list(manifest_paths)
    if not paths:
        raise BackupInvalid("erasure_backup_no_authoritative_snapshots")
    key_path = _path(private_key)
    try:
        if key_path.stat().st_size > 16384:
            raise ValueError()
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=password)
        if not isinstance(key, rsa.RSAPrivateKey) or not 3072 <= key.key_size <= 8192:
            raise ValueError()
    except (ValueError, OSError, TypeError):
        raise BackupInvalid("erasure_backup_invalid_private_key") from None
    result = {}
    for path in paths:
        manifest = readvalid(path)
        if manifest["recipient"] != envelope.recipient_id(key):
            raise BackupInvalid("erasure_backup_wrong_recipient")
        raw = _path(Path(path).parent / manifest["envelope"]).read_bytes()
        info, aad, ciphertext, tag = _header(raw)
        try:
            aes = key.decrypt(base64.b64decode(info["wrapped_key"], validate=True), envelope.OAEP)
            cipher = Cipher(algorithms.AES(aes), modes.GCM(base64.b64decode(info["nonce"]), tag)).decryptor()
            cipher.authenticate_additional_data(aad)
            plaintext = cipher.update(ciphertext) + cipher.finalize()
        except (ValueError, InvalidTag):
            raise BackupInvalid("erasure_backup_authentication_failed") from None
        if hashlib.sha256(plaintext).hexdigest() != info["source_sha256"]:
            raise BackupInvalid("erasure_backup_integrity_failed")
        for identity, requested, finalized in _decode(plaintext):
            prior = result.get(identity)
            if prior is None:
                result[identity] = [identity, requested, finalized]
            else:
                # Do not erase finalization intent when a later supplied snapshot
                # has lost it. Preserve earliest recorded timestamp for each event.
                prior[1] = min(prior[1], requested)
                if finalized:
                    prior[2] = min(prior[2], finalized) if prior[2] else finalized
    return sorted(result.values())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--recipient", required=True)
    args = parser.parse_args()
    try:
        encrypt(args.ledger, args.output, args.recipient)
        print(json.dumps({"status": "ok", "encrypted": True, "immutable": True}))
    except (BackupInvalid, ValueError, OSError):
        parser.exit(1, "erasure_backup_failed\n")
