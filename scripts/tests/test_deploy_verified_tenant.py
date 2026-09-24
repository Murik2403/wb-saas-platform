import json
import subprocess

import pytest

from scripts import deploy_verified_tenant


def test_derives_missing_tenant_key_from_gateway_without_logging(monkeypatch):
    derived = {
        "key": "unit-test-derived-fernet-key",
        "key_id": "current",
        "keyring": "{}",
    }
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, json.dumps(derived), "")

    monkeypatch.setattr(deploy_verified_tenant.subprocess, "run", fake_run)
    env = deploy_verified_tenant._derive_tenant_encryption_env(
        "gateway", {"WB_SAAS_TENANT_SLUG": "shop-a"}
    )

    assert env == {
        "WB_SAAS_DATA_ENCRYPTION_KEY": derived["key"],
        "WB_SAAS_DATA_ENCRYPTION_KEY_ID": derived["key_id"],
        "WB_SAAS_DATA_ENCRYPTION_KEYRING": derived["keyring"],
    }
    assert calls[0][0][:4] == ["docker", "exec", "gateway", "python"]
    assert "shop-a" in calls[0][0]
    assert "unit-test-derived-fernet-key" not in " ".join(calls[0][0])


def test_keeps_configured_tenant_key_and_does_not_call_gateway(monkeypatch):
    def unexpected_call(*args, **kwargs):
        raise AssertionError("gateway must not be called when tenant key exists")

    monkeypatch.setattr(deploy_verified_tenant.subprocess, "run", unexpected_call)
    assert deploy_verified_tenant._derive_tenant_encryption_env(
        "gateway", {"WB_SAAS_DATA_ENCRYPTION_KEY": "already-configured"}
    ) == {}


def test_missing_master_key_fails_before_tenant_mutation(monkeypatch):
    def failed_gateway(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, "", "no key")

    monkeypatch.setattr(deploy_verified_tenant.subprocess, "run", failed_gateway)
    with pytest.raises(RuntimeError, match="derivation failed"):
        deploy_verified_tenant._derive_tenant_encryption_env(
            "gateway", {"WB_SAAS_TENANT_SLUG": "shop-a"}
        )


def test_does_not_attempt_derivation_without_gateway_or_slug():
    with pytest.raises(RuntimeError, match="cannot be derived"):
        deploy_verified_tenant._derive_tenant_encryption_env(
            None, {"WB_SAAS_TENANT_SLUG": "shop-a"}
        )
    with pytest.raises(RuntimeError, match="cannot be derived"):
        deploy_verified_tenant._derive_tenant_encryption_env("gateway", {})


def test_verified_backup_script_is_valid_and_keeps_key_out_of_command_arguments():
    key = "synthetic-test-key-material"
    script = deploy_verified_tenant._verified_backup_script(
        {"WB_SAAS_DATA_ENCRYPTION_KEY": key}
    )
    compile(script, "verified-backup", "exec")
    assert key in script
    docker_args = ["docker", "exec", "-i", "tenant", "python", "-"]
    assert key not in docker_args
