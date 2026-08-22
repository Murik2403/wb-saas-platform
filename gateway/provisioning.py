"""Docker orchestration: turns a freshly-registered account into a running,
routable tenant container.

Uses docker-py (the `docker` package) to talk to the local Docker daemon --
the same daemon Traefik and every tenant container run under. Cannot be
exercised in the sandbox this was authored in (no `docker` package install,
no Docker Hub access to pull a base image), so this module leans on
config.py + logic/accounts.py (both unit-tested) for anything that isn't
pure "call the Docker API" plumbing, to keep the untested surface area as
small as possible. Test for real on a VPS before onboarding a paying
customer -- see ../DEPLOY.md.
"""
from __future__ import annotations

import logging
import time

import config
from logic import accounts, billing, db as control_db

logger = logging.getLogger("wb_saas_gateway.provisioning")


def _client():
    import docker  # imported lazily so this module can be imported (e.g. by app.py) without docker-py installed

    return docker.from_env()


def _volume_name(slug: str) -> str:
    return f"wb-tenant-data-{slug}"


def ensure_network(client) -> None:
    try:
        client.networks.get(config.DOCKER_NETWORK)
    except Exception:
        logger.info("Creating docker network %s", config.DOCKER_NETWORK)
        client.networks.create(config.DOCKER_NETWORK, driver="bridge")


def ensure_volume(client, slug: str):
    name = _volume_name(slug)
    try:
        return client.volumes.get(name)
    except Exception:
        logger.info("Creating docker volume %s", name)
        return client.volumes.create(name)


def container_labels(slug: str) -> dict[str, str]:
    router = f"tenant-{slug}"
    host = config.tenant_host(slug)
    return {
        "traefik.enable": "true",
        f"traefik.http.routers.{router}.rule": f"Host(`{host}`)",
        f"traefik.http.routers.{router}.entrypoints": "websecure",
        f"traefik.http.routers.{router}.tls.certresolver": config.TLS_CERT_RESOLVER,
        f"traefik.http.routers.{router}.middlewares": config.FORWARD_AUTH_MIDDLEWARE,
        f"traefik.http.services.{router}.loadbalancer.server.port": str(config.TENANT_INTERNAL_PORT),
        # Purely informational -- lets you find "which client is this container" from `docker ps`/`docker inspect`.
        "wb-saas.slug": slug,
    }


def _run_tenant_container(client, slug: str, container_name: str):
    ensure_network(client)
    volume = ensure_volume(client, slug)
    return client.containers.run(
        config.TENANT_IMAGE,
        name=container_name,
        detach=True,
        network=config.DOCKER_NETWORK,
        volumes={volume.name: {"bind": "/app/data", "mode": "rw"}},
        labels=container_labels(slug),
        restart_policy={"Name": "unless-stopped"},
        mem_limit=config.TENANT_MEM_LIMIT,
        nano_cpus=int(config.TENANT_CPU_QUOTA * 1_000_000_000),
        # Lets the dashboard itself render a "Subscription" link and a
        # "Log out" control back to the gateway -- the tenant container has
        # no other way to know its own parent domain. Read by
        # tenant-app/config.py.
        environment={
            "WB_SAAS_BILLING_URL": config.billing_url(),
            "WB_SAAS_LOGOUT_URL": config.logout_url(),
        },
    )


def _wait_until_healthy(client, container_name: str, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            container = client.containers.get(container_name)
            container.reload()
            health = container.attrs.get("State", {}).get("Health", {})
            status = health.get("Status")
            if status == "healthy":
                return True
            if status == "unhealthy":
                return False
            if container.attrs.get("State", {}).get("Status") != "running":
                return False
        except Exception:
            pass
        time.sleep(3)
    return False


def provision_tenant_background(account_id: int, tenant_id: int, slug: str) -> None:
    """Entry point for FastAPI's BackgroundTasks (see app.py's /register route).

    Runs after the HTTP response for registration has already been sent, so
    it has as long as it needs (bounded by
    config.PROVISION_HEALTH_TIMEOUT_SECONDS) to pull/start the container and
    wait for its health check -- the user meanwhile sees the polling
    /provisioning page.
    """
    container_name = f"wb-tenant-{slug}"
    try:
        client = _client()
        _run_tenant_container(client, slug, container_name)
        healthy = _wait_until_healthy(client, container_name, config.PROVISION_HEALTH_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 -- any Docker failure must not crash the background task silently
        logger.exception("Provisioning failed for account %s (slug=%s)", account_id, slug)
        _mark_failed(tenant_id, f"Docker error: {exc}")
        return

    if not healthy:
        logger.error("Tenant container %s never became healthy within timeout", container_name)
        _mark_failed(tenant_id, "Контейнер не прошёл health-check вовремя.")
        return

    with control_db.connect() as conn:
        accounts.mark_tenant_provisioned(conn, tenant_id, account_id)
        billing.start_trial(conn, account_id)
    logger.info("Tenant %s (account %s) provisioned successfully, trial started", slug, account_id)


def _mark_failed(tenant_id: int, note: str) -> None:
    with control_db.connect() as conn:
        accounts.set_tenant_status(conn, tenant_id, "failed", note=note)
    try:
        from telegram_notifier import send_telegram_alert
        send_telegram_alert(f"<b>Provisioning Failed</b>\nTenant ID: {tenant_id}\nNote: {note}")
    except Exception:
        pass


# --------------------------------------------------------------------------
# Operational helpers -- not wired into any HTTP route yet, but needed for
# real operation: stopping a churned client's container, and rolling a code
# fix out to every existing tenant without touching their data.
# --------------------------------------------------------------------------

def stop_tenant(slug: str) -> None:
    client = _client()
    container_name = f"wb-tenant-{slug}"
    try:
        container = client.containers.get(container_name)
        container.stop(timeout=15)
    except Exception:
        logger.warning("stop_tenant: container %s not found or already stopped", container_name)
    with control_db.connect() as conn:
        tenant = accounts.get_tenant_by_slug(conn, slug)
        if tenant is not None:
            accounts.set_tenant_status(conn, int(tenant["id"]), "stopped")


def wake_tenant(slug: str) -> bool:
    """Wakes up a stopped tenant container and waits for it to become healthy."""
    client = _client()
    container_name = f"wb-tenant-{slug}"
    try:
        container = client.containers.get(container_name)
        if container.status != "running":
            logger.info("Waking up idle tenant container %s", container_name)
            container.start()
    except Exception:
        logger.info("Container %s not found during wake, re-running container", container_name)
        try:
            _run_tenant_container(client, slug, container_name)
        except Exception as exc:
            logger.exception("Failed to run tenant container %s during wake: %s", container_name, exc)
            return False

    healthy = _wait_until_healthy(client, container_name, config.PROVISION_HEALTH_TIMEOUT_SECONDS)
    if healthy:
        with control_db.connect() as conn:
            tenant = accounts.get_tenant_by_slug(conn, slug)
            if tenant is not None:
                accounts.mark_tenant_provisioned(conn, int(tenant["id"]), int(tenant["account_id"]))
    return healthy


def stop_idle_tenants(idle_minutes: int | None = None) -> int:
    """Stops containers for tenants that have been idle longer than idle_minutes."""
    if idle_minutes is None:
        idle_minutes = config.TENANT_IDLE_TIMEOUT_MINUTES
    if not config.IDLE_AUTO_STOP_ENABLED:
        return 0

    with control_db.connect() as conn:
        idle_list = accounts.get_idle_tenants(conn, idle_minutes)

    stopped_count = 0
    for tenant in idle_list:
        slug = tenant["slug"]
        logger.info("Stopping idle tenant %s (inactive for > %d minutes)", slug, idle_minutes)
        try:
            stop_tenant(slug)
            stopped_count += 1
        except Exception:
            logger.exception("Failed to stop idle tenant %s", slug)

    return stopped_count


def upgrade_tenant(slug: str, image: str | None = None) -> bool:
    """Recreates a tenant's container from a (presumably newer) image, on
    the same named volume -- data survives, code doesn't. Intended to be
    called once per active tenant when rolling out a fix; see DEPLOY.md for
    the loop that iterates every 'running' tenant.
    """
    client = _client()
    container_name = f"wb-tenant-{slug}"
    target_image = image or config.TENANT_IMAGE
    try:
        old = client.containers.get(container_name)
        old.stop(timeout=15)
        old.remove()
    except Exception:
        logger.warning("upgrade_tenant: no existing container %s to remove (continuing)", container_name)
    try:
        # Best-effort: if wb-dashboard-tenant is built locally (`docker build`,
        # no registry to pull from) this will fail and that's expected --
        # the already-present local image tag is used as-is.
        client.images.pull(target_image)
    except Exception:
        logger.info("upgrade_tenant: pull skipped for %s (using locally-built image)", target_image)
    _run_tenant_container(client, slug, container_name)
    return _wait_until_healthy(client, container_name, config.PROVISION_HEALTH_TIMEOUT_SECONDS)
