import asyncio
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

import app
from app import TenantExportRequest, TenantRequest, tenant_container_spec


def _request() -> TenantRequest:
    return TenantRequest(
        internal_api_secret="scoped-secret",
        billing_url="https://example.test/billing",
        logout_url="https://example.test/logout",
        internal_api_url="https://example.test",
    )


def test_spec_is_allowlisted_and_hardened():
    spec = tenant_container_spec("shop-a", _request())
    assert spec["image"]
    assert spec["user"] == "10001:10001"
    assert spec["cap_drop"] == ["ALL"]
    assert spec["security_opt"] == ["no-new-privileges:true"]
    assert spec["read_only"] is True
    assert set(spec["volumes"]) == {"wb-tenant-data-shop-a"}
    assert "/var/run/docker.sock" not in str(spec)


def test_tenant_receives_only_derived_data_key():
    request = _request().model_copy(update={"data_encryption_key": "derived-key"})
    assert tenant_container_spec("shop-a", request)["environment"]["WB_SAAS_DATA_ENCRYPTION_KEY"] == "derived-key"


def test_tenant_receives_sentry_configuration_only_when_configured():
    request = _request().model_copy(
        update={
            "sentry_dsn": "https://public@example.ingest.sentry.io/123",
            "sentry_environment": "staging",
        }
    )
    environment = tenant_container_spec("shop-a", request)["environment"]
    assert environment["WB_SAAS_SENTRY_DSN"] == request.sentry_dsn
    assert environment["WB_SAAS_SENTRY_ENVIRONMENT"] == "staging"


def test_tenant_health_router_is_public_but_exact(monkeypatch):
    monkeypatch.setattr(app, "PARENT_DOMAIN", "example.test")
    labels = tenant_container_spec("shop-a", _request())["labels"]
    prefix = "traefik.http.routers.tenant-shop-a-health"
    assert labels[f"{prefix}.rule"] == "Host(`shop-a.app.example.test`) && Path(`/healthz`) && Method(`GET`)"
    assert labels[f"{prefix}.service"] == "tenant-shop-a"
    assert labels[f"{prefix}.middlewares"] == "tenant-shop-a-health-rewrite"
    assert labels["traefik.http.middlewares.tenant-shop-a-health-rewrite.replacePath.path"] == "/_stcore/health"


def test_tenant_legacy_health_router_is_public_but_exact(monkeypatch):
    monkeypatch.setattr(app, "PARENT_DOMAIN", "example.test")
    labels = tenant_container_spec("shop-a", _request())["labels"]
    prefix = "traefik.http.routers.tenant-shop-a-health-legacy"
    assert labels[f"{prefix}.rule"] == (
        "Host(`shop-a.app.example.test`) && Path(`/_stcore/health`) && Method(`GET`)"
    )
    assert labels[f"{prefix}.service"] == "tenant-shop-a"
    assert f"{prefix}.middlewares" not in labels


def test_tenant_pwa_assets_are_public_but_exact(monkeypatch):
    monkeypatch.setattr(app, "PARENT_DOMAIN", "example.test")
    labels = tenant_container_spec("shop-a", _request())["labels"]
    prefix = "traefik.http.routers.tenant-shop-a-pwa"

    assert labels[f"{prefix}.rule"] == (
        "Host(`shop-a.app.example.test`) && "
        "(Path(`/manifest.webmanifest`) || Path(`/sw.js`) || Path(`/offline.html`) || PathPrefix(`/icons/`))"
    )
    assert labels[f"{prefix}.service"] == "tenant-shop-a"
    assert f"{prefix}.middlewares" not in labels


@pytest.mark.parametrize("slug", ["../escape", "UPPER", "two__underscores", "", "a" * 33])
def test_invalid_slug_is_rejected_before_docker_client(slug):
    with pytest.raises(Exception):
        tenant_container_spec(slug, _request())


def test_caller_cannot_supply_extra_docker_arguments():
    with pytest.raises(ValidationError):
        TenantRequest(
            internal_api_secret="scoped-secret",
            billing_url="https://example.test/billing",
            logout_url="https://example.test/logout",
            internal_api_url="https://example.test",
            image="attacker:latest",
        )


def test_export_request_requires_scoped_internal_secret():
    request = TenantExportRequest(expected_internal_secret="scoped-secret")
    assert request.expected_internal_secret == "scoped-secret"
    with pytest.raises(ValidationError):
        TenantExportRequest(expected_internal_secret="")


def test_export_stream_is_read_only_and_removes_helper(monkeypatch):
    class FakeContainer:
        attrs = {"Config": {"Env": ["WB_SAAS_INTERNAL_API_SECRET=scoped-secret"]}}

        def logs(self, **kwargs):
            assert kwargs == {"stream": True, "stdout": True, "stderr": False}
            yield b"archive"

        def remove(self, **kwargs):
            assert kwargs == {"force": True}
            self.removed = True

    class FakeClient:
        def __init__(self):
            self.container = FakeContainer()

        def containers_get(self, name):
            return self.container

        class Containers:
            pass

    client = FakeClient()
    client.containers = type("Containers", (), {})()
    client.containers.get = lambda name: client.container
    client.containers.run = lambda *args, **kwargs: client.container
    monkeypatch.setattr(app, "_client", lambda: client)
    response = app.export_tenant("shop-a", TenantExportRequest(expected_internal_secret="scoped-secret"))
    assert response.media_type == "application/gzip"
    async def read_body():
        return b"".join([chunk async for chunk in response.body_iterator])

    assert asyncio.run(read_body()) == b"archive"
    assert client.container.removed is True


def test_remove_tenant_purge_removes_volume(monkeypatch):
    from docker.errors import NotFound
    class Volume:
        def __init__(self):
            self.removed = False
            self.attrs = {"Mountpoint": "/volumes/wb-tenant-data-shop-a"}

        def reload(self):
            pass

        def remove(self, **kwargs):
            assert kwargs == {"force": False}
            self.removed = True

    class Volumes:
        def __init__(self):
            self.volume = Volume()

        def get(self, name):
            assert name == "wb-tenant-data-shop-a"
            return self.volume

    class Client:
        def __init__(self):
            self.volumes = Volumes()
            self.containers = type("Containers", (), {})()
            self.networks = type("Networks", (), {})()

    client = Client()
    client.containers.get = lambda name: (_ for _ in ()).throw(NotFound("already gone"))
    client.containers.list = lambda **kwargs: []
    client.networks.get = lambda name: (_ for _ in ()).throw(RuntimeError("already gone"))
    monkeypatch.setattr(app, "_client", lambda: client)
    assert app.remove_tenant("shop-a", purge_volume=True)["status"] == "purged"
    assert client.volumes.volume.removed is True
