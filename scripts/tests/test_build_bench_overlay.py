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


BASE = {"Architecture": "amd64", "Os": "linux", "RootFS": {"Layers": ["sha256:a", "sha256:b"]},
        "Config": {"Entrypoint": ["/usr/local/bin/x"], "Cmd": None, "User": "", "WorkingDir": "/app/tenant-app", "Env": ["A=1"], "ExposedPorts": {"8501/tcp": {}}, "Volumes": {"/app/tenant-app/data": {}},
                   "Healthcheck": {"Test": ["CMD", "true"]}}}


def built(**changes):
    info = {"Architecture": "amd64", "Os": "linux", "RootFS": {"Layers": ["sha256:a", "sha256:b", "sha256:c"]}, "Config": dict(BASE["Config"], Labels={"x": "y"})}
    for key, value in changes.items():
        if key == "Config":
            info["Config"] = dict(info["Config"], **value)
        else:
            info[key] = value
    return info


def test_an_overlay_on_the_unchanged_base_is_accepted():
    assert overlay.inheritance_failures(BASE, built()) == []


@pytest.mark.parametrize("name,info,fragment", [
    ("a base layer replaced", built(RootFS={"Layers": ["sha256:a", "sha256:z", "sha256:c"]}), "start with exactly"),
    ("base layers reordered", built(RootFS={"Layers": ["sha256:b", "sha256:a", "sha256:c"]}), "start with exactly"),
    ("nothing overlaid", built(RootFS={"Layers": ["sha256:a", "sha256:b"]}), "added no layer"),
    ("entrypoint changed", built(Config={"Entrypoint": ["/bin/sh"]}), "Entrypoint"),
    ("user changed", built(Config={"User": "0"}), "User"),
    ("env changed", built(Config={"Env": ["A=2"]}), "Env"),
    ("volume added", built(Config={"Volumes": {}}), "Volumes"),
    ("healthcheck removed", built(Config={"Healthcheck": None}), "Healthcheck"),
    ("other architecture", built(Architecture="arm64"), "platform"),
])
def test_each_departure_from_the_base_is_refused(name, info, fragment):
    problems = overlay.inheritance_failures(BASE, info)
    assert problems and any(fragment in line for line in problems), (name, problems)


def test_a_base_without_layers_is_refused():
    assert overlay.inheritance_failures({"Config": {}}, built())


def test_the_dockerfile_is_built_from_the_immutable_id():
    text = overlay.render_dockerfile("sha256:" + "1" * 64, overlay.copy_lines(DOCKERFILE), {"k": "v"})
    assert text.splitlines()[0] == "FROM sha256:" + "1" * 64
