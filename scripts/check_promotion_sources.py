"""Read-only promotion packaging/route checks; does not import app code or deploy.

This source gate complements check_clean_clone.py, the test suites and an actual
isolated Docker build. Success does not assert that a release is deployed.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import shlex


GROWTH_ROUTES = (
    ("get", "/api/v1/growth/activation"),
    ("get", "/api/v1/growth/first-report"),
    ("post", "/api/v1/growth/report-viewed"),
)
HEALTH_ROUTES = (("get", "/healthz"), ("get", "/api/v1/health"))
FRONTEND_INPUTS = (
    "package.json", "package-lock.json", "index.html", "public", "src",
    "tsconfig.json", "tsconfig.node.json", "vite.config.ts",
)


def copy_sources(text: str) -> set[str]:
    """Source entries in local COPY instructions, excluding stage-to-stage copies."""
    result: set[str] = set()
    for line in re.sub(r"\\\s*\n", " ", text).splitlines():
        if not re.match(r"^\s*COPY\s", line, re.I):
            continue
        body = re.sub(r"^\s*COPY\s+", "", line, flags=re.I).strip()
        if "--from=" in body:
            continue
        while body.startswith("--"):
            body = body.split(None, 1)[1]
        parts = json.loads(body) if body.startswith("[") else shlex.split(body)
        result.update(str(source).rstrip("/") for source in parts[:-1])
    return result


def shipped(source: str, copies: set[str]) -> bool:
    return any(source == item or source.startswith(item.rstrip("/") + "/") for item in copies)


def api_routes(tree: ast.AST) -> set[tuple[str, str]]:
    routes = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                    and isinstance(decorator.func.value, ast.Name) and decorator.func.value.id == "app"
                    and decorator.args and isinstance(decorator.args[0], ast.Constant)):
                routes.add((decorator.func.attr.lower(), str(decorator.args[0].value)))
    return routes


def check_sources(root: Path) -> dict:
    root = root.resolve()
    failures: list[str] = []
    checks: list[str] = []
    warnings: list[str] = []
    lab = root / "mh-redesign-lab"
    api_dir = lab / "tenant-api"
    required = [lab / "Dockerfile.tenant-staging", api_dir / "api_server.py",
                lab / "src/data-source.ts", lab / "deploy/nginx.tenant-staging.conf"]
    for path in required:
        if not path.is_file():
            failures.append(f"Missing source: {path.relative_to(root).as_posix()}")
    if failures:
        return {"checks": checks, "failures": failures, "warnings": warnings}
    docker_text = required[0].read_text(encoding="utf-8")
    try:
        copies = copy_sources(docker_text)
    except (ValueError, IndexError) as exc:
        failures.append(f"Unsupported or malformed COPY syntax: {type(exc).__name__}")
        return {"checks": checks, "failures": failures, "warnings": warnings}
    for source in sorted(copies):
        if "$" not in source and "*" not in source and not (root / source).exists():
            failures.append(f"Docker COPY source is missing: {source}")

    # Follow local imports without importing modules (no app startup, keyring,
    # credentials, databases, background workers or remote requests).
    pending = [api_dir / "api_server.py"]
    inspected: set[Path] = set()
    api_tree = None
    while pending:
        path = pending.pop()
        if path in inspected:
            continue
        inspected.add(path)
        relative = path.relative_to(root).as_posix()
        if not shipped(relative, copies):
            failures.append(f"Local tenant API module has no Docker COPY: {relative}")
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=relative)
        except (SyntaxError, UnicodeError) as exc:
            failures.append(f"Cannot parse {relative}: {type(exc).__name__}")
            continue
        if path.name == "api_server.py":
            api_tree = tree
        for node in ast.walk(tree):
            names = ([node.module] if isinstance(node, ast.ImportFrom) and node.module else
                     [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
            for name in names:
                dependency = api_dir / (name.split(".")[0] + ".py")
                if dependency.is_file() and dependency not in inspected:
                    pending.append(dependency)
    checks.append(f"Tenant API local imports examined: {len(inspected)}")

    frontend = required[2].read_text(encoding="utf-8")
    routes = api_routes(api_tree) if api_tree else set()
    for method, route in GROWTH_ROUTES + HEALTH_ROUTES:
        if (method, route) not in routes:
            failures.append(f"Missing API contract: {method.upper()} {route}")
    for _, route in GROWTH_ROUTES:
        if not re.search(re.escape(route) + r'(?=$|[?`"\s$])', frontend):
            failures.append(f"Missing frontend route contract: {route}")
    checks.append("Growth and health route contracts examined")

    for name in FRONTEND_INPUTS:
        source = f"mh-redesign-lab/{name}"
        if not (root / source).exists():
            failures.append(f"Missing frontend build input: {source}")
        elif not shipped(source, copies):
            failures.append(f"Frontend build input has no Docker COPY: {source}")
    if not re.search(r"^RUN\s+npm run build\s*$", docker_text, re.M):
        failures.append("Docker frontend stage does not run npm run build")
    if not re.search(r"^COPY\s+--from=frontend-build\s+/frontend/dist\s+/usr/share/nginx/html\s*$", docker_text, re.M):
        failures.append("Compiled frontend is not copied into the tenant image")
    if not re.search(r"^LABEL\s+org.opencontainers.image.revision=\$REDESIGN_BUILD_ID\s*$", docker_text, re.M):
        failures.append("Tenant image revision label is missing")
    checks.append("Docker frontend inputs, build output and image revision examined")

    nginx = required[3].read_text(encoding="utf-8")
    for location in ("location /api/", "location = /healthz"):
        if location not in nginx:
            failures.append(f"Tenant nginx is missing {location}")
    for header in ("X-Tenant-Account", "X-Tenant-Role"):
        if f"proxy_set_header {header} $http_{header.lower().replace('-', '_')};" not in nginx:
            failures.append(f"Tenant nginx does not forward authenticated {header}")
    checks.append("Tenant nginx API and health proxy locations examined")
    standalone_compose = lab / "deploy/docker-compose.redesign-tenant.yml"
    if standalone_compose.is_file():
        compose_text = standalone_compose.read_text(encoding="utf-8")
        if "WB_SAAS_REDESIGN_INTERNAL_API_SECRET" not in compose_text:
            failures.append("Standalone redesign Compose has no scoped internal credential variable")
        if "${WB_SAAS_INTERNAL_API_SECRET:-}" in compose_text:
            failures.append("Standalone redesign Compose must not pass the gateway master secret")
        checks.append("Standalone tenant credential scope examined")
    warnings.append("Source checks do not replace clean-checkout, Docker build, authenticated staging flow or visual acceptance")
    return {"checks": checks, "failures": failures, "warnings": warnings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = check_sources(args.root)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for message in result["checks"]:
            print("PROMOTION_SOURCE_CHECK", message)
        for message in result["warnings"]:
            print("PROMOTION_SOURCE_NOTE", message)
        for message in result["failures"]:
            print("PROMOTION_SOURCE_FAIL", message)
        print("PROMOTION_SOURCE_OK" if not result["failures"] else "PROMOTION_SOURCE_BLOCKED")
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
