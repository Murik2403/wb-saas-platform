import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_promotion_sources.py"
spec = importlib.util.spec_from_file_location("promotion_source_gate", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def write(root, path, body):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


def fixture_tree(root):
    api = "\n".join(f'@app.{method}("{route}")\ndef route_{i}(): pass' for i, (method, route) in enumerate(gate.GROWTH_ROUTES + gate.HEALTH_ROUTES))
    write(root, "mh-redesign-lab/tenant-api/api_server.py", "from growth_activation import ready\nfrom growth_delivery import deliver\n" + api)
    write(root, "mh-redesign-lab/tenant-api/growth_activation.py", "def ready(): pass\n")
    write(root, "mh-redesign-lab/tenant-api/growth_delivery.py", "def deliver(): pass\n")
    copies = ["COPY mh-redesign-lab/tenant-api/" + name + " ./" + name for name in ("api_server.py", "growth_activation.py", "growth_delivery.py")]
    for name in gate.FRONTEND_INPUTS:
        target = root / "mh-redesign-lab" / name
        if name in {"public", "src"}:
            target.mkdir(exist_ok=True)
        else:
            write(root, "mh-redesign-lab/" + name, "{}")
        copies.append("COPY mh-redesign-lab/" + name + " ./" + name)
    write(root, "mh-redesign-lab/Dockerfile.tenant-staging", "\n".join(copies) + "\nLABEL org.opencontainers.image.revision=$REDESIGN_BUILD_ID\nRUN npm run build\nCOPY --from=frontend-build /frontend/dist /usr/share/nginx/html\n")
    write(root, "mh-redesign-lab/src/data-source.ts", "\n".join(route for _, route in gate.GROWTH_ROUTES))
    write(root, "mh-redesign-lab/deploy/nginx.tenant-staging.conf", "location /api/ { proxy_pass http://127.0.0.1:8001; proxy_set_header X-Tenant-Account $http_x_tenant_account; proxy_set_header X-Tenant-Role $http_x_tenant_role; }\nlocation = /healthz { proxy_pass http://127.0.0.1:8001/healthz; }")
    return root


def test_complete_local_import_packaging_and_routes_pass(tmp_path):
    assert gate.check_sources(fixture_tree(tmp_path))["failures"] == []


def test_missing_growth_copy_is_a_blocker_even_when_file_exists(tmp_path):
    fixture_tree(tmp_path)
    dockerfile = tmp_path / "mh-redesign-lab/Dockerfile.tenant-staging"
    dockerfile.write_text(dockerfile.read_text().replace("COPY mh-redesign-lab/tenant-api/growth_delivery.py ./growth_delivery.py", ""))
    failures = gate.check_sources(tmp_path)["failures"]
    assert any("growth_delivery.py" in message and "COPY" in message for message in failures)


def test_transitive_local_import_must_ship_too(tmp_path):
    fixture_tree(tmp_path)
    write(tmp_path, "mh-redesign-lab/tenant-api/growth_delivery.py", "from transport_retry import retry\n")
    write(tmp_path, "mh-redesign-lab/tenant-api/transport_retry.py", "def retry(): pass\n")
    assert any("transport_retry.py" in message for message in gate.check_sources(tmp_path)["failures"])


def test_missing_or_wrong_method_activation_route_is_detected(tmp_path):
    fixture_tree(tmp_path)
    api = tmp_path / "mh-redesign-lab/tenant-api/api_server.py"
    api.write_text(api.read_text().replace('@app.post("/api/v1/growth/report-viewed")', '@app.get("/api/v1/growth/report-viewed")'))
    assert any("POST /api/v1/growth/report-viewed" in message for message in gate.check_sources(tmp_path)["failures"])


def test_frontend_route_typo_is_detected(tmp_path):
    fixture_tree(tmp_path)
    source = tmp_path / "mh-redesign-lab/src/data-source.ts"
    source.write_text(source.read_text().replace("growth/activation", "growth/activation-typo"))
    assert any("frontend" in message and "activation" in message for message in gate.check_sources(tmp_path)["failures"])


def test_missing_health_route_is_detected(tmp_path):
    fixture_tree(tmp_path)
    api = tmp_path / "mh-redesign-lab/tenant-api/api_server.py"
    api.write_text(api.read_text().replace('@app.get("/healthz")', '@app.get("/health-typo")'))
    assert any("GET /healthz" in message for message in gate.check_sources(tmp_path)["failures"])


def test_tenant_identity_headers_must_reach_api_proxy(tmp_path):
    fixture_tree(tmp_path)
    nginx = tmp_path / "mh-redesign-lab/deploy/nginx.tenant-staging.conf"
    nginx.write_text(nginx.read_text().replace("proxy_set_header X-Tenant-Role $http_x_tenant_role;", ""))
    assert any("authenticated X-Tenant-Role" in message for message in gate.check_sources(tmp_path)["failures"])


def test_missing_frontend_build_input_is_detected(tmp_path):
    fixture_tree(tmp_path)
    (tmp_path / "mh-redesign-lab/package-lock.json").unlink()
    assert any("package-lock.json" in message for message in gate.check_sources(tmp_path)["failures"])
