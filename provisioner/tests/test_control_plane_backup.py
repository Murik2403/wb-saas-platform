from unittest.mock import Mock
from types import SimpleNamespace

import app
from app import _control_plane_volume_name


def test_control_plane_backup_resolves_only_this_compose_projects_gateway_volume(monkeypatch):
    monkeypatch.setenv("HOSTNAME", "provisioner-container-id")
    own_container = Mock()
    own_container.attrs = {
        "Config": {"Labels": {"com.docker.compose.project": "wb-saas-platform-clean"}}
    }
    gateway_volume = SimpleNamespace(name="wb-saas-platform-clean_gateway_data")
    client = Mock()
    client.containers.run.return_value.wait.return_value={'StatusCode':0}
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
    client.containers.run.return_value.wait.return_value={'StatusCode':0}
    monkeypatch.setattr(app, "BACKUP_MOUNT", tmp_path)
    monkeypatch.setattr(app, "BACKUP_DIR", "/host/backups")
    monkeypatch.setattr(app, "BACKUP_ENCRYPTION_KEY", "backup-secret-that-is-longer-than-thirty-two-characters")
    monkeypatch.setattr(app, "_client", lambda: client)
    monkeypatch.setattr(app, "_control_plane_volume_name", lambda _: "project_gateway_data")
    monkeypatch.setattr(app, "encrypt_file", lambda _source, destination, _key: destination.write_bytes(b"encrypted"))

    result = app.backup_control_plane()

    assert result["archive"].endswith(".tar.gz.enc")
    run_kwargs = client.containers.run.call_args.kwargs
    # rw on purpose: a reader of a WAL database updates the shared -shm; the helper opens databases read-only
    assert run_kwargs["volumes"] == {
        "project_gateway_data": {"bind": "/data", "mode": "rw"},
        "/host/backups": {"bind": "/backup", "mode": "rw"},
    }
    assert run_kwargs["command"][0] == "/data"
    assert run_kwargs["command"][1].startswith("/backup/control-plane/.")
