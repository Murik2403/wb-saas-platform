import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from scripts import pdn_erasure_backup as backup


@pytest.fixture
def ledger(tmp_path):
    path = tmp_path / 'ledger.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE metadata(version INTEGER NOT NULL)')
        conn.execute('INSERT INTO metadata VALUES (1)')
        conn.execute('CREATE TABLE erasures(identity TEXT PRIMARY KEY,requested_at TEXT NOT NULL,finalization_requested_at TEXT NOT NULL)')
        conn.execute('INSERT INTO erasures VALUES (?,?,?)', ('a'*64,'2026-10-01T12:00:00+00:00',''))
    os.chmod(path,0o600)
    return path


@pytest.fixture
def keys(tmp_path):
    key=rsa.generate_private_key(public_exponent=65537,key_size=3072)
    public=tmp_path/'public.pem';private=tmp_path/'private.pem'
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    os.chmod(public,0o644);os.chmod(private,0o600)
    return public,private


@pytest.fixture
def output(tmp_path):
    path=tmp_path/'encrypted';path.mkdir(mode=0o700)
    return path


def test_snapshot_missing_never_creates_replacement(tmp_path):
    path=tmp_path/'absent.sqlite3'
    with pytest.raises(backup.BackupInvalid):backup.snapshot(path)
    assert not path.exists()


def test_consistent_snapshot_includes_committed_wal_excludes_uncommitted(ledger):
    writer=sqlite3.connect(ledger)
    writer.execute('PRAGMA journal_mode=WAL')
    writer.execute('INSERT INTO erasures VALUES (?,?,?)',('b'*64,'2026-10-02T12:00:00+00:00',''))
    writer.commit()
    writer.execute('INSERT INTO erasures VALUES (?,?,?)',('c'*64,'2026-10-03T12:00:00+00:00',''))
    raw=backup.snapshot(ledger)
    assert [r[0] for r in json.loads(raw)['erasures']]==['a'*64,'b'*64]
    writer.rollback();writer.close()


@pytest.mark.parametrize('mutation',[
    'UPDATE metadata SET version=2',
    "UPDATE erasures SET identity='broken'",
    "UPDATE erasures SET requested_at='invalid'",
    "UPDATE erasures SET finalization_requested_at='invalid'",
    'ALTER TABLE erasures ADD COLUMN email TEXT',
    'CREATE TABLE leaked_private_data(email TEXT)',
])
def test_invalid_ledger_fails_closed(ledger,mutation):
    with sqlite3.connect(ledger) as conn:conn.execute(mutation)
    with pytest.raises(backup.BackupInvalid):backup.snapshot(ledger)


def test_corrupt_source_no_output(ledger,output,keys):
    ledger.write_bytes(b'corrupt')
    with pytest.raises(backup.BackupInvalid):backup.encrypt(ledger,output,keys[0])
    assert list(output.iterdir())==[]


def test_encrypted_immutable_deduplicated_roundtrip_no_plaintext_file(ledger,output,keys):
    original=ledger.read_bytes();manifest=backup.encrypt(ledger,output,keys[0])
    info=backup.readvalid(manifest);cipher=output/info['envelope'];before=cipher.read_bytes()
    assert before.startswith(b'MHOFFSITE2') and b'a'*64 not in before
    assert backup.encrypt(ledger,output,keys[0])==manifest
    assert cipher.read_bytes()==before and ledger.read_bytes()==original
    assert backup.recover_union([manifest],keys[1])==[['a'*64,'2026-10-01T12:00:00+00:00','']]
    assert sorted(p.suffix for p in output.iterdir())==['.enc','.json']
    assert not list(output.glob('*.partial'))
    if os.name!='nt':assert all(p.stat().st_mode & 0o077==0 for p in output.iterdir())


def test_recovery_union_preserves_intent_despite_stale_snapshot(ledger,output,keys):
    old=backup.encrypt(ledger,output,keys[0])
    with sqlite3.connect(ledger) as conn:
        conn.execute('UPDATE erasures SET finalization_requested_at=?',('2026-10-02T12:00:00+00:00',))
        conn.execute('INSERT INTO erasures VALUES (?,?,?)',('b'*64,'2026-10-03T12:00:00+00:00',''))
    fresh=backup.encrypt(ledger,output,keys[0])
    # Simulates later incomplete history; recovery union must not forget b.
    with sqlite3.connect(ledger) as conn:
        conn.execute("DELETE FROM erasures WHERE identity=?",('b'*64,))
        conn.execute("UPDATE erasures SET finalization_requested_at=''")
    stale=backup.encrypt(ledger,output,keys[0])
    assert stale==old
    expected=[['a'*64,'2026-10-01T12:00:00+00:00','2026-10-02T12:00:00+00:00'],['b'*64,'2026-10-03T12:00:00+00:00','']]
    assert backup.recover_union([fresh,stale,old],keys[1])==expected
    assert backup.recover_union([old,fresh,stale],keys[1])==expected
    assert len(json.loads(backup.snapshot(ledger))['erasures'])==1  # Recovery never writes live DB.


def test_no_authoritative_snapshots_cannot_recover_empty(keys):
    with pytest.raises(backup.BackupInvalid,match='authoritative'):backup.recover_union([],keys[1])


def test_corrupt_cipher_hash_and_authentication_fail_closed(ledger,output,keys):
    manifest=backup.encrypt(ledger,output,keys[0]);info=backup.readvalid(manifest)
    cipher=output/info['envelope'];raw=bytearray(cipher.read_bytes());raw[-17]^=1;cipher.write_bytes(raw)
    with pytest.raises(backup.BackupInvalid):backup.readvalid(manifest)
    # Even if someone rewrites unsigned hash metadata, AEAD detects tampering.
    info['cipher_sha256']=hashlib.sha256(raw).hexdigest();manifest.write_text(json.dumps(info))
    backup.readvalid(manifest)
    with pytest.raises(backup.BackupInvalid,match='authentication'):backup.recover_union([manifest],keys[1])


def test_orphan_envelope_not_blessed_or_overwritten(ledger,output,keys):
    manifest=backup.encrypt(ledger,output,keys[0]);info=backup.readvalid(manifest)
    cipher=output/info['envelope'];before=cipher.read_bytes();manifest.unlink()
    with pytest.raises(backup.BackupInvalid,match='unpaired'):backup.encrypt(ledger,output,keys[0])
    assert cipher.read_bytes()==before and not manifest.exists()


def test_manifest_traversal_rejected(ledger,output,keys):
    manifest=backup.encrypt(ledger,output,keys[0]);info=json.loads(manifest.read_bytes());info['envelope']='../ledger.sqlite3'
    manifest.write_text(json.dumps(info))
    with pytest.raises(backup.BackupInvalid):backup.readvalid(manifest)


def test_publish_failure_cleans_cipher_partial_no_plaintext(ledger,output,keys,monkeypatch):
    monkeypatch.setattr(backup.os,'link',lambda *a:(_ for _ in ()).throw(OSError('synthetic')))
    with pytest.raises(OSError):backup.encrypt(ledger,output,keys[0])
    assert list(output.iterdir())==[]


def test_wrong_private_recipient_is_rejected(ledger,output,keys,tmp_path):
    manifest=backup.encrypt(ledger,output,keys[0]);wrong=rsa.generate_private_key(public_exponent=65537,key_size=3072)
    path=tmp_path/'wrong.pem';path.write_bytes(wrong.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));os.chmod(path,0o600)
    with pytest.raises(backup.BackupInvalid,match='recipient'):backup.recover_union([manifest],path)


def test_symlink_ledger_rejected(tmp_path,ledger):
    link=tmp_path/'link.sqlite3'
    try:link.symlink_to(ledger)
    except OSError:pytest.skip('Windows requires symlink privilege')
    with pytest.raises(backup.BackupInvalid):backup.snapshot(link)
