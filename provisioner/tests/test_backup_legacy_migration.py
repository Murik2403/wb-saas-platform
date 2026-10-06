import os,time
import pytest
import app
from backup_crypto import decrypt_file

def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(app,'BACKUP_MOUNT',tmp_path);monkeypatch.setattr(app,'BACKUP_ENCRYPTION_KEY','x'*40)
    directory=tmp_path/'demo';directory.mkdir()
    source=directory/'legacy.tar.gz';source.write_bytes(b'legacy-private-fixture');os.utime(source,(time.time()-30*86400,)*2)
    return source

def test_expired_plaintext_removed_only_after_exact_encrypted_roundtrip(tmp_path,monkeypatch):
    source=setup(tmp_path,monkeypatch)
    result=app.migrate_legacy_backups('demo',14,remove_expired=True)
    assert result=={'migrated':1,'verified':1,'plaintext_removed':1} and not source.exists()
    restored=tmp_path/'restore';decrypt_file(source.with_name(source.name+'.enc'),restored,'x'*40)
    assert restored.read_bytes()==b'legacy-private-fixture'

def test_default_and_not_expired_keep_plaintext_until_explicit_policy(tmp_path,monkeypatch):
    source=setup(tmp_path,monkeypatch)
    assert app.migrate_legacy_backups('demo',14)['plaintext_removed']==0 and source.exists()
    os.utime(source,None)
    assert app.migrate_legacy_backups('demo',14,True)['plaintext_removed']==0 and source.exists()

def test_failed_verification_preserves_original_and_no_invalid_new_cipher(tmp_path,monkeypatch):
    source=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(app,'decrypt_file',lambda *args:(_ for _ in ()).throw(ValueError('synthetic failed verification')))
    with pytest.raises(ValueError):app.migrate_legacy_backups('demo',14,True)
    assert source.read_bytes()==b'legacy-private-fixture'
    assert not source.with_name(source.name+'.enc').exists()
    assert not list(source.parent.glob('.*.partial'))
