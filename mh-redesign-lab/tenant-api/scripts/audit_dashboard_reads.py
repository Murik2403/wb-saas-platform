from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request


PATHS = [
    "/api/v1/health",
    "/api/v1/session",
    "/api/v1/data-status",
    "/api/v1/dashboard/today",
    "/api/v1/dashboard/stock",
    "/api/v1/dashboard/ads?period=7d",
    "/api/overview?period=7d",
    "/api/v1/dashboard/production",
    "/api/v1/dashboard/supply",
    "/api/v1/dashboard/assistant",
    "/api/v1/dashboard/reports",
    "/api/products?period=7d",
    "/api/v1/control/data-quality",
    "/api/v1/settings",
    "/api/v1/settings/material-inventory",
    "/api/v1/settings/product-costs",
    "/api/v1/settings/production-norms",
    "/api/v1/operations/procurement",
    "/api/v1/operations/suppliers",
    "/api/v1/operations/reports",
    "/api/v1/operations/reports/delivery",
]


def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8501"
    failed = False
    for path in PATHS:
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(f"{base_url}{path}", timeout=30) as response:
                payload = response.read()
                json.loads(payload)
                elapsed = round((time.perf_counter() - started) * 1000)
                print(f"OK {response.status} {elapsed:>5}ms {len(payload):>8}b {path}")
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            failed = True
            elapsed = round((time.perf_counter() - started) * 1000)
            print(f"FAIL {elapsed:>5}ms {path}: {error}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
