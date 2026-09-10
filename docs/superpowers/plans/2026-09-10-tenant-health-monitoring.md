# Tenant Health Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make only each tenant's Streamlit liveness endpoint publicly reachable so an external uptime monitor can detect a failed dashboard without opening dashboard content.

**Architecture:** Tenant containers already expose Streamlit's `/_stcore/health` internally and the normal Traefik router applies ForwardAuth to every request. The provisioner will emit a second, higher-priority Traefik router for an exact `GET /_stcore/health` match, using the same service but no authentication middleware. The existing router continues to protect every other tenant path.

**Tech Stack:** FastAPI/Pydantic provisioner, Docker SDK labels, Traefik v3, pytest, Docker Compose.

**Spec:** `C:\Users\mvvor\Downloads\audittracker.html` (P2-7: external pings for `/healthz` and `/_stcore/health`).

## Global Constraints

- Preserve the existing authenticated tenant router for all non-health paths.
- Expose only `GET /_stcore/health` on the tenant host; do not attach ForwardAuth to the health router.
- Keep the same tenant service and TLS configuration as the authenticated router.
- Do not add secrets or production hostnames to source control.

---

### Task 1: Add an exact public health router

**Files:**
- Modify: `provisioner/app.py:85-97`
- Test: `provisioner/tests/test_policy.py`

**Interfaces:**
- Consumes: `_tenant_host(slug) -> str` and `FORWARD_AUTH_MIDDLEWARE`.
- Produces: `_labels(slug, require_auth) -> dict[str, str]` with `tenant-<slug>-health` labels.

- [x] **Step 1: Write the failing test**

```python
def test_tenant_health_router_is_public_but_exact():
    labels = tenant_container_spec("shop-a", _request())["labels"]
    prefix = "traefik.http.routers.tenant-shop-a-health"
    assert labels[f"{prefix}.rule"] == "Host(`shop-a.app.example.test`) && Path(`/_stcore/health`) && Method(`GET`)"
    assert labels[f"{prefix}.service"] == "tenant-shop-a"
    assert f"{prefix}.middlewares" not in labels
```

- [x] **Step 2: Run the test to verify it fails**

Run: `docker run --rm -v "${PWD}/provisioner:/app:ro" -w /app --entrypoint python wb-saas-platform-clean-provisioner -m pytest tests/test_policy.py::test_tenant_health_router_is_public_but_exact -q`

Expected: FAIL because the `tenant-shop-a-health` labels do not exist.

- [x] **Step 3: Write minimal implementation**

```python
health_router = f"{router}-health"
labels.update({
    f"traefik.http.routers.{health_router}.rule": f"Host(`{_tenant_host(slug)}`) && Path(`/_stcore/health`) && Method(`GET`)",
    f"traefik.http.routers.{health_router}.priority": "100",
    f"traefik.http.routers.{health_router}.entrypoints": "websecure",
    f"traefik.http.routers.{health_router}.tls.certresolver": TLS_CERT_RESOLVER,
    f"traefik.http.routers.{health_router}.service": router,
})
```

- [x] **Step 4: Run the provisioner suite**

Run: `docker run --rm -v "${PWD}/provisioner:/app:ro" -w /app --entrypoint python wb-saas-platform-clean-provisioner -m pytest -q`

Expected: PASS.

### Task 2: Document the second UptimeRobot monitor

**Files:**
- Modify: `DEPLOY.md` monitoring section

**Interfaces:**
- Consumes: public tenant hostname `<slug>.app.<parent-domain>` and Streamlit health path.
- Produces: an operator instruction for a separate 5-minute UptimeRobot HTTP(s) monitor.

- [x] **Step 1: Add explicit monitor instruction**

```markdown
Create a second HTTP(s) monitor for
`https://<slug>.<tenant-prefix>.<parent-domain>/_stcore/health`.
Use a real, non-demo tenant host and the same five-minute interval.
```

- [x] **Step 2: Validate configuration and image build**

Run: `docker compose config --quiet` and `docker compose build provisioner`

Expected: both commands exit successfully.

- [ ] **Step 3: Commit**

```bash
git add provisioner/app.py provisioner/tests/test_policy.py DEPLOY.md docs/superpowers/plans/2026-09-10-tenant-health-monitoring.md
git commit -m "feat: expose tenant health endpoint for monitoring"
```
