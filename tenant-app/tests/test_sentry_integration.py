"""Contract tests for opt-in tenant error reporting."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

TENANT_ROOT = Path(__file__).resolve().parents[1]
if str(TENANT_ROOT) not in sys.path:
    sys.path.insert(0, str(TENANT_ROOT))

import sentry_integration  # noqa: E402


def test_initialization_is_a_no_op_without_a_dsn(monkeypatch):
    monkeypatch.delenv("WB_SAAS_SENTRY_DSN", raising=False)
    sentry_integration.initialize()


def test_initialization_uses_non_pii_configuration(monkeypatch):
    sdk = mock.Mock()
    monkeypatch.setenv("WB_SAAS_SENTRY_DSN", "https://key@example.ingest.sentry.io/1")
    monkeypatch.setenv("WB_SAAS_SENTRY_ENVIRONMENT", "staging")
    with mock.patch.dict(sys.modules, {"sentry_sdk": sdk}):
        sentry_integration.initialize()

    sdk.init.assert_called_once_with(
        dsn="https://key@example.ingest.sentry.io/1",
        environment="staging",
        send_default_pii=False,
    )
    sdk.set_tag.assert_called_once_with("service", "tenant-app")


def test_initialization_allows_service_override(monkeypatch):
    sdk = mock.Mock()
    monkeypatch.setenv("WB_SAAS_SENTRY_DSN", "https://key@example.ingest.sentry.io/1")
    with mock.patch.dict(sys.modules, {"sentry_sdk": sdk}):
        sentry_integration.initialize(service_name="tenant-api")

    sdk.init.assert_called_once_with(
        dsn="https://key@example.ingest.sentry.io/1",
        environment="production",
        send_default_pii=False,
    )
    sdk.set_tag.assert_called_once_with("service", "tenant-api")


def test_api_server_import_triggers_sentry_initialization():
    # The API server process should initialize tenant telemetry even when it is
    # running separately from Streamlit.
    with mock.patch("sentry_integration.initialize") as mocked_initialize:
        sys.modules.pop("api_server", None)
        import api_server  # noqa: F401

    mocked_initialize.assert_called_once_with(service_name="tenant-api")
