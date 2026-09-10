from unittest.mock import Mock

import pytest
from pydantic import ValidationError

import app
from app import TenantRequest, tenant_container_spec


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
    assert labels[f"{prefix}.rule"] == "Host(`shop-a.app.example.test`) && Path(`/_stcore/health`) && Method(`GET`)"
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
