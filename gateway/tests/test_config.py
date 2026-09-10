from __future__ import annotations

import importlib
import sys


def _reload_config() -> object:
    if "config" in sys.modules:
        return importlib.reload(sys.modules["config"])
    return importlib.import_module("config")


def test_tenant_sentry_dsn_falls_back_to_gateway_dsn(monkeypatch):
    monkeypatch.setenv("WB_SAAS_SENTRY_DSN", "https://gateway.example.ingest.sentry.io/1")
    monkeypatch.delenv("WB_SAAS_SENTRY_TENANT_DSN", raising=False)
    config = _reload_config()
    assert config.SENTRY_DSN == "https://gateway.example.ingest.sentry.io/1"
    assert config.SENTRY_TENANT_DSN == config.SENTRY_DSN


def test_tenant_sentry_dsn_can_be_overridden(monkeypatch):
    monkeypatch.setenv("WB_SAAS_SENTRY_DSN", "https://gateway.example.ingest.sentry.io/1")
    monkeypatch.setenv("WB_SAAS_SENTRY_TENANT_DSN", "https://tenant.example.ingest.sentry.io/2")
    config = _reload_config()
    assert config.SENTRY_DSN == "https://gateway.example.ingest.sentry.io/1"
    assert config.SENTRY_TENANT_DSN == "https://tenant.example.ingest.sentry.io/2"
