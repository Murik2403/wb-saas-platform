import os
from pathlib import Path
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import scripts.backup_offsite_envelope as envelope

@pytest.fixture
def keys(tmp_path):
    key=rsa.generate_private_key(public_exponent=65537,key_size=3072)
    public=tmp_path/'public.pem';private=tmp_path/'private.pem'
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    return public,private

def test_exact_streaming_roundtrip_nested_backup_and_private_atomic_output(tmp_path,keys,monkeypatch):
    source=tmp_path/'source';encrypted=tmp_path/'encrypted';restored=tmp_path/'restored'
    original=b'WBSAASBK1'+os.urandom(envelope.CHUNK*3+13);source.write_bytes(original)
    modes=[];open_original=os.open
    monkeypatch.setattr(envelope.os,'open',lambda path,flags,mode:(modes.append(mode),open_original(path,flags,mode))[1])
    envelope.encrypt(source,encrypted,keys[0]);envelope.decrypt(encrypted,restored,keys[1])
    assert restored.read_bytes()==original and encrypted.read_bytes().startswith(envelope.MAGIC)
    assert modes==[0o600,0o600] and not list(tmp_path.glob('*.partial'))

def test_wrong_recipient_tampering_and_truncation_do_not_replace_existing_output(tmp_path,keys):
    source=tmp_path/'source';encrypted=tmp_path/'encrypted';restored=tmp_path/'restored'
    source.write_bytes(b'WBSAASBK1fixture');envelope.encrypt(source,encrypted,keys[0]);restored.write_bytes(b'preserve')
    wrong=rsa.generate_private_key(public_exponent=65537,key_size=3072);wrongfile=tmp_path/'wrong.pem'
    wrongfile.write_bytes(wrong.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    with pytest.raises(ValueError,match='recipient'):envelope.decrypt(encrypted,restored,wrongfile)
    blob=encrypted.read_bytes();damaged=bytearray(blob);damaged[-17]^=1;encrypted.write_bytes(damaged)
    with pytest.raises(ValueError,match='authentication'):envelope.decrypt(encrypted,restored,keys[1])
    encrypted.write_bytes(blob[:-1])
    with pytest.raises(ValueError,match='truncated'):envelope.decrypt(encrypted,restored,keys[1])
    assert restored.read_bytes()==b'preserve' and not list(tmp_path.glob('*.partial'))

def test_public_recipient_rejects_private_or_weak_keys(tmp_path,keys):
    with pytest.raises(ValueError):envelope.public_key(keys[1])
    weak=rsa.generate_private_key(public_exponent=65537,key_size=2048);path=tmp_path/'weak.pem'
    path.write_bytes(weak.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    with pytest.raises(ValueError,match='public_key'):envelope.public_key(path)

@pytest.mark.parametrize('magic', [b'WBSAASBK1', b'WBSAASBK2'])
def test_staging_returns_only_verified_envelopes_never_plaintext_leftovers(tmp_path,keys,magic):
    source=tmp_path/'source';stage=tmp_path/'stage';(source/'demo').mkdir(parents=True);(stage/'demo').mkdir(parents=True)
    (source/'demo'/'valid.tar.gz.enc').write_bytes(magic+b'fixture')
    leftover=stage/'demo'/'old.tar.gz.enc';leftover.write_bytes(b'plaintext-leftover')
    paths=envelope.prepare(source,stage,keys[0],['demo'])
    assert len(paths)==1 and paths[0]!=leftover
    assert paths[0].read_bytes().startswith(envelope.MAGIC)
    first=paths[0].read_bytes();assert envelope.prepare(source,stage,keys[0],['demo'])[0].read_bytes()==first
    paths[0].write_bytes(b'corrupted-cache')
    assert envelope.prepare(source,stage,keys[0],['demo'])[0].read_bytes().startswith(envelope.MAGIC)

def test_staging_overlap_and_invalid_source_cannot_be_uploaded(tmp_path,keys):
    source=tmp_path/'source';(source/'demo').mkdir(parents=True);(source/'demo'/'plain.tar.gz.enc').write_bytes(b'plaintext')
    with pytest.raises(ValueError,match='overlap'):envelope.prepare(source,source/'staging',keys[0],['demo'])
    with pytest.raises(ValueError,match='encrypted_archive'):envelope.prepare(source,tmp_path/'stage',keys[0],['demo'])

def test_changed_source_hash_refuses_atomic_publication(tmp_path,keys,monkeypatch):
    source=tmp_path/'source';source.write_bytes(b'WBSAASBK1fixture');target=tmp_path/'target'
    monkeypatch.setattr(envelope,'digest',lambda _: '0'*64)
    with pytest.raises(ValueError,match='source_changed'):envelope.encrypt(source,target,keys[0])
    assert not target.exists() and not list(tmp_path.glob('*.partial'))
