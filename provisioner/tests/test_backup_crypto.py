import pytest

from backup_crypto import decrypt_file, encrypt_file


SECRET = "backup-secret-that-is-longer-than-thirty-two-characters"


def test_encrypted_backup_round_trips_without_plaintext(tmp_path):
    source, encrypted, restored = tmp_path / "source", tmp_path / "archive.enc", tmp_path / "restore"
    source.write_bytes(b"tenant backup" * 1000)
    encrypt_file(source, encrypted, SECRET)
    assert b"tenant backup" not in encrypted.read_bytes()
    decrypt_file(encrypted, restored, SECRET)
    assert restored.read_bytes() == source.read_bytes()


def test_backup_rejects_wrong_key_without_creating_output(tmp_path):
    source, encrypted, restored = tmp_path / "source", tmp_path / "archive.enc", tmp_path / "restore"
    source.write_bytes(b"tenant backup")
    encrypt_file(source, encrypted, SECRET)
    with pytest.raises(ValueError, match="authentication"):
        decrypt_file(encrypted, restored, "wrong-key-that-is-longer-than-thirty-two-characters")
    assert not restored.exists()
