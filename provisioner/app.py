"""Narrow internal control plane for Docker tenant lifecycle operations.

The public gateway deliberately has no Docker socket.  This service is its
only path to the daemon and builds every Docker argument itself, rather than
accepting a generic Docker API request.  Keep this contract small: adding a
new endpoint is a security-sensitive change, not a convenience wrapper.
"""
from __future__ import annotations

import os
import json
import re
import sqlite3
import tarfile
import tempfile
import threading
import time
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from backup_crypto import decrypt_file, encrypt_file
from demo_refresh import DailyDemoRefresh
from demo_recovery import DemoRecoveryJournal

@asynccontextmanager
async def _lifespan(_app):
    start_daily_demo()
    try:
        yield
    finally:
        stop_daily_demo()


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=_lifespan)

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
TENANT_IMAGE = os.environ.get("WB_SAAS_TENANT_IMAGE", "wb-dashboard-tenant:redesign")
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
BACKUP_ENCRYPTION_KEY = os.environ.get("WB_SAAS_BACKUP_ENCRYPTION_KEY", "")
CONTROL_PLANE_BACKUP_GROUP = "control-plane"
_DEMO_RESEED_LOCK = threading.Lock()
_DEMO_REFRESH_STOP = threading.Event()
_DEMO_REFRESH_THREAD = None
_DEMO_DAILY = None


def _refresh_demo_and_healthcheck():
    reseed_demo()
    # Reseed commits only after health while its verified rollback is retained.


def _wait_demo_health(client):
    for _ in range(30):
        container = client.containers.get('wb-tenant-demo')
        container.reload()
        if container.attrs.get('State', {}).get('Health', {}).get('Status') == 'healthy':
            return
        time.sleep(2)
    raise RuntimeError('Demo health check failed')


def start_daily_demo():
    global _DEMO_DAILY, _DEMO_REFRESH_THREAD
    _recover_demo_reseed()
    _DEMO_DAILY = DailyDemoRefresh(BACKUP_MOUNT / 'demo', _refresh_demo_and_healthcheck,
        enabled=os.environ.get('WB_SAAS_DEMO_DAILY_REFRESH', '0') == '1',
        hour=int(os.environ.get('WB_SAAS_DEMO_REFRESH_HOUR_MOSCOW', '3')))
    if _DEMO_DAILY.enabled:
        _DEMO_REFRESH_STOP.clear()
        _DEMO_REFRESH_THREAD = threading.Thread(target=_DEMO_DAILY.run, args=(_DEMO_REFRESH_STOP,), daemon=True, name='demo-daily-refresh')
        _DEMO_REFRESH_THREAD.start()


def stop_daily_demo():
    _DEMO_REFRESH_STOP.set()
    if _DEMO_REFRESH_THREAD is not None:
        _DEMO_REFRESH_THREAD.join(timeout=15)


@app.get('/v1/demo/refresh-status')
def daily_demo_status():
    if _DEMO_DAILY is None:
        return {'enabled': False, 'last_result': 'not_started'}
    try:
        return _DEMO_DAILY.status()
    except Exception:
        raise HTTPException(status_code=503, detail='Demo refresh state unavailable') from None


class TenantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    internal_api_secret: str = Field(min_length=1, max_length=512)
    data_encryption_key: str = Field(default="", max_length=128)
    data_encryption_key_id: str = Field(default="current", min_length=1, max_length=128)
    data_encryption_keyring: str = Field(default="", max_length=8192)
    billing_url: str = Field(min_length=1, max_length=512)
    logout_url: str = Field(min_length=1, max_length=512)
    internal_api_url: str = Field(min_length=1, max_length=512)
    sentry_dsn: str = Field(default="", max_length=512)
    sentry_environment: str = Field(default="production", min_length=1, max_length=64)
    require_auth: bool = True


class CredentialCheckRequest(BaseModel):
    expected_secret: str = Field(min_length=1, max_length=512)


class TenantExportRequest(BaseModel):
    expected_internal_secret: str = Field(min_length=1, max_length=512)


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
    health_router = f"{router}-health"
    health_router_legacy = f"{router}-health-legacy"
    pwa_router = f"{router}-pwa"
    labels = {
        "traefik.enable": "true",
        f"traefik.http.routers.{router}.rule": f"Host(`{_tenant_host(slug)}`)",
        f"traefik.http.routers.{router}.entrypoints": "websecure",
        f"traefik.http.routers.{router}.tls.certresolver": TLS_CERT_RESOLVER,
        f"traefik.http.services.{router}.loadbalancer.server.port": str(TENANT_INTERNAL_PORT),
        "traefik.docker.network": _network_name(slug),
        "wb-saas.slug": slug,
        # A narrowly scoped liveness route lets an external monitor detect a
        # failed redesigned tenant without exposing the dashboard.
        f"traefik.http.routers.{health_router}.rule": (
            f"Host(`{_tenant_host(slug)}`) && Path(`/healthz`) && Method(`GET`)"
        ),
        f"traefik.http.routers.{health_router}.priority": "100",
        f"traefik.http.routers.{health_router}.entrypoints": "websecure",
        f"traefik.http.routers.{health_router}.tls.certresolver": TLS_CERT_RESOLVER,
        f"traefik.http.routers.{health_router}.service": router,
        f"traefik.http.routers.{health_router}.middlewares": f"{health_router}-rewrite",
        f"traefik.http.middlewares.{health_router}-rewrite.replacePath.path": "/_stcore/health",
        # Keep legacy streamlit tenants monitorable with the original health path.
        f"traefik.http.routers.{health_router_legacy}.rule": (
            f"Host(`{_tenant_host(slug)}`) && Path(`/_stcore/health`) && Method(`GET`)"
        ),
        f"traefik.http.routers.{health_router_legacy}.priority": "100",
        f"traefik.http.routers.{health_router_legacy}.entrypoints": "websecure",
        f"traefik.http.routers.{health_router_legacy}.tls.certresolver": TLS_CERT_RESOLVER,
        f"traefik.http.routers.{health_router_legacy}.service": router,
        # A phone must load these PWA bootstrap files before it can use the
        # authenticated dashboard. They contain no tenant data; all pages and
        # API requests continue through the authenticated primary router.
        f"traefik.http.routers.{pwa_router}.rule": (
            f"Host(`{_tenant_host(slug)}`) && "
            "(Path(`/manifest.webmanifest`) || Path(`/sw.js`) || Path(`/offline.html`) || PathPrefix(`/icons/`))"
        ),
        f"traefik.http.routers.{pwa_router}.priority": "100",
        f"traefik.http.routers.{pwa_router}.entrypoints": "websecure",
        f"traefik.http.routers.{pwa_router}.tls.certresolver": TLS_CERT_RESOLVER,
        f"traefik.http.routers.{pwa_router}.service": router,
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
    if request.data_encryption_key:
        environment["WB_SAAS_DATA_ENCRYPTION_KEY"] = request.data_encryption_key
        environment["WB_SAAS_DATA_ENCRYPTION_KEY_ID"] = request.data_encryption_key_id
    if request.data_encryption_keyring:
        environment["WB_SAAS_DATA_ENCRYPTION_KEYRING"] = request.data_encryption_keyring
    if request.sentry_dsn:
        environment["WB_SAAS_SENTRY_DSN"] = request.sentry_dsn
        environment["WB_SAAS_SENTRY_ENVIRONMENT"] = request.sentry_environment
    if demo:
        environment["WB_SAAS_IS_DEMO"] = "1"
    environment["MPLCONFIGDIR"] = "/tmp/matplotlib"
    environment["HOME"] = "/app"
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
        "tmpfs": {
            "/tmp": "rw,noexec,nosuid,size=64m",
            "/var/lib/nginx": "rw,noexec,nosuid,size=64m,uid=10001,gid=10001,mode=0777",
            "/var/lib/nginx/body": "rw,noexec,nosuid,size=16m",
            "/var/lib/nginx/proxy": "rw,noexec,nosuid,size=16m,uid=10001,gid=10001,mode=0777",
            "/var/lib/nginx/fastcgi": "rw,noexec,nosuid,size=16m,uid=10001,gid=10001,mode=0777",
            "/var/cache/nginx": "rw,noexec,nosuid,size=16m,uid=10001,gid=10001,mode=0777",
            "/var/log/nginx": "rw,noexec,nosuid,size=16m,uid=10001,gid=10001,mode=0777",
            "/run": "rw,noexec,nosuid,size=16m,uid=10001,gid=10001,mode=0777",
            "/run/nginx": "rw,noexec,nosuid,size=16m,uid=10001,gid=10001,mode=0777",
        },
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


def _control_plane_volume_name(client: Any) -> str:
    """Resolve this Compose project's gateway_data volume without caller input."""
    try:
        hostname = os.environ["HOSTNAME"]
        own_container = client.containers.get(hostname)
        labels = own_container.attrs.get("Config", {}).get("Labels", {}) or {}
        project = labels["com.docker.compose.project"]
        volumes = client.volumes.list(filters={"label": [
            f"com.docker.compose.project={project}",
            "com.docker.compose.volume=gateway_data",
        ]})
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Unable to resolve the control-plane volume") from exc
    if len(volumes) != 1 or not volumes[0].name:
        raise HTTPException(status_code=503, detail="Expected exactly one control-plane volume")
    return str(volumes[0].name)


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
    for path in tenant_dir.glob("*.tar.gz.enc"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def _backup_volume(volume_name: str, backup_group: str, keep_days: int, client: Any) -> dict[str, str | int]:
    """Archive one fixed Docker volume to an encrypted archive namespace."""
    if not BACKUP_ENCRYPTION_KEY:
        raise HTTPException(status_code=503, detail="WB_SAAS_BACKUP_ENCRYPTION_KEY is not configured")
    backup_dir = BACKUP_MOUNT / backup_group
    backup_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{backup_group}-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    archive_name = f"{stem}.tar.gz.enc"
    plain_name = f".{stem}.tar.gz.partial"
    plain_path = backup_dir / plain_name
    archive_path = backup_dir / archive_name
    try:
        client.containers.run(
            BACKUP_HELPER_IMAGE,
            command=["tar", "czf", f"/backup/{backup_group}/{plain_name}", "-C", "/data", "."],
            volumes={
                volume_name: {"bind": "/data", "mode": "ro"},
                BACKUP_DIR: {"bind": "/backup", "mode": "rw"},
            },
            remove=True,
            network_disabled=True,
            security_opt=["no-new-privileges:true"],
            cap_drop=["ALL"],
            read_only=True,
            tmpfs={"/tmp": "rw,noexec,nosuid,size=16m"},
        )
        encrypt_file(plain_path, archive_path, BACKUP_ENCRYPTION_KEY)
    except Exception as exc:
        plain_path.unlink(missing_ok=True)
        archive_path.unlink(missing_ok=True)
        raise HTTPException(status_code=502, detail=f"Docker backup failed: {exc}") from exc
    plain_path.unlink(missing_ok=True)
    return {"archive": archive_name, "pruned": _prune_old_backups(backup_group, keep_days)}


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
def remove_tenant(slug: str, purge_volume: bool = False) -> dict[str, str]:
    _validate_slug(slug)
    client = _client()
    try:
        container = client.containers.get(_container_name(slug))
        container.stop(timeout=10)
        container.remove(force=True)
    except Exception:
        pass
    _remove_network(client, slug)
    if purge_volume:
        try:
            client.volumes.get(_volume_name(slug)).remove(force=True)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Tenant data purge failed: {exc}") from exc
    return {"status": "purged" if purge_volume else "removed"}


def _verify_demo_backup(archive_name: str, slug: str) -> str:
    """Authenticate the encrypted archive and verify its tenant SQLite DB before use."""
    if Path(archive_name).name != archive_name or not archive_name.endswith(".tar.gz.enc"):
        raise ValueError("Invalid backup archive name")
    backup_dir = BACKUP_MOUNT / slug
    encrypted = backup_dir / archive_name
    with tempfile.TemporaryDirectory(prefix="demo-backup-verify-", dir=backup_dir) as temp_dir:
        plain = Path(temp_dir) / "verified.tar.gz"
        database = Path(temp_dir) / "verified.sqlite3"
        decrypt_file(encrypted, plain, BACKUP_ENCRYPTION_KEY)
        with tarfile.open(plain, "r:gz") as archive:
            db_member = None
            members = archive.getmembers()
            for member in members:
                path = Path(member.name)
                if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                    raise ValueError("Backup archive contains an unsafe entry")
                if path.name == "wb_dashboard.sqlite3" and member.isfile():
                    db_member = member
            if db_member is None:
                raise ValueError("Backup does not contain the tenant database")
            db_parent = Path(db_member.name).parent
            for member in members:
                path = Path(member.name)
                if path.parent != db_parent or path.name not in {
                    "wb_dashboard.sqlite3", "wb_dashboard.sqlite3-wal", "wb_dashboard.sqlite3-shm", "wb_dashboard.sqlite3-journal",
                }:
                    continue
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("Tenant database backup entry could not be read")
                suffix = path.name.removeprefix("wb_dashboard.sqlite3")
                (database.parent / f"verified.sqlite3{suffix}").write_bytes(source.read())
        conn = sqlite3.connect(database)
        try:
            result = conn.execute("PRAGMA quick_check").fetchone()
            if not result or result[0] != "ok":
                raise ValueError("Tenant database backup failed SQLite integrity check")
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"sales", "procurement_orders", "products_catalog"}.issubset(tables):
                raise ValueError("Backup is not a complete tenant database")
        finally:
            conn.close()
    # The Docker daemon resolves this same mounted backup directory at BACKUP_DIR.
    verified_name = f".{Path(archive_name).stem}.verified.tar.gz"
    verified_path = backup_dir / verified_name
    try:
        decrypt_file(encrypted, verified_path, BACKUP_ENCRYPTION_KEY)
        os.chmod(verified_path, 0o600)
    except Exception:
        verified_path.unlink(missing_ok=True)
        raise
    return verified_name


def _restore_demo_backup(volume_name: str, slug: str, verified_name: str, client: Any) -> None:
    # A failed seed can leave a new SQLite WAL beside the old database. Remove
    # only this tenant DB's sidecars before unpacking the verified snapshot.
    client.containers.run(
        BACKUP_HELPER_IMAGE,
        command=["rm", "-f", "/data/wb_dashboard.sqlite3-wal", "/data/wb_dashboard.sqlite3-shm", "/data/wb_dashboard.sqlite3-journal"],
        volumes={volume_name: {"bind": "/data", "mode": "rw"}},
        remove=True,
        network_disabled=True,
        security_opt=["no-new-privileges:true"],
        cap_drop=["ALL"],
        read_only=True,
        tmpfs={"/tmp": "rw,noexec,nosuid,size=16m"},
    )
    client.containers.run(
        BACKUP_HELPER_IMAGE,
        command=["tar", "xzf", f"/backup/{slug}/{verified_name}", "-C", "/data"],
        volumes={
            volume_name: {"bind": "/data", "mode": "rw"},
            BACKUP_DIR: {"bind": "/backup", "mode": "ro"},
        },
        remove=True,
        network_disabled=True,
        security_opt=["no-new-privileges:true"],
        cap_drop=["ALL"],
        read_only=True,
        tmpfs={"/tmp": "rw,noexec,nosuid,size=16m"},
    )
    check = client.containers.run(
        TENANT_IMAGE,
        entrypoint=["python3"],
        command=["-c", "import sqlite3; c=sqlite3.connect('file:/app/data/wb_dashboard.sqlite3?mode=ro',uri=True); print(c.execute('PRAGMA quick_check').fetchone()[0]); c.close()"],
        network_disabled=True,
        volumes={volume_name: {"bind": "/app/data", "mode": "ro"}},
        user="10001:10001",
        security_opt=["no-new-privileges:true"],
        cap_drop=["ALL"],
        read_only=True,
        tmpfs={"/tmp": "rw,noexec,nosuid,size=16m"},
    )
    if (check.decode("utf-8") if isinstance(check, bytes) else str(check)).strip() != "ok":
        raise ValueError("Restored demo database failed SQLite integrity check")


def _remove_partial_demo(client: Any, slug: str) -> None:
    try:
        partial = client.containers.get(_container_name(slug))
    except Exception:
        return
    try:
        partial.stop(timeout=10)
    except Exception:
        pass
    partial.remove(force=True)


def _validate_demo_seed(volume_name: str, client: Any) -> dict[str, Any]:
    """Check the seed marker and essential joined business/FIFO invariants read-only."""
    code = "import json; from demo_validation import validate_demo; r=validate_demo('/app/data/wb_dashboard.sqlite3'); print(json.dumps(r)); raise SystemExit(0 if r['ok'] else 1)"
    output = client.containers.run(
        TENANT_IMAGE,
        entrypoint=["python3"],
        command=["-c", code],
        network_disabled=True,
        volumes={volume_name: {"bind": "/app/data", "mode": "ro"}},
        user="10001:10001",
        security_opt=["no-new-privileges:true"],
        cap_drop=["ALL"],
        read_only=True,
        tmpfs={"/tmp": "rw,noexec,nosuid,size=16m"},
    )
    result = json.loads(output.decode("utf-8") if isinstance(output, bytes) else str(output))
    if result.get("ok") is not True:
        raise ValueError("Demo seed invariant validation failed")
    return result


def _start_demo_container(client: Any, slug: str) -> None:
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


def _recover_demo_reseed(client=None):
    """Finish a journaled rollback before serving lifecycle requests after crash."""
    journal = DemoRecoveryJournal(BACKUP_MOUNT/'demo')
    state = journal.read()
    if state is None:
        return
    client = client or _client()
    original = client.containers.get(state['original_id'])
    original.reload()
    if state['phase']=='stopping':
        if original.attrs.get('State',{}).get('Status')!='running':
            original.start()
        journal.clear()
        return
    if not BACKUP_ENCRYPTION_KEY:
        raise RuntimeError('Demo recovery requires encrypted backup configuration')
    try:
        seed = client.containers.get('wb-tenant-demo-seed')
    except Exception:
        seed = None
    if seed is not None:
        seed.stop(timeout=15)
        seed.remove(force=True)
    try:
        candidate = client.containers.get('wb-tenant-demo')
    except Exception:
        candidate = None
    if candidate is not None and candidate.id!=original.id:
        candidate.stop(timeout=15)
        candidate.remove(force=True)
    original.reload()
    if original.attrs.get('State',{}).get('Status')=='running':
        original.stop(timeout=15)
    verified = _verify_demo_backup(state['archive'],'demo')
    try:
        _restore_demo_backup(_volume_name('demo'),'demo',verified,client)
        if original.name!='wb-tenant-demo':
            original.rename('wb-tenant-demo')
        original.start()
        _wait_demo_health(client)
        journal.clear()
    finally:
        (BACKUP_MOUNT/'demo'/verified).unlink(missing_ok=True)


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
    """Reseed the fixed public demo tenant with a verified rollback snapshot."""
    if not BACKUP_ENCRYPTION_KEY:
        raise HTTPException(status_code=503, detail="Demo reseed requires configured encrypted backups")
    if not _DEMO_RESEED_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Demo reseed is already in progress")
    slug = "demo"
    verified_name = ""
    old = None
    stopped = False
    replaced = False
    journal = DemoRecoveryJournal(BACKUP_MOUNT/'demo')
    try:
        client = _client()
        _recover_demo_reseed(client)
        volume = _volume(client, slug)
        try:
            old = client.containers.get(_container_name(slug))
            old.reload()
        except Exception:
            raise HTTPException(status_code=409, detail="Demo tenant must be running before reseed") from None
        if old.attrs.get("State", {}).get("Status") != "running":
            raise HTTPException(status_code=409, detail="Demo tenant must be running before reseed")
        try:
            client.containers.get("wb-tenant-demo-seed")
        except Exception:
            pass
        else:
            raise HTTPException(status_code=409, detail="A demo seed container is already present")

        journal.write('stopping',old.id)
        old.stop(timeout=15)
        stopped = True
        try:
            backup = _backup_volume(volume.name, slug, 14, client)
            verified_name = _verify_demo_backup(str(backup["archive"]), slug)
            journal.write('mutating',old.id,str(backup['archive']))
        except Exception:
            old.start()
            journal.clear()
            stopped = False
            raise HTTPException(status_code=502, detail="Demo backup could not be verified; previous tenant restarted") from None

        try:
            client.containers.run(
                TENANT_IMAGE,
                name="wb-tenant-demo-seed",
                remove=True,
                entrypoint=["python3"],
                command=["seed_demo_data.py"],
                environment={"WB_SAAS_IS_DEMO": "1", "WB_SAAS_TENANT_SLUG": "demo"},
                network_mode="none",
                volumes={volume.name: {"bind": "/app/data", "mode": "rw"}},
                user="10001:10001",
                security_opt=["no-new-privileges:true"],
                cap_drop=["ALL"],
                read_only=True,
                tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},
            )
            _validate_demo_seed(volume.name, client)
        except Exception:
            try:
                _restore_demo_backup(volume.name, slug, verified_name, client)
            except Exception:
                stopped = False
                raise HTTPException(status_code=502, detail="Demo seed failed; rollback verification failed and tenant remains stopped") from None
            old.start()
            journal.clear()
            stopped = False
            raise HTTPException(status_code=502, detail="Demo seed failed validation; previous tenant restored") from None

        # Keep original image/configuration until candidate runtime is healthy.
        rollback_name = f"wb-tenant-demo-refresh-rollback-{time.time_ns()}"
        try:
            old.rename(rollback_name)
        except Exception:
            try:
                _restore_demo_backup(volume.name, slug, verified_name, client)
                old.start()
                journal.clear()
            except Exception:
                stopped = False
                raise HTTPException(status_code=502, detail="Demo replacement failed; rollback could not be verified") from None
            stopped = False
            raise HTTPException(status_code=502, detail="Demo replacement failed; previous tenant restored") from None
        replaced = True
        try:
            _start_demo_container(client, slug)
            _wait_demo_health(client)
        except Exception:
            try:
                _remove_partial_demo(client, slug)
                _restore_demo_backup(volume.name, slug, verified_name, client)
                old.rename(_container_name(slug))
                old.start()
                journal.clear()
            except Exception:
                stopped = False
                raise HTTPException(status_code=502, detail="Demo recreate failed; rollback could not be verified") from None
            stopped = False
            replaced = False
            raise HTTPException(status_code=502, detail="Demo recreate failed; previous tenant restored") from None
        stopped = False
        journal.clear()
        try:
            old.remove(force=True)
        except Exception:
            pass  # Healthy candidate remains live; stale stopped rollback is safe.
        return {"container": _container_name(slug)}
    except HTTPException:
        raise
    except Exception:
        # Never restart a potentially half-seeded DB; durable recovery owns it.
        try:
            _recover_demo_reseed(client)
        except Exception:
            pass
        if replaced:
            raise HTTPException(status_code=502, detail="Demo reseed failed after replacement; check tenant recovery") from None
        raise HTTPException(status_code=502, detail="Demo reseed failed; previous tenant preserved") from None
    finally:
        if verified_name:
            try:
                (BACKUP_MOUNT / slug / verified_name).unlink(missing_ok=True)
            except OSError:
                pass
        _DEMO_RESEED_LOCK.release()


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


def _tenant_internal_secret_matches(slug: str, expected: str, client: Any) -> bool:
    import hmac

    try:
        container = client.containers.get(_container_name(slug))
        environment = container.attrs.get("Config", {}).get("Env", [])
        actual = next(
            (value.split("=", 1)[1] for value in environment if value.startswith("WB_SAAS_INTERNAL_API_SECRET=")),
            "",
        )
        return bool(actual) and hmac.compare_digest(actual, expected)
    except Exception:
        return False


@app.post("/v1/tenants/{slug}/export")
def export_tenant(slug: str, request: TenantExportRequest):
    """Stream a read-only tenant archive after gateway-side ownership proof."""
    _validate_slug(slug)
    client = _client()
    if not _tenant_internal_secret_matches(slug, request.expected_internal_secret, client):
        raise HTTPException(status_code=403, detail="Invalid tenant credential")
    helper = None
    try:
        helper = client.containers.run(
            BACKUP_HELPER_IMAGE,
            command=["tar", "czf", "-", "-C", "/data", "."],
            detach=True,
            remove=False,
            network_mode="none",
            volumes={_volume_name(slug): {"bind": "/data", "mode": "ro"}},
            user="0:0",
            security_opt=["no-new-privileges:true"],
            cap_drop=["ALL"],
            read_only=True,
            tmpfs={"/tmp": "rw,noexec,nosuid,size=16m"},
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Docker export failed: {exc}") from exc

    def stream():
        try:
            yield from helper.logs(stream=True, stdout=True, stderr=False)
        finally:
            try:
                helper.remove(force=True)
            except Exception:
                pass

    return StreamingResponse(
        stream(),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="marketshelper-{slug}.tar.gz"'},
    )


@app.post("/v1/tenants/{slug}/backup")
def backup_tenant(slug: str, keep_days: int = 14) -> dict[str, str | int]:
    """Create an encrypted archive using only fixed helper image and paths."""
    _validate_slug(slug)
    return _backup_volume(_volume_name(slug), slug, keep_days, _client())


@app.post("/v1/control-plane/backup")
def backup_control_plane(keep_days: int = 14) -> dict[str, str | int]:
    """Back up only this Compose project's gateway_data volume."""
    client = _client()
    volume_name = _control_plane_volume_name(client)
    return _backup_volume(volume_name, CONTROL_PLANE_BACKUP_GROUP, keep_days, client)
