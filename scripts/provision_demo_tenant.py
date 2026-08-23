#!/usr/bin/env python3
"""Provisions (or re-seeds) the public 'demo' tenant embedded on the landing
page's "Как это выглядит" section via iframe.

Unlike a real customer tenant, this one:
  - is not tracked in the gateway's accounts/tenant_instances tables at all
    (no billing, no trial, nothing to churn);
  - has no ForwardAuth middleware on its Traefik router, so it's reachable
    without a login -- that's the whole point;
  - runs on entirely fictional data (tenant-app/seed_demo_data.py), never
    real WB API data;
  - has no WB_SAAS_IS_DEMO=1 environment gating: app.py hides Настройки and
    every other page except Сегодня/Обзор/Финансы for it, so nothing
    editable is reachable by an anonymous visitor.

Run this on the VPS after `docker build -t wb-dashboard-tenant:latest
./tenant-app` (same image every real tenant uses). Re-running re-seeds the
data and recreates the container -- safe to run again any time the demo
data should be refreshed.

Usage (from the repo root):
    python3 scripts/provision_demo_tenant.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

import config  # noqa: E402
import provisioning  # noqa: E402
from provisioning import _client, ensure_volume, _wait_until_healthy  # noqa: E402

SLUG = "demo"
CONTAINER_NAME = f"wb-tenant-{SLUG}"
SEED_CONTAINER_NAME = f"{CONTAINER_NAME}-seed"


def main() -> int:
    client = _client()
    volume = ensure_volume(client, SLUG)

    print("Seeding demo data...")
    try:
        existing_seed = client.containers.get(SEED_CONTAINER_NAME)
        existing_seed.remove(force=True)
    except Exception:
        pass
    client.containers.run(
        config.TENANT_IMAGE,
        name=SEED_CONTAINER_NAME,
        remove=True,
        # seed_demo_data.py makes no network calls at all -- no need to put
        # this one-off container on any network, let alone a tenant network.
        network_mode="none",
        volumes={volume.name: {"bind": "/app/data", "mode": "rw"}},
        entrypoint=["python3", "seed_demo_data.py"],
    )

    print("Recreating demo tenant container...")
    try:
        old = client.containers.get(CONTAINER_NAME)
        old.stop(timeout=15)
        old.remove()
    except Exception:
        pass

    # Same per-tenant network isolation as every real customer tenant (see
    # provisioning.ensure_tenant_network) -- the demo being public doesn't
    # mean it should be able to reach (or be reached by) any other tenant.
    network_name = provisioning._tenant_network_name(SLUG)
    provisioning.ensure_tenant_network(client, SLUG)
    provisioning._attach_traefik(client, network_name)
    client.containers.run(
        config.TENANT_IMAGE,
        name=CONTAINER_NAME,
        detach=True,
        network=network_name,
        volumes={volume.name: {"bind": "/app/data", "mode": "rw"}},
        # require_auth=False: every other tenant's router gets
        # config.FORWARD_AUTH_MIDDLEWARE, this one deliberately doesn't --
        # it's meant to be reachable without a login.
        labels=provisioning.container_labels(SLUG, require_auth=False),
        restart_policy={"Name": "unless-stopped"},
        mem_limit=config.TENANT_MEM_LIMIT,
        nano_cpus=int(config.TENANT_CPU_QUOTA * 1_000_000_000),
        environment={"WB_SAAS_IS_DEMO": "1"},
    )
    healthy = _wait_until_healthy(client, CONTAINER_NAME, config.PROVISION_HEALTH_TIMEOUT_SECONDS)
    print("Demo tenant is", "healthy" if healthy else "NOT HEALTHY -- check `docker logs wb-tenant-demo`")
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
