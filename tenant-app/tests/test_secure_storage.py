import pytest

import secure_storage


KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


def test_encrypted_file_does_not_reveal_token_and_round_trips(tmp_path):
    path = tmp_path / ".wb_token"
    secure_storage.write(path, "super-secret", KEY)
    assert "super-secret" not in path.read_text(encoding="utf-8")
    assert secure_storage.read(path, KEY) == ("super-secret", False)


def test_wrong_key_rejects_ciphertext(tmp_path):
    path = tmp_path / ".wb_token"
    secure_storage.write(path, "super-secret", KEY)
    with pytest.raises(RuntimeError, match="authenticated"):
        secure_storage.read(path, "YWJjZGVmMDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODk=")


def test_plaintext_is_marked_for_migration(tmp_path):
    path = tmp_path / ".wb_token"
    path.write_text("legacy-token", encoding="utf-8")
    assert secure_storage.read(path, KEY) == ("legacy-token", True)
