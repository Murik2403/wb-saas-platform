#!/usr/bin/env python3
"""Request a fixed, host-side tenant-volume backup through provisioner.

Run this inside the gateway container so it reads the live control-plane
database. It deliberately has no Docker SDK client: provisioner owns the
socket, helper image, host destination and volume mount policy.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

import config  # noqa: E402
from logic import db as control_db  # noqa: E402


def _all_slugs() -> list[str]:
    with control_db.connect() as conn:
        rows = conn.execute("SELECT DISTINCT slug FROM tenant_instances ORDER BY slug").fetchall()
    return [str(row["slug"]) for row in rows]


def _backup_one(slug: str, keep_days: int) -> dict[str, object]:
    response = requests.post(
        f"{config.PROVISIONER_URL.rstrip('/')}/v1/tenants/{slug}/backup",
        params={"keep_days": keep_days},
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Provisioner returned an invalid backup response")
    return payload


def _backup_control_plane(keep_days: int) -> dict[str, object]:
    response = requests.post(
        f"{config.PROVISIONER_URL.rstrip('/')}/v1/control-plane/backup",
        params={"keep_days": keep_days},
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Provisioner returned an invalid control-plane backup response")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default=None, help="Comma-separated tenant slugs (default: all known tenants)")
    parser.add_argument("--keep-days", type=int, default=config.BACKUP_KEEP_DAYS)
    args = parser.parse_args()

    slugs = _all_slugs()
    if args.only:
        requested = {slug.strip() for slug in args.only.split(",") if slug.strip()}
        slugs = [slug for slug in slugs if slug in requested]
    failures: list[str] = []
    try:
        result = _backup_control_plane(args.keep_days)
        print(f"control-plane: OK -> {result['archive']}")
    except Exception as exc:  # noqa: BLE001 -- tenant backups can still proceed
        print(f"control-plane: ERROR: {exc}")
        failures.append("control-plane")

    if not slugs:
        print("No tenant volumes to back up.")
    for slug in slugs:
        try:
            result = _backup_one(slug, args.keep_days)
            print(f"{slug}: OK -> {result['archive']}")
        except Exception as exc:  # noqa: BLE001 -- continue with other tenants
            print(f"{slug}: ERROR: {exc}")
            failures.append(slug)
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
