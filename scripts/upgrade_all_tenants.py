#!/usr/bin/env python3
"""Roll a new tenant-app image out to every currently-running tenant.

Run this on the VPS after `docker build -t wb-dashboard-tenant:latest
./tenant-app` picks up a code fix. Each tenant is stopped, recreated from
the new image on its *same* data volume, and health-checked before moving
on to the next -- so a bad build stops the rollout instead of taking down
every client at once.

Usage (from the repo root, with the gateway's venv/deps available):
    python3 scripts/upgrade_all_tenants.py [--image wb-dashboard-tenant:latest]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

from logic import accounts, db as control_db  # noqa: E402
import provisioning  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=None, help="Override the image tag (defaults to config.TENANT_IMAGE)")
    parser.add_argument("--only", default=None, help="Comma-separated list of slugs to upgrade (default: all running tenants)")
    args = parser.parse_args()

    with control_db.connect() as conn:
        rows = conn.execute("SELECT slug FROM tenant_instances WHERE status='running' ORDER BY slug").fetchall()
    slugs = [r["slug"] for r in rows]
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        slugs = [s for s in slugs if s in wanted]

    if not slugs:
        print("No running tenants to upgrade.")
        return 0

    print(f"Upgrading {len(slugs)} tenant(s): {', '.join(slugs)}")
    failures = []
    for slug in slugs:
        print(f"-> {slug} ...", end=" ", flush=True)
        try:
            ok = provisioning.upgrade_tenant(slug, image=args.image)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: {exc}")
            failures.append(slug)
            continue
        print("OK" if ok else "FAILED health check")
        if not ok:
            failures.append(slug)

    if failures:
        print(f"\n{len(failures)} tenant(s) need attention: {', '.join(failures)}")
        return 1
    print("\nAll upgraded successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
