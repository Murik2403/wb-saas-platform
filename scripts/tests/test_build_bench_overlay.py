"""The overlay builder only copies what the real Dockerfile copies, and refuses when an overlay would not be valid."""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("build_bench_overlay_under_test", SCRIPTS / "build_bench_overlay.py")
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)

DOCKERFILE = """FROM python:3.12-slim
COPY tenant-app/requirements.txt tenant-app/requirements.lock ./
COPY tenant-app/ ./
COPY mh-redesign-lab/tenant-api/api_server.py ./api_server.py
COPY mh-redesign-lab/tenant-api/shop_context_api.py mh-redesign-lab/tenant-api/shop_dispatch.py ./
COPY --from=frontend-build /frontend/dist /usr/share/nginx/html
COPY mh-redesign-lab/deploy/nginx.tenant-staging.conf /etc/nginx/conf.d/default.conf
"""


def test_only_application_copies_become_overlay_lines():
    lines = overlay.copy_lines(DOCKERFILE)
    assert lines == [(["tenant-app/"], "./"), (["mh-redesign-lab/tenant-api/api_server.py"], "./api_server.py"),
                     (["mh-redesign-lab/tenant-api/shop_context_api.py", "mh-redesign-lab/tenant-api/shop_dispatch.py"], "./")]


def test_destinations_are_relative_to_the_tenant_workdir():
    assert overlay.container_destination("./") == "/app/tenant-app/"
    assert overlay.container_destination("./api_server.py") == "/app/tenant-app/api_server.py"
    assert overlay.container_destination("/usr/share/x") == "/usr/share/x"


def test_the_rendered_dockerfile_copies_and_labels():
    text = overlay.render_dockerfile("base:1", overlay.copy_lines(DOCKERFILE), {"wb-saas.bench-head": "a" * 40})
    assert text.startswith("FROM base:1\nWORKDIR /app/tenant-app\n")
    assert "COPY tenant-app/ /app/tenant-app/\n" in text and "COPY mh-redesign-lab/tenant-api/api_server.py /app/tenant-app/api_server.py\n" in text
    assert f'LABEL wb-saas.bench-head="{"a" * 40}"' in text and "--from" not in text and "requirements" not in text


def test_a_dirty_tree_is_refused():
    with patch.object(overlay, "git", return_value=" M file"):
        with pytest.raises(SystemExit) as refused:
            overlay.main(["--base", "b", "--tag", "t"])
    assert "dirty" in str(refused.value)


def run_main(base_revision, changed):
    info = '[{"Id": "sha256:' + "1" * 64 + '", "Config": {"Labels": {"org.opencontainers.image.revision": "' + base_revision + '"}}}]'
    with patch.object(overlay, "git", return_value=""), patch.object(overlay.base, "assert_local_engine"), patch.object(overlay.base, "docker", return_value=info), \
         patch.object(overlay, "runtime_changes", return_value=changed):
        return overlay.main(["--base", "b", "--tag", "t"])


def test_a_base_without_a_commit_label_is_refused():
    with pytest.raises(SystemExit) as refused:
        run_main("", [])
    assert "revision label" in str(refused.value)


def test_changed_dependencies_or_runtime_files_force_a_full_build():
    with pytest.raises(SystemExit) as refused:
        run_main("a" * 40, ["tenant-app/requirements.lock"])
    assert "Do a full build" in str(refused.value) and "requirements.lock" in str(refused.value)
