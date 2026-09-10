from pathlib import Path

from standalone import run_agents


def test_config_path_defaults_to_user_private_directory(monkeypatch):
    monkeypatch.delenv("WB_SAAS_STANDALONE_CONFIG_PATH", raising=False)
    assert run_agents.config_path() == Path.home() / ".wb-saas-secrets" / "standalone-config.yaml"


def test_config_path_honors_explicit_override(monkeypatch, tmp_path):
    target = tmp_path / "secret.yaml"
    monkeypatch.setenv("WB_SAAS_STANDALONE_CONFIG_PATH", str(target))
    assert run_agents.config_path() == target
