from unittest.mock import Mock
from types import SimpleNamespace

import app
import pytest
from fastapi import HTTPException
from app import _control_plane_volume_name


def test_control_plane_backup_resolves_only_this_compose_projects_gateway_volume(monkeypatch):
    monkeypatch.setenv("HOSTNAME", "provisioner-container-id")
    own_container = Mock()
    own_container.attrs = {
        "Config": {"Labels": {"com.docker.compose.project": "wb-saas-platform-clean"}}
    }
    gateway_volume = SimpleNamespace(name="wb-saas-platform-clean_gateway_data")
    client = Mock()
    client.containers.get.return_value = own_container
    client.volumes.list.return_value = [gateway_volume]

    assert _control_plane_volume_name(client) == gateway_volume.name
    assert client.volumes.list.call_args.kwargs == {
        "filters": {
            "label": [
                "com.docker.compose.project=wb-saas-platform-clean",
                "com.docker.compose.volume=gateway_data",
            ]
        }
    }


def test_control_plane_backup_mounts_only_resolved_gateway_volume(tmp_path, monkeypatch):
    client = Mock()
    monkeypatch.setattr(app, "BACKUP_MOUNT", tmp_path)
    monkeypatch.setattr(app, "BACKUP_DIR", "/host/backups")
    monkeypatch.setattr(app, "BACKUP_ENCRYPTION_KEY", "backup-secret-that-is-longer-than-thirty-two-characters")
    monkeypatch.setattr(app, "_client", lambda: client)
    monkeypatch.setattr(app, "_control_plane_volume_name", lambda _: "project_gateway_data")
    monkeypatch.setattr(app, "encrypt_file", lambda _source, destination, _key: destination.write_bytes(b"encrypted"))

    result = app.backup_control_plane()

    assert result["archive"].endswith(".tar.gz.enc")
    run_kwargs = client.containers.run.call_args.kwargs
    assert run_kwargs["volumes"] == {
        "project_gateway_data": {"bind": "/data", "mode": "ro"},
        str(app.Path("/host/backups") / "control-plane-encrypted"): {"bind": "/backup", "mode": "rw"},
    }
    assert run_kwargs["cap_drop"] == ["ALL"]
    assert run_kwargs["cap_add"] == ["DAC_READ_SEARCH"]
    assert run_kwargs["network_disabled"] is True
    assert run_kwargs["command"][2].startswith("/backup/.control-plane-encrypted-")


@pytest.mark.parametrize("keep_days", [0, 3651])
def test_backup_rejects_invalid_retention_before_side_effects(tmp_path, monkeypatch, keep_days):
    client = Mock()
    monkeypatch.setattr(app, "BACKUP_MOUNT", tmp_path)
    monkeypatch.setattr(app, "BACKUP_ENCRYPTION_KEY", "backup-secret-that-is-longer-than-thirty-two-characters")

    with pytest.raises(HTTPException) as error:
        app._backup_volume("tenant_volume", "tenant", keep_days, client)

    assert error.value.status_code == 422
    assert error.value.detail == "keep_days must be between 1 and 3650"
    assert not (tmp_path / "tenant").exists()
    client.containers.run.assert_not_called()


@pytest.mark.parametrize("keep_days", [0, 3651])
@pytest.mark.parametrize("backup_route", [app.backup_tenant, app.backup_control_plane])
def test_backup_routes_validate_retention_before_docker_access(monkeypatch, keep_days, backup_route):
    create_client = Mock(side_effect=AssertionError("invalid retention must fail before Docker client access"))
    monkeypatch.setattr(app, "_client", create_client)

    with pytest.raises(HTTPException) as error:
        if backup_route is app.backup_tenant:
            backup_route("tenant", keep_days)
        else:
            backup_route(keep_days)

    assert error.value.status_code == 422
    create_client.assert_not_called()
