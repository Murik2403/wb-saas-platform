"""Resolve CI paths like a runner; no Docker build, network or app imports."""
from pathlib import Path
import re
import shlex

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
SCAN = WORKFLOW["jobs"]["scan-images"]


def _render(value, matrix):
    value = re.sub(r"\$\{\{\s*matrix\.(\w+)\s*\}\}", lambda match: matrix[match[1]], value)
    return value.replace("${{ github.sha }}", "fixture-commit")


def _working_directory(job, step, matrix):
    directory = "."
    for owner in (WORKFLOW, job):
        directory = owner.get("defaults", {}).get("run", {}).get("working-directory", directory)
    return (ROOT / _render(step.get("working-directory", directory), matrix)).resolve()


@pytest.mark.parametrize("matrix", SCAN["strategy"]["matrix"]["include"], ids=lambda row: row["name"])
def test_scan_dockerfile_and_context_exist_from_runner_working_directory(matrix):
    step = next(step for step in SCAN["steps"] if "docker build " in step.get("run", ""))
    cwd = _working_directory(SCAN, step, matrix)
    args = shlex.split(_render(step["run"], matrix))
    assert args[:2] == ["docker", "build"], "Resolve any shell cd before checking Docker paths"
    dockerfile = (cwd / args[args.index("--file") + 1]).resolve()
    context = (cwd / args[-1]).resolve()
    assert cwd.is_relative_to(ROOT), "Build working directory leaves checkout"
    assert context.is_relative_to(ROOT) and context.is_dir(), f"Missing build context for {matrix['name']}: {context}"
    assert dockerfile.is_relative_to(ROOT) and dockerfile.is_file(), (
        f"{matrix['name']}: --file resolves from runner cwd ({cwd}), not build context ({context}): {dockerfile}"
    )


def test_redesign_pytest_target_exists_from_its_own_step_working_directory():
    job = WORKFLOW["jobs"]["test-tenant-app"]
    step = next(step for step in job["steps"] if step.get("name") == "Run Redesign API Tests")
    cwd = _working_directory(job, step, {})
    commands = [shlex.split(part) for part in step["run"].strip().split("&&")]
    for command in commands[:-1]:
        assert command[0] == "cd" and len(command) == 2, "Unexpected setup command in redesign test step"
        cwd = (cwd / command[1]).resolve()
    args = commands[-1]
    assert args[:3] == ["python", "-m", "pytest"]
    target = (cwd / args[3]).resolve()
    assert cwd.is_relative_to(ROOT), f"Redesign test step leaves checkout: {cwd}"
    assert target.is_dir(), f"Redesign pytest target missing: {target}"
    assert (target / "test_growth_routes.py").is_file(), "Growth route tests are not collected"


def test_staging_image_revision_uses_the_same_commit_as_its_tag():
    matrix = next(row for row in SCAN["strategy"]["matrix"]["include"] if row["name"] == "tenant-staging")
    step = next(step for step in SCAN["steps"] if "docker build " in step.get("run", ""))
    args = shlex.split(_render(step["run"], matrix))
    build_args = [args[index + 1] for index, value in enumerate(args) if value == "--build-arg"]
    assert "REDESIGN_BUILD_ID=fixture-commit" in build_args, "Image revision must be the CI commit, not Dockerfile's dev default"
    assert args[args.index("--tag") + 1].endswith(":fixture-commit")
