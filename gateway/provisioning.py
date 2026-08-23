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


def _tenant_network_name(slug: str) -> str:
    return f"wbsaas-tenant-{slug}"


def ensure_tenant_network(client, slug: str):
    """One dedicated bridge network per tenant, joined only by that tenant's
    own container and Traefik (see _attach_traefik). Docker allows any two
    containers on the same bridge network to talk to each other directly by
    default (inter-container communication is on unless the whole network
    opts out, and opting out would also block Traefik's own routing since
    Traefik is just another container on the network) -- putting every
    tenant on its own network is what actually stops tenant-to-tenant
    traffic, since containers on different networks simply have no route to
    each other at all.
    """
    name = _tenant_network_name(slug)
    try:
        return client.networks.get(name)
    except Exception:
        logger.info("Creating docker network %s", name)
        return client.networks.create(name, driver="bridge")


def _attach_traefik(client, network_name: str) -> None:
    """Traefik's Docker provider load-balances directly to a container's IP,
    so it needs L3 reachability into every tenant's dedicated network even
    though tenants no longer share one flat network with each other."""
    try:
        traefik = client.containers.get(config.TRAEFIK_CONTAINER_NAME)
    except Exception:
        logger.warning(
            "_attach_traefik: container %s not found -- network %s will not be routable",
            config.TRAEFIK_CONTAINER_NAME, network_name,
        )
        return
    network = client.networks.get(network_name)
    try:
        network.connect(traefik)
    except Exception as exc:
        # Already-connected is the expected case on every upgrade_tenant
        # call (Traefik stays attached across container recreations) --
        # docker-py has no dedicated exception for it, just log and move on.
        logger.debug("_attach_traefik: %s already on %s (%s)", config.TRAEFIK_CONTAINER_NAME, network_name, exc)


def _detach_traefik_and_remove_network(client, slug: str) -> None:
    """Cleanup counterpart to ensure_tenant_network/_attach_traefik, called
    when a tenant is stopped -- otherwise Traefik accumulates one network
    interface per churned client forever."""
    network_name = _tenant_network_name(slug)
    try:
        network = client.networks.get(network_name)
    except Exception:
        return
    try:
        traefik = client.containers.get(config.TRAEFIK_CONTAINER_NAME)
        network.disconnect(traefik, force=True)
    except Exception:
        pass
    try:
        network.remove()
    except Exception as exc:
        logger.warning("Could not remove tenant network %s: %s", network_name, exc)


def ensure_volume(client, slug: str):
    name = _volume_name(slug)
    try:
        return client.volumes.get(name)
    except Exception:
        logger.info("Creating docker volume %s", name)
        return client.volumes.create(name)


def container_labels(slug: str, require_auth: bool = True) -> dict[str, str]:
    router = f"tenant-{slug}"
    host = config.tenant_host(slug)
    labels = {
        "traefik.enable": "true",
        f"traefik.http.routers.{router}.rule": f"Host(`{host}`)",
        f"traefik.http.routers.{router}.entrypoints": "websecure",
        f"traefik.http.routers.{router}.tls.certresolver": config.TLS_CERT_RESOLVER,
        f"traefik.http.services.{router}.loadbalancer.server.port": str(config.TENANT_INTERNAL_PORT),
        # Traefik ends up attached to every per-tenant network plus its own
        # wbsaas_net -- this label removes any ambiguity about which network
        # to route this specific backend through.
        "traefik.docker.network": _tenant_network_name(slug),
        # Purely informational -- lets you find "which client is this container" from `docker ps`/`docker inspect`.
        "wb-saas.slug": slug,
    }
    if require_auth:
        labels[f"traefik.http.routers.{router}.middlewares"] = config.FORWARD_AUTH_MIDDLEWARE
    return labels


def _run_tenant_container(client, slug: str, container_name: str, require_auth: bool = True):
    network_name = _tenant_network_name(slug)
    ensure_tenant_network(client, slug)
    _attach_traefik(client, network_name)
    volume = ensure_volume(client, slug)
    return client.containers.run(
        config.TENANT_IMAGE,
        name=container_name,
        detach=True,
        network=network_name,
        volumes={volume.name: {"bind": "/app/data", "mode": "rw"}},
        labels=container_labels(slug, require_auth=require_auth),
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
    except Exception:
        logger.warning("stop_tenant: container %s not found", container_name)
        return
    container.stop(timeout=15)
    _detach_traefik_and_remove_network(client, slug)
    with control_db.connect() as conn:
        tenant = accounts.get_tenant_by_slug(conn, slug)
        if tenant is not None:
            accounts.set_tenant_status(conn, int(tenant["id"]), "stopped")


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
