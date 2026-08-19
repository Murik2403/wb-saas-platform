#!/usr/bin/env python3
"""Nightly host-level backup of every tenant's Docker volume.

Each tenant's SQLite database lives in a named Docker volume
(wb-tenant-data-<slug>), not inside the container itself -- a code upgrade
(`docker stop/rm` + recreate, see upgrade_all_tenants.py) never touches it.
But a lost or corrupted *host* (disk failure, an operator running the wrong
`docker volume rm`, a botched migration) would take every tenant's data
down at once. tenant-app/backup_tools.py already makes a daily SQLite-level
backup *inside* each tenant's own volume -- that protects against in-app
mistakes (e.g. bad manual edits) but not against losing the volume/host
itself, since it never leaves the volume it's protecting.

This script copies each volume's content out to a plain .tar.gz on the
HOST filesystem (config.BACKUP_DIR), using a short-lived `alpine` helper
container (the standard docker-volume-backup pattern: mount the volume
read-only, tar it, write into a second mount that's a real host directory).

A local-only backup on the same server does not survive losing that
server. Point a separate, standard tool (rsync/rclone/`aws s3 sync`/etc.)
at config.BACKUP_DIR to push copies off this host -- see DEPLOY.md for the
recommended cron line. This script deliberately does not attempt cloud
upload itself, to avoid hard-coding a choice of provider/credentials here.

Requires the `alpine` image to be pullable (or already cached) on the host
-- this cannot be exercised in the sandbox this was authored in (no Docker
Hub access there); see DEPLOY.md for the first-run pull.

Usage (from the repo root):
    python3 scripts/backup_tenant_volumes.py [--only slug1,slug2] [--keep-days 14]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

import config  # noqa: E402
from logic import db as control_db  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backup_tenant_volumes")

HELPER_IMAGE = "alpine:3.20"


def _client():
    import docker  # imported lazily -- keeps this script importable without docker-py installed

    return docker.from_env()


def _volume_name(slug: str) -> str:
    return f"wb-tenant-data-{slug}"


def _all_slugs() -> list[str]:
    """Every tenant that has ever been provisioned, not just currently
    'running' ones -- a cancelled client's last-known data is still worth
    keeping backed up until you actually decide to delete their volume."""
    with control_db.connect() as conn:
        rows = conn.execute("SELECT DISTINCT slug FROM tenant_instances ORDER BY slug").fetchall()
    return [str(r["slug"]) for r in rows]


def backup_one(client, slug: str, backup_dir: Path, stamp: str) -> Path | None:
    volume_name = _volume_name(slug)
    try:
        client.volumes.get(volume_name)
    except Exception:
        logger.warning("Skipping %s: volume %s not found (never provisioned or already removed)", slug, volume_name)
        return None

    tenant_dir = backup_dir / slug
    tenant_dir.mkdir(parents=True, exist_ok=True)
    archive_name = f"{slug}-{stamp}.tar.gz"

    client.containers.run(
        HELPER_IMAGE,
        command=["tar", "czf", f"/backup/{archive_name}", "-C", "/data", "."],
        volumes={
            volume_name: {"bind": "/data", "mode": "ro"},
            str(tenant_dir): {"bind": "/backup", "mode": "rw"},
        },
        remove=True,
    )
    return tenant_dir / archive_name


def prune_old_backups(tenant_dir: Path, keep_days: int) -> int:
    if not tenant_dir.exists():
        return 0
    import time

    cutoff = time.time() - keep_days * 86400
    removed = 0
    for path in tenant_dir.glob("*.tar.gz"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            logger.warning("Could not remove stale backup %s", path)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default=None, help="Comma-separated list of slugs to back up (default: all known tenants)")
    parser.add_argument("--keep-days", type=int, default=config.BACKUP_KEEP_DAYS, help="Delete a tenant's own backups older than this many days")
    parser.add_argument("--backup-dir", default=config.BACKUP_DIR, help="Host directory backups are written under (one subfolder per slug)")
    args = parser.parse_args()

    from datetime import datetime

    slugs = _all_slugs()
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        slugs = [s for s in slugs if s in wanted]

    if not slugs:
        print("No tenants to back up.")
        return 0

    backup_dir = Path(args.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    client = _client()

    print(f"Backing up {len(slugs)} tenant volume(s) to {backup_dir} ...")
    failures = []
    for slug in slugs:
        print(f"-> {slug} ...", end=" ", flush=True)
        try:
            result_path = backup_one(client, slug, backup_dir, stamp)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: {exc}")
            failures.append(slug)
            continue
        if result_path is None:
            print("SKIPPED (no volume)")
            continue
        removed = prune_old_backups(backup_dir / slug, args.keep_days)
        note = f" (pruned {removed} old)" if removed else ""
        print(f"OK -> {result_path.name}{note}")

    if failures:
        print(f"\n{len(failures)} tenant(s) failed to back up: {', '.join(failures)}")
        return 1
    print("\nAll volumes backed up successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
