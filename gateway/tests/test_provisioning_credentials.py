from unittest.mock import patch
import provisioning
import config
from internal_auth import tenant_data_key, verify_credential


def test_container_gets_scoped_key_and_requested_image():
    with patch.object(config, "INTERNAL_API_SECRET", "test-master"), patch.object(config, "TENANT_ENCRYPTION_MASTER_KEY", "encryption-master"):
        payload = provisioning._tenant_payload("shop-a")
    token = payload["internal_api_secret"]
    assert token != "test-master"
    assert verify_credential("test-master", "shop-a", token)
    assert not verify_credential("test-master", "shop-b", token)
    assert payload["data_encryption_key"] == tenant_data_key("encryption-master", "shop-a")
    assert payload["data_encryption_key"] != "encryption-master"


def test_gateway_calls_only_named_provisioner_route():
    with patch.object(provisioning, "_request") as request:
        provisioning._run_tenant_container("shop-a", image="test-image:v2")
    method, path, payload = request.call_args.args
    assert method == "POST"
    assert path == "/v1/tenants/shop-a"
    assert payload["require_auth"] is True


def test_tenant_payload_includes_sentry_if_configured():
    with (
        patch.object(config, "SENTRY_TENANT_DSN", "https://tenant.example.ingest.sentry.io/123"),
        patch.object(config, "SENTRY_ENVIRONMENT", "staging"),
        patch.object(config, "SENTRY_DSN", "https://gateway.example.ingest.sentry.io/1"),
        patch.object(config, "TENANT_ENCRYPTION_MASTER_KEY", "encryption-master"),
        patch.object(config, "INTERNAL_API_SECRET", "test-master"),
    ):
        payload = provisioning._tenant_payload("shop-a")

    assert payload["sentry_dsn"] == "https://tenant.example.ingest.sentry.io/123"
    assert payload["sentry_environment"] == "staging"


def test_tenant_payload_omits_sentry_when_missing():
    with (
        patch.object(config, "SENTRY_TENANT_DSN", ""),
        patch.object(config, "SENTRY_DSN", "https://gateway.example.ingest.sentry.io/1"),
        patch.object(config, "SENTRY_ENVIRONMENT", "production"),
        patch.object(config, "TENANT_ENCRYPTION_MASTER_KEY", "encryption-master"),
        patch.object(config, "INTERNAL_API_SECRET", "test-master"),
    ):
        payload = provisioning._tenant_payload("shop-a")

    assert payload["sentry_dsn"] == ""
    assert payload["sentry_environment"] == "production"
