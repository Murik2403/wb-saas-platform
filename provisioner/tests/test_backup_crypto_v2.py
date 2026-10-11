import json
import sqlite3
import tarfile

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import backup_crypto as crypto

SECRET = 'synthetic-old-key-' + 'x' * 40
NEW_SECRET = 'synthetic-new-key-' + 'y' * 40


def legacy_archive(source, destination):
    # Independently reproduce the deployed v1 layout, without the new writer.
    salt, nonce = bytes(range(16)), bytes(range(12))
    writer = Cipher(algorithms.AES(crypto._key(SECRET, salt)), modes.GCM(nonce)).encryptor()
    destination.write_bytes(b'WBSAASBK1' + salt + nonce + writer.update(source.read_bytes()) + writer.finalize() + writer.tag)


def test_identified_v2_roundtrip_and_old_keyring(tmp_path):
    plain, archive, output = (tmp_path / name for name in ('plain', 'archive', 'out'))
    plain.write_bytes(b'synthetic backup payload' * 100)
    crypto.encrypt_file(plain, archive, SECRET, key_id='old-2026')
    assert archive.read_bytes().startswith(b'WBSAASBK2')
    assert b'old-2026' in archive.read_bytes()[:100]
    crypto.decrypt_file(archive, output, NEW_SECRET, key_id='current-2027', keyring={'old-2026': SECRET})
    assert output.read_bytes() == plain.read_bytes()


def test_v1_current_key_and_explicit_historical_key(tmp_path):
    plain, archive, output = (tmp_path / name for name in ('plain', 'archive', 'out'))
    plain.write_bytes(b'v1 independent fixture')
    legacy_archive(plain, archive)
    crypto.decrypt_file(archive, output, SECRET, key_id='current-id')
    assert output.read_bytes() == plain.read_bytes()
    crypto.decrypt_file(archive, output, NEW_SECRET, key_id='new', keyring={'old': SECRET}, legacy_key_id='old')
    assert output.read_bytes() == plain.read_bytes()
    with pytest.raises(ValueError, match='authentication'):
        crypto.decrypt_file(archive, output, NEW_SECRET, key_id='new', keyring={'old': SECRET})
    assert output.read_bytes() == plain.read_bytes()


def test_v2_unknown_id_and_changed_authenticated_header(tmp_path):
    plain, archive, output = (tmp_path / name for name in ('plain', 'archive', 'out'))
    plain.write_bytes(b'secret fixture')
    crypto.encrypt_file(plain, archive, SECRET, key_id='old-key')
    with pytest.raises(ValueError, match='Unknown backup key ID'):
        crypto.decrypt_file(archive, output, SECRET, key_id='new-key')
    assert not output.exists()
    archive.write_bytes(archive.read_bytes().replace(b'old-key', b'new-key', 1))
    with pytest.raises(ValueError, match='authentication'):
        crypto.decrypt_file(archive, output, SECRET, key_id='new-key')
    assert not output.exists() and not (tmp_path / '.out.tmp').exists()


@pytest.mark.parametrize('location', [0, 11, 25, -1, -20])
def test_v2_corruption_never_replaces_existing_plaintext(tmp_path, location):
    plain, archive, output = (tmp_path / name for name in ('plain', 'archive', 'out'))
    plain.write_bytes(b'fixture payload')
    output.write_bytes(b'preserve existing destination')
    crypto.encrypt_file(plain, archive, SECRET)
    raw = bytearray(archive.read_bytes()); raw[location] ^= 1; archive.write_bytes(raw)
    with pytest.raises(ValueError): crypto.decrypt_file(archive, output, SECRET)
    assert output.read_bytes() == b'preserve existing destination'
    assert not (tmp_path / '.out.tmp').exists()


def test_v2_configuration_bounds_and_no_secret_in_errors(tmp_path):
    plain = tmp_path / 'plain'; plain.write_bytes(b'fixture')
    for value in ('', 'a'*65, 'bad id', '../key', 'ключ'):
        with pytest.raises(ValueError, match='key ID'):
            crypto.encrypt_file(plain, tmp_path / 'archive', SECRET, key_id=value)
    for value in ('null', '[]', '{"x":"short"}', json.dumps({str(i): SECRET for i in range(17)}), '{"x":"'+SECRET+'","x":"'+NEW_SECRET+'"}'):
        with pytest.raises(ValueError) as failure: crypto.parse_keyring(value)
        assert SECRET not in str(failure.value) and NEW_SECRET not in str(failure.value)
    assert crypto.parse_keyring('') == {}


def test_v2_provisioner_configuration_and_cli_helper(tmp_path, monkeypatch):
    import app
    import decrypt_backup
    import sys
    plain, archive, restored = (tmp_path / name for name in ('plain', 'archive', 'out'))
    plain.write_bytes(b'synthetic integration fixture')
    monkeypatch.setattr(app, 'BACKUP_ENCRYPTION_KEY', SECRET)
    monkeypatch.setattr(app, 'BACKUP_KEY_ID', 'integration-old')
    app._encrypt_backup(plain, archive)
    monkeypatch.setattr(app, 'BACKUP_ENCRYPTION_KEY', NEW_SECRET)
    monkeypatch.setattr(app, 'BACKUP_KEY_ID', 'integration-new')
    monkeypatch.setattr(app, 'BACKUP_KEYRING', {'integration-old': SECRET})
    app._decrypt_backup(archive, restored)
    assert restored.read_bytes() == plain.read_bytes()
    monkeypatch.setenv('WB_SAAS_BACKUP_ENCRYPTION_KEY', NEW_SECRET)
    monkeypatch.setenv('WB_SAAS_BACKUP_ENCRYPTION_KEY_ID', 'integration-new')
    monkeypatch.setenv('WB_SAAS_BACKUP_ENCRYPTION_KEYRING', json.dumps({'integration-old': SECRET}))
    monkeypatch.delenv('WB_SAAS_BACKUP_LEGACY_KEY_ID', raising=False)
    monkeypatch.setattr(sys, 'argv', ['decrypt_backup', str(archive), str(restored)])
    assert decrypt_backup.main() == 0
    assert restored.read_bytes() == plain.read_bytes()


def test_v2_truncation_collision_and_empty_payload(tmp_path):
    plain, archive, restored = (tmp_path / name for name in ('plain', 'archive', 'out'))
    plain.write_bytes(b'')
    crypto.encrypt_file(plain, archive, SECRET)
    crypto.decrypt_file(archive, restored, SECRET)
    assert restored.read_bytes() == b''
    raw = archive.read_bytes()
    for length in (9, 10, 15, 30, len(raw)-1):
        archive.write_bytes(raw[:length])
        with pytest.raises(ValueError): crypto.decrypt_file(archive, restored, SECRET)
        assert restored.read_bytes() == b'' and not (tmp_path / '.out.tmp').exists()
    archive.write_bytes(raw)
    (tmp_path / '.out.tmp').write_bytes(b'other operation')
    with pytest.raises(FileExistsError): crypto.decrypt_file(archive, restored, SECRET)
    assert (tmp_path / '.out.tmp').read_bytes() == b'other operation'


def test_v2_new_archive_restore_sqlite_fixture(tmp_path):
    database = tmp_path / 'fixture.sqlite3'
    conn = sqlite3.connect(database)
    conn.execute('CREATE TABLE sales(id INTEGER PRIMARY KEY, amount INTEGER)')
    conn.execute('INSERT INTO sales VALUES(1, 101)'); conn.commit(); conn.close()
    plain, archive, restored = (tmp_path / name for name in ('fixture.tar.gz', 'backup.enc', 'restored.tar.gz'))
    with tarfile.open(plain, 'w:gz') as tar: tar.add(database, arcname='wb_dashboard.sqlite3')
    crypto.encrypt_file(plain, archive, SECRET, key_id='restore-test')
    crypto.decrypt_file(archive, restored, SECRET, key_id='restore-test')
    with tarfile.open(restored, 'r:gz') as tar:
        recovered = tmp_path / 'recovered.sqlite3'
        recovered.write_bytes(tar.extractfile('wb_dashboard.sqlite3').read())
    conn = sqlite3.connect(recovered)
    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert conn.execute('SELECT amount FROM sales').fetchone()[0] == 101
    conn.close()
