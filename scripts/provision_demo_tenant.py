#!/usr/bin/env python3
"""Re-seed the fixed public demo through the constrained provisioner."""
from __future__ import annotations

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))
import config  # noqa: E402
import provisioning  # noqa: E402


def main() -> int:
    response = requests.post(f"{config.PROVISIONER_URL.rstrip('/')}/v1/demo/reseed", timeout=120)
    response.raise_for_status()
    healthy = provisioning._wait_until_healthy("demo", config.PROVISION_HEALTH_TIMEOUT_SECONDS)
    print("Demo tenant is", "healthy" if healthy else "NOT HEALTHY -- check provisioner logs")
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
