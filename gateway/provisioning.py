"""Tenant lifecycle client for the private, constrained provisioner service.

The gateway intentionally has no Docker SDK client and no Docker socket. It
can request only named tenant operations from ``provisioner``; the service
holding the socket rebuilds and validates every Docker argument itself.
"""
from __future__ import annotations

import logging
import time

import requests

import config
from internal_auth import tenant_credential
from logic import accounts, billing, db as control_db

logger = logging.getLogger("wb_saas_gateway.provisioning")
_REQUEST_TIMEOUT_SECONDS = 20


def _provisioner_url() -> str:
    return config.PROVISIONER_URL.rstrip("/")


def _tenant_payload(slug: str, require_auth: bool = True) -> dict[str, object]:
    return {
        "internal_api_secret": tenant_credential(config.INTERNAL_API_SECRET, slug),
        "billing_url": config.billing_url(),
        "logout_url": config.logout_url(),
        "internal_api_url": config.base_url(),
        "require_auth": require_auth,
    }


def _request(method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    response = requests.request(
        method,
        f"{_provisioner_url()}{path}",
        json=payload,
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise RuntimeError("Provisioner returned an invalid response")
    return body


def _run_tenant_container(slug: str, require_auth: bool = True, image: str | None = None) -> None:
    """Ask the provisioner to create one tenant from its fixed allowlist.

    ``image`` remains only for backward-compatible callers; arbitrary image
    overrides are deliberately ignored. The provisioner accepts the single
    configured tenant image so a gateway compromise cannot start a host-level
    helper container from an attacker-selected image.
    """
    if image and image != config.TENANT_IMAGE:
        logger.warning("Ignoring non-configured tenant image override")
    _request("POST", f"/v1/tenants/{slug}", _tenant_payload(slug, require_auth))


def _wait_until_healthy(slug: str, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            state = _request("GET", f"/v1/tenants/{slug}/health")
            health = state.get("health")
            if health == "healthy":
                return True
            if health == "unhealthy" or state.get("status") != "running":
                return False
        except Exception:
            pass
        time.sleep(3)
    return False


def provision_tenant_background(account_id: int, tenant_id: int, slug: str) -> None:
    """Provision after registration without allowing Docker access in gateway."""
    try:
        _run_tenant_container(slug)
        healthy = _wait_until_healthy(slug, config.PROVISION_HEALTH_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 -- status must be persisted for every infrastructure failure
        logger.exception("Provisioning failed for account %s (slug=%s)", account_id, slug)
        _cleanup_failed_tenant(slug)
        _mark_failed(tenant_id, f"Provisioner error: {exc}")
        return

    if not healthy:
        logger.error("Tenant container %s never became healthy within timeout", slug)
        _cleanup_failed_tenant(slug)
        _mark_failed(tenant_id, "Контейнер не прошёл health-check вовремя.")
        return

    _wait_for_tenant_cert(slug, timeout_seconds=45)
    with control_db.connect() as conn:
        accounts.mark_tenant_provisioned(conn, tenant_id, account_id)
        billing.start_trial(conn, account_id)
    logger.info("Tenant %s (account %s) provisioned successfully, trial started", slug, account_id)


def _mark_failed(tenant_id: int, note: str) -> None:
    with control_db.connect() as conn:
        accounts.set_tenant_status(conn, tenant_id, "failed", note=note)


def _wait_for_tenant_cert(slug: str, timeout_seconds: int = 45) -> bool:
    """Wait until Traefik serves a trusted certificate for a tenant host."""
    import socket
    import ssl

    try:
        import certifi

        context = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        context = ssl.create_default_context()

    host = config.tenant_host(slug)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((config.TRAEFIK_CONTAINER_NAME, 443), timeout=5) as sock:
                with context.wrap_socket(sock, server_hostname=host):
                    return True
        except Exception:
            pass
        time.sleep(3)
    logger.warning("Cert for %s not confirmed within %ss -- proceeding anyway", host, timeout_seconds)
    return False


def _cleanup_failed_tenant(slug: str) -> None:
    try:
        _request("DELETE", f"/v1/tenants/{slug}")
    except Exception:
        logger.warning("Failed to clean up tenant %s through provisioner", slug, exc_info=True)


def stop_tenant(slug: str) -> None:
    try:
        _request("POST", f"/v1/tenants/{slug}/stop")
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            logger.warning("stop_tenant: tenant %s not found", slug)
            return
        raise
    with control_db.connect() as conn:
        tenant = accounts.get_tenant_by_slug(conn, slug)
        if tenant is not None:
            accounts.set_tenant_status(conn, int(tenant["id"]), "stopped")


def upgrade_tenant(slug: str, image: str | None = None) -> bool:
    if image and image != config.TENANT_IMAGE:
        logger.warning("Ignoring non-configured tenant image override")
    _request("POST", f"/v1/tenants/{slug}/upgrade", _tenant_payload(slug))
    healthy = _wait_until_healthy(slug, config.PROVISION_HEALTH_TIMEOUT_SECONDS)
    if healthy:
        with control_db.connect() as conn:
            tenant = accounts.get_tenant_by_slug(conn, slug)
            if tenant is not None:
                accounts.set_tenant_status(conn, int(tenant["id"]), "running")
    return healthy
