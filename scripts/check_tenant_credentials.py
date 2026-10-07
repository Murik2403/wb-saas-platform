"""Read-only credential migration check without Docker access in gateway."""
from pathlib import Path
import sys

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))
import config
from provisioning import provisioner_headers
from internal_auth import tenant_credential
from logic import db


def main() -> int:
    if not config.INTERNAL_API_SECRET:
        print("Gateway master key is not configured")
        return 1
    with db.connect() as conn:
        slugs = [row["slug"] for row in conn.execute("SELECT slug FROM tenant_instances WHERE status='running'")]
    failed = False
    for slug in slugs:
        try:
            response = requests.post(
                f"{config.PROVISIONER_URL.rstrip('/')}/v1/tenants/{slug}/verify-internal-secret",
                json={"expected_secret": tenant_credential(config.INTERNAL_API_SECRET, slug)},
                headers=provisioner_headers(),
                timeout=20,
            )
            valid = response.ok and response.json().get("valid") is True
        except Exception:
            valid = False
        print(f"{slug}: {'OK' if valid else 'MIGRATION REQUIRED'}")
        failed |= not valid
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
