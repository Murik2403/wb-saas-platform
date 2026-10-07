"""Shop-floor roles constrain writes; account owner controls invitations.

These roles share read access with the team, as explicitly stated in the UI.
"""
ROLES = {"owner", "manager", "viewer", "technologist", "cutter", "seamstress", "warehouse", "integration_reader"}


def write_allowed(role: str, method: str, path: str) -> bool:
    if role not in ROLES:
        return False
    owner_connections={'/api/v1/settings/wb-token','/api/v1/settings/ozon','/api/v1/settings/tax','/api/v1/operations/reports/telegram/link-code','/api/v1/ozon-accounting/advertising/credentials'}
    if method=='DELETE' or path.startswith('/api/v1/operations/backups') or (method not in {'GET','HEAD','OPTIONS'} and (path in owner_connections or path=='/api/v1/self-buyouts/delete')):
        return role=='owner'
    if method=='POST' and path in {'/api/v1/dashboard/products/price-advice','/api/v1/ozon-insights/tax-input'}:
        return role!='integration_reader'
    if path.startswith("/api/v1/shared-warehouse"):
        if role=="integration_reader":return False
        if method in {"GET","HEAD","OPTIONS"}:return True
        if "/imported-basis/" in path:return role=="owner"
        if path.endswith("/preview") and not path.endswith("/returns/preview"):return True
        if '/financial-bindings/' in path:return role in {'owner','manager'}
        if path.endswith("/primary-binding") or path.endswith("/items") or "/imported-basis/" in path or "/mappings/" in path:return role=="owner"
        return role in {"owner","manager","warehouse"}
    if method == "POST" and path == "/api/v1/cash-accounts/statements/preview":
        return role != "integration_reader"
    if method == "POST" and path == "/api/v1/production-workspace/workcenters/preview":
        return role != "integration_reader"
    if method == "POST" and path in {"/api/v1/production-workspace/workcenters/centers", "/api/v1/production-workspace/workcenters/norms"}:
        return role in {"owner", "manager", "technologist"}
    if method == "POST" and path == "/api/v1/production-workspace/workcenters/confirm":
        return role in {"owner", "manager"}
    if method == "POST" and path in {"/api/v1/supply-planning/preview", "/api/v1/migration-import/preview"}:
        return role != "integration_reader"
    if method == "POST" and path in {"/api/v1/supply-planning/confirm", "/api/v1/migration-import/confirm"}:
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
