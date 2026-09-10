from unittest.mock import Mock

import pytest
from pydantic import ValidationError

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
