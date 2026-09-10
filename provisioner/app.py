"""Narrow internal control plane for Docker tenant lifecycle operations.

The public gateway deliberately has no Docker socket.  This service is its
only path to the daemon and builds every Docker argument itself, rather than
accepting a generic Docker API request.  Keep this contract small: adding a
new endpoint is a security-sensitive change, not a convenience wrapper.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
TENANT_IMAGE = os.environ.get("WB_SAAS_TENANT_IMAGE", "wb-dashboard-tenant:latest")
PARENT_DOMAIN = os.environ.get("WB_SAAS_PARENT_DOMAIN", "localhost")
TENANT_SUBDOMAIN_PREFIX = os.environ.get("WB_SAAS_TENANT_SUBDOMAIN_PREFIX", "app")
TENANT_INTERNAL_PORT = int(os.environ.get("WB_SAAS_TENANT_INTERNAL_PORT", "8501"))
TENANT_MEM_LIMIT = os.environ.get("WB_SAAS_TENANT_MEM_LIMIT", "768m")
TENANT_CPU_QUOTA = float(os.environ.get("WB_SAAS_TENANT_CPU_QUOTA", "1.0"))
FORWARD_AUTH_MIDDLEWARE = os.environ.get("WB_SAAS_FORWARD_AUTH_MIDDLEWARE", "wb-saas-auth@file")
TRAEFIK_CONTAINER_NAME = os.environ.get("WB_SAAS_TRAEFIK_CONTAINER_NAME", "wb-saas-traefik-1")
TLS_CERT_RESOLVER = os.environ.get("WB_SAAS_TLS_CERT_RESOLVER", "http-resolver")
BACKUP_DIR = os.environ.get("WB_SAAS_BACKUP_DIR", "/opt/wb-saas/backups")
BACKUP_MOUNT = Path("/backups")
BACKUP_HELPER_IMAGE = "alpine:3.20"


class TenantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    internal_api_secret: str = Field(min_length=1, max_length=512)
    billing_url: str = Field(min_length=1, max_length=512)
    logout_url: str = Field(min_length=1, max_length=512)
    internal_api_url: str = Field(min_length=1, max_length=512)
    require_auth: bool = True


class CredentialCheckRequest(BaseModel):
    expected_secret: str = Field(min_length=1, max_length=512)


def _client():
    import docker

    return docker.from_env()


def _validate_slug(slug: str) -> str:
    if not _SLUG.fullmatch(slug):
        raise HTTPException(status_code=422, detail="Invalid tenant slug")
    return slug


def _container_name(slug: str) -> str:
    return f"wb-tenant-{slug}"


def _volume_name(slug: str) -> str:
    return f"wb-tenant-data-{slug}"


def _network_name(slug: str) -> str:
    return f"wbsaas-tenant-{slug}"


def _tenant_host(slug: str) -> str:
    return f"{slug}.{TENANT_SUBDOMAIN_PREFIX}.{PARENT_DOMAIN}"


def _labels(slug: str, require_auth: bool) -> dict[str, str]:
    router = f"tenant-{slug}"
    labels = {
        "traefik.enable": "true",
        f"traefik.http.routers.{router}.rule": f"Host(`{_tenant_host(slug)}`)",
        f"traefik.http.routers.{router}.entrypoints": "websecure",
        f"traefik.http.routers.{router}.tls.certresolver": TLS_CERT_RESOLVER,
        f"traefik.http.services.{router}.loadbalancer.server.port": str(TENANT_INTERNAL_PORT),
        "traefik.docker.network": _network_name(slug),
        "wb-saas.slug": slug,
    }
    if require_auth:
        labels[f"traefik.http.routers.{router}.middlewares"] = FORWARD_AUTH_MIDDLEWARE
    return labels


def tenant_container_spec(slug: str, request: TenantRequest, *, demo: bool = False) -> dict[str, Any]:
    """Return the complete allowlisted Docker SDK argument set for one tenant.

    No caller-controlled image, mounts, namespaces, privileges, labels, or
    command are accepted.  This is the security boundary relied on when the
    public gateway is compromised.
    """
    _validate_slug(slug)
    network_name = _network_name(slug)
    volume_name = _volume_name(slug)
    environment = {
        "WB_SAAS_BILLING_URL": request.billing_url,
        "WB_SAAS_LOGOUT_URL": request.logout_url,
        "WB_SAAS_TENANT_SLUG": slug,
        "WB_SAAS_INTERNAL_API_URL": request.internal_api_url,
        "WB_SAAS_INTERNAL_API_SECRET": request.internal_api_secret,
        "MH_ALLOWED_HOSTS": f"localhost,127.0.0.1,{_tenant_host(slug)}",
    }
    if demo:
        environment["WB_SAAS_IS_DEMO"] = "1"
    return {
        "image": TENANT_IMAGE,
        "name": _container_name(slug),
        "detach": True,
        "network": network_name,
        "volumes": {volume_name: {"bind": "/app/data", "mode": "rw"}},
        "labels": _labels(slug, request.require_auth),
        "restart_policy": {"Name": "unless-stopped"},
        "mem_limit": TENANT_MEM_LIMIT,
        "nano_cpus": int(TENANT_CPU_QUOTA * 1_000_000_000),
        "user": "10001:10001",
        "security_opt": ["no-new-privileges:true"],
        "cap_drop": ["ALL"],
        "read_only": True,
        "tmpfs": {"/tmp": "rw,noexec,nosuid,size=64m"},
        "environment": environment,
    }


def _network(client: Any, slug: str):
    name = _network_name(slug)
    try:
        return client.networks.get(name)
    except Exception:
        return client.networks.create(name, driver="bridge")


def _volume(client: Any, slug: str):
    name = _volume_name(slug)
    try:
        return client.volumes.get(name)
    except Exception:
        return client.volumes.create(name)


def _attach_traefik(client: Any, network: Any) -> None:
    try:
        traefik = client.containers.get(TRAEFIK_CONTAINER_NAME)
        network.connect(traefik)
    except Exception:
        # Docker reports an error when already connected.  A missing Traefik
        # is handled by the gateway's normal health/certificate timeout.
        return


def _detach_traefik(client: Any, network: Any) -> None:
    try:
        network.disconnect(client.containers.get(TRAEFIK_CONTAINER_NAME), force=True)
    except Exception:
        return


def _remove_network(client: Any, slug: str) -> None:
    try:
        network = client.networks.get(_network_name(slug))
    except Exception:
        return
    try:
        network.disconnect(client.containers.get(_container_name(slug)), force=True)
    except Exception:
        pass
    _detach_traefik(client, network)
    try:
        network.remove()
    except Exception:
        return


def _prune_old_backups(slug: str, keep_days: int) -> int:
    if not 1 <= keep_days <= 3650:
        raise HTTPException(status_code=422, detail="keep_days must be between 1 and 3650")
    tenant_dir = BACKUP_MOUNT / slug
    tenant_dir.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for path in tenant_dir.glob("*.tar.gz"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/tenants/{slug}")
def create_tenant(slug: str, request: TenantRequest) -> dict[str, str]:
    _validate_slug(slug)
    client = _client()
    network = _network(client, slug)
    _volume(client, slug)
    _attach_traefik(client, network)
    try:
        client.containers.run(**tenant_container_spec(slug, request))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Docker create failed: {exc}") from exc
    return {"container": _container_name(slug)}


@app.get("/v1/tenants/{slug}/health")
def tenant_health(slug: str) -> dict[str, str | None]:
    _validate_slug(slug)
    try:
        container = _client().containers.get(_container_name(slug))
        container.reload()
        state = container.attrs.get("State", {})
        return {"status": state.get("Status"), "health": state.get("Health", {}).get("Status")}
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Tenant not found: {exc}") from exc


@app.post("/v1/tenants/{slug}/stop")
def stop_tenant(slug: str) -> dict[str, str]:
    _validate_slug(slug)
    client = _client()
    try:
        client.containers.get(_container_name(slug)).stop(timeout=15)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Tenant not found: {exc}") from exc
    _remove_network(client, slug)
    return {"status": "stopped"}


@app.delete("/v1/tenants/{slug}")
def remove_tenant(slug: str) -> dict[str, str]:
    _validate_slug(slug)
    client = _client()
    try:
        container = client.containers.get(_container_name(slug))
        container.stop(timeout=10)
        container.remove(force=True)
    except Exception:
        pass
    _remove_network(client, slug)
    return {"status": "removed"}


@app.post("/v1/tenants/{slug}/upgrade")
def upgrade_tenant(slug: str, request: TenantRequest) -> dict[str, str]:
    _validate_slug(slug)
    client = _client()
    try:
        client.containers.get(_container_name(slug)).stop(timeout=15)
        client.containers.get(_container_name(slug)).remove()
    except Exception:
        pass
    try:
        client.images.pull(TENANT_IMAGE)
    except Exception:
        # Local image tags are the normal deployment mode and do not pull.
        pass
    return create_tenant(slug, request)


@app.post("/v1/demo/reseed")
def reseed_demo() -> dict[str, str]:
    """Refresh only the fixed public demo tenant; no caller controls Docker args."""
    slug = "demo"
    client = _client()
    volume = _volume(client, slug)
    try:
        client.containers.get("wb-tenant-demo-seed").remove(force=True)
    except Exception:
        pass
    try:
        client.containers.run(
            TENANT_IMAGE,
            name="wb-tenant-demo-seed",
            remove=True,
            command=["python3", "seed_demo_data.py"],
            network_mode="none",
            volumes={volume.name: {"bind": "/app/data", "mode": "rw"}},
            user="10001:10001",
            security_opt=["no-new-privileges:true"],
            cap_drop=["ALL"],
            read_only=True,
            tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Demo seed failed: {exc}") from exc
    remove_tenant(slug)
    request = TenantRequest(
        internal_api_secret="demo-disabled-internal-api",
        billing_url="https://invalid.example/billing",
        logout_url="https://invalid.example/logout",
        internal_api_url="https://invalid.example",
        require_auth=False,
    )
    network = _network(client, slug)
    _attach_traefik(client, network)
    client.containers.run(**tenant_container_spec(slug, request, demo=True))
    return {"container": _container_name(slug)}


@app.post("/v1/tenants/{slug}/verify-internal-secret")
def verify_internal_secret(slug: str, request: CredentialCheckRequest) -> dict[str, bool]:
    """Compare one expected secret without exposing container environment data."""
    _validate_slug(slug)
    try:
        container = _client().containers.get(_container_name(slug))
        environment = container.attrs.get("Config", {}).get("Env", [])
        actual = next((value.split("=", 1)[1] for value in environment if value.startswith("WB_SAAS_INTERNAL_API_SECRET=")), "")
        return {"valid": actual == request.expected_secret}
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Tenant not found: {exc}") from exc


@app.post("/v1/tenants/{slug}/backup")
def backup_tenant(slug: str, keep_days: int = 14) -> dict[str, str | int]:
    """Create one archive using only fixed helper image, volume and host path."""
    _validate_slug(slug)
    tenant_dir = BACKUP_MOUNT / slug
    tenant_dir.mkdir(parents=True, exist_ok=True)
    archive_name = f"{slug}-{datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.gz"
    try:
        _client().containers.run(
            BACKUP_HELPER_IMAGE,
            command=["tar", "czf", f"/backup/{archive_name}", "-C", "/data", "."],
            volumes={
                _volume_name(slug): {"bind": "/data", "mode": "ro"},
                BACKUP_DIR: {"bind": "/backup", "mode": "rw"},
            },
            remove=True,
            network_disabled=True,
            security_opt=["no-new-privileges:true"],
            cap_drop=["ALL"],
            read_only=True,
            tmpfs={"/tmp": "rw,noexec,nosuid,size=16m"},
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Docker backup failed: {exc}") from exc
    return {"archive": archive_name, "pruned": _prune_old_backups(slug, keep_days)}
