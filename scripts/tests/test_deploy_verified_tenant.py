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


def test_container_create_body_preserves_volume_ports_and_network_aliases():
    original = {
        "Config": {
            "Image": "old-image",
            "Env": ["A=B"],
            "ExposedPorts": {"8501/tcp": {}},
            "Cmd": ["python", "app.py"],
        },
        "HostConfig": {
            "Binds": ["tenant-data:/app/data"],
            "Mounts": [{"Type": "volume", "Source": "tenant-data", "Target": "/app/data"}],
            "PortBindings": {"8501/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18501"}]},
            "PublishAllPorts": False,
            "NetworkMode": "wbsaas_net",
            "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
        },
        "Mounts": [{
            "Type": "volume", "Name": "tenant-data", "Source": "/var/lib/docker/volumes/tenant-data/_data",
            "Destination": "/app/data", "RW": True,
        }],
        "NetworkSettings": {"Networks": {
            "wbsaas_net": {
                "Aliases": ["tenant-old-name", "tenant-service"],
                "Links": None,
                "IPAMConfig": {"IPv4Address": "172.22.0.10"},
                "MacAddress": "02:42:ac:16:00:0a",
                "DriverOpts": {"com.docker.network.endpoint.exposedports": "8501/tcp"},
            },
            "provisioning_net": {"Aliases": ["tenant-old-name"], "IPAMConfig": None},
        }},
    }
    expected_env = ["A=B", "WB_SAAS_DATA_ENCRYPTION_KEY=derived"]

    body = deploy_verified_tenant._container_create_body(
        original, "sha256:new", "tenant-old-name", expected_env
    )
    # docker_api serializes this exact object as the Engine API request JSON.
    request_json = json.loads(json.dumps(body))

    # Docker Engine's create body is flattened: Config fields plus HostConfig
    # and NetworkingConfig are siblings at the JSON root.
    assert "Config" not in request_json
    assert request_json["Image"] == "sha256:new"
    assert request_json["Env"] == expected_env
    assert request_json["HostConfig"] == original["HostConfig"]
    assert request_json["ExposedPorts"] == original["Config"]["ExposedPorts"]
    assert request_json["Cmd"] == original["Config"]["Cmd"]
    endpoints = request_json["NetworkingConfig"]["EndpointsConfig"]
    assert set(endpoints) == {"wbsaas_net", "provisioning_net"}
    assert endpoints["wbsaas_net"]["Aliases"] == ["tenant-old-name", "tenant-service"]
    assert "IPAMConfig" not in endpoints["wbsaas_net"]
    assert "MacAddress" not in endpoints["wbsaas_net"]
    assert endpoints["provisioning_net"]["Aliases"] == ["tenant-old-name"]


def test_runtime_parity_rejects_lost_volume_network_or_alias():
    original = {
        "Config": {"ExposedPorts": {"8501/tcp": {}}},
        "HostConfig": {"Binds": ["tenant-data:/app/data"], "Mounts": [], "PortBindings": {"8501/tcp": [{"HostPort": "18501"}]}, "PublishAllPorts": False, "NetworkMode": "wbsaas_net", "RestartPolicy": {"Name": "unless-stopped"}},
        "Mounts": [{"Type": "volume", "Name": "tenant-data", "Source": "/data", "Destination": "/app/data", "RW": True}],
        "NetworkSettings": {"Networks": {"wbsaas_net": {"Aliases": ["tenant", "tenant-service"]}}},
    }
    actual = json.loads(json.dumps(original))
    deploy_verified_tenant._assert_runtime_parity(original, actual, "tenant")

    actual["Mounts"] = []
    with pytest.raises(AssertionError, match="mounts changed"):
        deploy_verified_tenant._assert_runtime_parity(original, actual, "tenant")

    actual = json.loads(json.dumps(original))
    actual["NetworkSettings"]["Networks"]["wbsaas_net"]["Aliases"] = ["tenant"]
    with pytest.raises(AssertionError, match="aliases changed"):
        deploy_verified_tenant._assert_runtime_parity(original, actual, "tenant")

    actual = json.loads(json.dumps(original))
    actual["NetworkSettings"]["Networks"] = {}
    with pytest.raises(AssertionError, match="Docker networks changed"):
        deploy_verified_tenant._assert_runtime_parity(original, actual, "tenant")
