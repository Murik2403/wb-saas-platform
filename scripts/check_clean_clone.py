"""Validate that a checkout contains the sources required for a release.

This is a read-only gate.  It deliberately does not fetch, stage, commit,
delete, or modify files.  Run it from a clean clone before building an image:

    python scripts/check_clean_clone.py

The deployment helpers are included in the required set because a release
that can build but cannot be safely deployed from the same checkout is not
reproducible.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


REQUIRED_PATHS = (
    ".github/workflows/ci.yml",
    "gateway/Dockerfile",
    "gateway/requirements.lock",
    "provisioner/Dockerfile",
    "provisioner/requirements.lock",
    "tenant-app/Dockerfile",
    "tenant-app/requirements.lock",
    "mh-redesign-lab/Dockerfile.tenant-staging",
    "mh-redesign-lab/package.json",
    "mh-redesign-lab/package-lock.json",
    "mh-redesign-lab/src/App.tsx",
    "mh-redesign-lab/tenant-api/api_server.py",
    "mh-redesign-lab/tenant-api/tests/test_audit_regressions.py",
    "mh-redesign-lab/scripts/test-history-format.mjs",
    "scripts/deploy_verified_tenant.py",
    "scripts/verify_release_container.py",
)

SENSITIVE_TRACKED = re.compile(
    r"(?:^|/)(?:\.env(?:\.[^/]*)?|config\.yaml|data/|node_modules/|\.next/)"
    r"|\.tar(?:\.gz)?$|(?:^|/)\.venv(?:/|$)"
)


def is_allowed_example(path: str) -> bool:
    """Keep committed configuration templates out of the secret warning."""
    return Path(path).name in {".env.example", ".env.staging.example"}


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="checkout root (defaults to the current directory)",
    )
    args = parser.parse_args()
    root = args.root.resolve()

    try:
        git_root = Path(run_git(root, "rev-parse", "--show-toplevel")).resolve()
        head = run_git(git_root, "rev-parse", "HEAD")
        status = run_git(git_root, "status", "--porcelain")
        tracked = run_git(git_root, "ls-files").splitlines()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"RELEASE_GATE_ERROR git checkout unavailable: {exc}", file=sys.stderr)
        return 2

    failures: list[str] = []
    if status:
        failures.append(
            "working tree is not clean; review and commit the release source before building"
        )

    missing = [path for path in REQUIRED_PATHS if not (git_root / path).is_file()]
    if missing:
        failures.append("missing required source: " + ", ".join(missing))

    sensitive = [
        path
        for path in tracked
        if SENSITIVE_TRACKED.search(path) and not is_allowed_example(path)
    ]
    if sensitive:
        failures.append("sensitive/generated paths tracked: " + ", ".join(sensitive))

    dockerfile = git_root / "mh-redesign-lab/Dockerfile.tenant-staging"
    if dockerfile.is_file():
        docker_text = dockerfile.read_text(encoding="utf-8")
        if not re.search(
            r"^LABEL\s+org\.opencontainers\.image\.revision=\$REDESIGN_BUILD_ID\s*$",
            docker_text,
            re.MULTILINE,
        ):
            failures.append(
                "tenant Dockerfile does not expose REDESIGN_BUILD_ID as the OCI revision label"
            )

    print(f"RELEASE_GATE_HEAD {head}")
    print(f"RELEASE_GATE_STATUS {'clean' if not status else 'dirty'}")
    print(f"RELEASE_GATE_REQUIRED {len(REQUIRED_PATHS) - len(missing)}/{len(REQUIRED_PATHS)}")
    print(f"RELEASE_GATE_TRACKED_SENSITIVE {len(sensitive)}")
    if failures:
        for failure in failures:
            print(f"RELEASE_GATE_FAIL {failure}")
        return 1
    print("RELEASE_GATE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
