"""Query npm's documented Bulk Advisory API for public locked packages.

Reference: https://docs.npmjs.com/cli/audit/#bulk-advisory-endpoint
Only public registry package names and versions are transmitted.
"""
import json
from pathlib import Path
import requests

root = Path(__file__).resolve().parents[1]
for project in ("frontend", "mh-redesign-lab"):
    lock = json.loads((root / project / "package-lock.json").read_text(encoding="utf-8"))
    packages = {}
    skipped = []
    for path, item in lock["packages"].items():
        if not path:
            continue
        if not item.get("version") or not item.get("resolved", "").startswith("https://registry.npmjs.org/"):
            skipped.append(path)
            continue
        name = item.get("name") or path.rsplit("node_modules/", 1)[-1]
        packages.setdefault(name, set()).add(item["version"])
    response = requests.post("https://registry.npmjs.org/-/npm/v1/security/advisories/bulk", json={name: sorted(versions) for name, versions in packages.items()}, timeout=30)
    response.raise_for_status()
    advisories = response.json()
    if not isinstance(advisories, dict):
        raise RuntimeError("Unexpected advisory response")
    report = {"package_count": len(packages), "skipped": skipped, "advisories": advisories}
    (root / "docs" / f"dependency-audit-{project}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(project, "packages:", len(packages), "advisory packages:", len(advisories), "skipped:", len(skipped))
