"""Shop-floor roles constrain writes; account owner controls invitations.

These roles share read access with the team, as explicitly stated in the UI.
"""
ROLES = {"owner", "manager", "viewer", "technologist", "cutter", "seamstress", "warehouse", "integration_reader"}


def write_allowed(role: str, method: str, path: str) -> bool:
    if role not in ROLES:
        return False
    if method == "POST" and path == "/api/v1/supply-planning/preview":
        return role != "integration_reader"
    if method == "POST" and path == "/api/v1/supply-planning/confirm":
        return role in {"owner", "manager"}
    if role == "integration_reader":
        return method == "GET" and path in {"/api/v1/integration/products", "/api/v1/integration/materials", "/api/v1/integration/releases"}
    if method in {"GET", "HEAD", "OPTIONS"}:
        return True
    if role in {"owner", "manager"}:
        return True
    if role == "viewer":
        return False
    exact = {
        "technologist": {("POST", "/api/v1/manufacturing-costs/boms"), ("PUT", "/api/v1/production-workspace/capacity"), ("PATCH", "/api/v1/settings/production-norms")},
        "cutter": {("POST", "/api/v1/production-workspace/wip/issue"), ("POST", "/api/v1/manufacturing-costs/defects")},
        "seamstress": {("POST", "/api/v1/manufacturing-costs/piecework"), ("POST", "/api/v1/production-workspace/wip/package")},
        "warehouse": {("POST", "/api/v1/manufacturing-costs/releases"), ("POST", "/api/v1/manufacturing-costs/defects")},
    }
    if (method, path) in exact.get(role, set()):
        return True
    parts = path.split("/")
    return role == "cutter" and method == "POST" and len(parts) == 7 and parts[1:5] == ["api", "v1", "production-workspace", "wip"] and parts[5].isdigit() and parts[6] == "complete"
