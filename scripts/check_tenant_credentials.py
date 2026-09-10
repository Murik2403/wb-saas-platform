"""Read-only post-migration check. Prints no tokens or container environment."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))
import config
import provisioning
from internal_auth import verify_credential
from logic import db


def main():
    if not config.INTERNAL_API_SECRET:
        print("Gateway master key is not configured")
        return 1
    client = provisioning._client()
    with db.connect() as conn:
        slugs = [row["slug"] for row in conn.execute("SELECT slug FROM tenant_instances WHERE status='running'")]
    failed = False
    for slug in slugs:
        try:
            container = client.containers.get(f"wb-tenant-{slug}")
            env = dict(value.split("=", 1) for value in container.attrs["Config"]["Env"] if "=" in value)
            valid = verify_credential(config.INTERNAL_API_SECRET, slug, env.get("WB_SAAS_INTERNAL_API_SECRET", ""))
        except Exception:
            valid = False
        print(f"{slug}: {'OK' if valid else 'MIGRATION REQUIRED'}")
        failed |= not valid
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
