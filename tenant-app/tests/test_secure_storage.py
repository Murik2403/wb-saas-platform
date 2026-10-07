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


@pytest.mark.parametrize("key", ["", "invalid-key"])
def test_legacy_plaintext_without_valid_key_fails_closed_and_is_unchanged(tmp_path, key):
    path = tmp_path / ".wb_token"
    path.write_text("synthetic-legacy-token", encoding="utf-8")
    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY"):
        secure_storage.read(path, key)
    assert path.read_text(encoding="utf-8") == "synthetic-legacy-token"


def test_config_migrates_legacy_with_existing_key_before_return(tmp_path, monkeypatch):
    import config
    path = tmp_path / ".wb_token"
    path.write_text("synthetic-legacy-token", encoding="utf-8")
    monkeypatch.setattr(config, "TENANT_SLUG", "synthetic")
    monkeypatch.setattr(config, "TOKEN_FALLBACK_PATH", path)
    monkeypatch.setattr(config, "DATA_ENCRYPTION_KEY", KEY)
    assert config.get_token() == "synthetic-legacy-token"
    assert path.read_text(encoding="utf-8").startswith(secure_storage.PREFIX)
    assert secure_storage.read(path, KEY) == ("synthetic-legacy-token", False)


def test_config_legacy_missing_key_never_returns_token(tmp_path, monkeypatch):
    import config
    path = tmp_path / ".wb_token"
    path.write_text("synthetic-legacy-token", encoding="utf-8")
    monkeypatch.setattr(config, "TENANT_SLUG", "synthetic")
    monkeypatch.setattr(config, "TOKEN_FALLBACK_PATH", path)
    monkeypatch.setattr(config, "DATA_ENCRYPTION_KEY", "")
    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY"):
        config.get_token()
