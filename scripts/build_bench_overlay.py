"""Build a LOCAL benchmark tenant image from the COMMITTED sources of HEAD, on top of an existing tenant image.

A full ``docker build`` of mh-redesign-lab/Dockerfile.tenant-staging downloads npm and pip packages; a benchmark on a
stand without that network needs the same application code in an image it already has. This copies exactly what the
Dockerfile copies from ``tenant-app/`` and ``mh-redesign-lab/tenant-api/`` (taken with ``git archive``, so it is the
committed content with LF endings, never the working tree) over a base image, and labels the result:

  wb-saas.bench-head               the commit the sources came from (bench_pdf_slots_load compares its source trees with HEAD's)
  wb-saas.bench-base-image         id of the base image the dependencies, nginx and frontend come from
  wb-saas.bench-base-revision      the revision label of that base image

It refuses when the base would not be valid: a dirty tree, or any difference between the base's revision and HEAD in the
files that decide the dependencies/runtime (requirements, entrypoint, nginx config, the Dockerfile itself). Then only a full
build is honest. Files deleted since the base's revision stay in the image (an overlay cannot delete); the load scenario
hashes the files that matter inside every container.

    python scripts/build_bench_overlay.py --base mh-dr-tenant:fdcc301 --tag bench-tenant:obs
"""
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_tenant_density as base  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = "mh-redesign-lab/Dockerfile.tenant-staging"
ROOTS = ("tenant-app/", "mh-redesign-lab/tenant-api/")
RUNTIME_PATHS = ("tenant-app/requirements.txt", "tenant-app/requirements.lock", "tenant-app/docker-entrypoint.sh", "mh-redesign-lab/deploy", DOCKERFILE)
WORKDIR = "/app/tenant-app"


def git(*args: str, check: bool = True) -> str:
    done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and done.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {done.stderr.strip()[:200]}")
    return done.stdout.strip()


def copy_lines(dockerfile: str) -> list[tuple[list[str], str]]:
    """(sources, destination) of every COPY that takes sources from the two application trees; --from copies and the
    dependency manifests are not part of an overlay."""
    out = []
    for line in dockerfile.splitlines():
        tokens = line.strip().split()
        if not tokens or tokens[0] != "COPY" or any(t.startswith("--") for t in tokens[1:]) or len(tokens) < 3:
            continue
        sources, destination = tokens[1:-1], tokens[-1]
        if not all(s.startswith(ROOTS) for s in sources) or any(s.startswith("tenant-app/requirements") for s in sources):
            continue
        out.append((sources, destination))
    return out


def container_destination(destination: str) -> str:
    return destination if destination.startswith("/") else f"{WORKDIR}/{destination[2:] if destination.startswith('./') else destination}"


def render_dockerfile(base_image: str, lines: list[tuple[list[str], str]], labels: dict[str, str]) -> str:
    body = [f"FROM {base_image}", f"WORKDIR {WORKDIR}"]
    for sources, destination in lines:
        body.append("COPY " + " ".join(sources) + " " + container_destination(destination))
    body.append("LABEL " + " ".join(f'{key}="{value}"' for key, value in sorted(labels.items())))
    return "\n".join(body) + "\n"


def runtime_changes(base_revision: str) -> list[str]:
    return git("diff", "--name-only", base_revision, "HEAD", "--", *RUNTIME_PATHS).splitlines()


def stage_sources(destination: Path) -> None:
    archive = subprocess.run(["git", "archive", "--format=tar", "HEAD", "tenant-app", "mh-redesign-lab/tenant-api"], cwd=ROOT, capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(destination, filter="data")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True, help="existing local tenant image that provides dependencies, nginx and the frontend")
    parser.add_argument("--tag", required=True)
    args = parser.parse_args(argv)
    if git("status", "--porcelain"):
        raise SystemExit("Refusing to build from a dirty working tree: the image must name a commit.")
    base.assert_local_engine()
    info = json.loads(base.docker("image", "inspect", args.base))[0]
    base_revision = (info.get("Config", {}).get("Labels") or {}).get("org.opencontainers.image.revision", "")
    if not re.fullmatch(r"[0-9a-f]{40}", base_revision):
        raise SystemExit("Refusing: the base image has no full commit revision label, so its dependencies cannot be compared with HEAD.")
    changed = runtime_changes(base_revision)
    if changed:
        raise SystemExit(f"Refusing: dependencies/runtime files differ between the base ({base_revision[:7]}) and HEAD: {changed}. Do a full build.")
    head = git("rev-parse", "HEAD")
    lines = copy_lines(git("show", f"HEAD:{DOCKERFILE}"))
    labels = {"wb-saas.bench-head": head, "wb-saas.bench-base-image": info["Id"], "wb-saas.bench-base-revision": base_revision,
              "org.opencontainers.image.revision": head}
    with tempfile.TemporaryDirectory(prefix="bench-overlay-") as stage:
        stage_path = Path(stage)
        stage_sources(stage_path)
        (stage_path / "Dockerfile").write_text(render_dockerfile(args.base, lines, labels), encoding="utf-8")
        base.docker("build", "-q", "-t", args.tag, str(stage_path), timeout=900)
    built = json.loads(base.docker("image", "inspect", args.tag))[0]
    print(json.dumps({"tag": args.tag, "image_id": built["Id"], "head": head, "base_image": info["Id"], "base_revision": base_revision, "copied": len(lines)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
