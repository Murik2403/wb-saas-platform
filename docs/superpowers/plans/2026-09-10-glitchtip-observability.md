# GlitchTip Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide a self-hosted, Sentry-compatible error-monitoring endpoint for the production gateway and tenant services.

**Architecture:** Run GlitchTip with its own PostgreSQL database in `/opt/glitchtip`, with no Valkey, no published container ports, and no access to tenant Docker networks. Traefik exposes the GlitchTip HTTPS UI at `glitchtip.app.marketshelper.ru` and a non-published ingestion entrypoint for tenant networks; gateway uses the private GlitchTip network directly.

**Tech Stack:** Docker Compose, GlitchTip 6, PostgreSQL 18, Traefik 3, Python `sentry-sdk` protocol.

**Spec:** `C:\Users\mvvor\Downloads\audittracker.html` (P2-7: observability)

## Global Constraints

- Do not modify or stage unrelated dirty files in `/opt/wb-saas`.
- Use an isolated GlitchTip database and a Docker internal network; do not reuse the control-plane database.
- Keep GlitchTip registration closed after the bootstrap administrator is provisioned.
- Do not enable log ingestion, internal-IP uptime probes, or MCP in the first deployment.
- Verify the public HTTPS UI, database health, and a synthetic error event before calling the setup complete.

---

### Task 1: Prepare capacity and isolated runtime

**Files:**
- Create: `/opt/glitchtip/compose.yml`
- Create: `/opt/glitchtip/.env`

**Interfaces:**
- Consumes: existing `wbsaas_net` Docker network and Traefik wildcard certificate for `*.app.marketshelper.ru`.
- Produces: an internal PostgreSQL service and a `glitchtip` service reachable only through Traefik.

- [x] **Step 1: Verify disk and running services before change**

Run: `df -h / && docker ps --format '{{.Names}} {{.Status}}'`

Expected: at least 4 GB free and each existing production container is `Up`.

- [x] **Step 2: Write the Compose runtime**

Use `glitchtip/glitchtip:6`, a dedicated PostgreSQL 18 volume, `SERVER_ROLE=all_in_one`, `VALKEY_URL=""`, `ENABLE_USER_REGISTRATION=False`, and an internal application network. Attach only the web service to `wbsaas_net` for Traefik discovery.

- [x] **Step 3: Generate non-committed service secrets**

Generate independent random values for Django `SECRET_KEY` and the PostgreSQL password. Store them only in `/opt/glitchtip/.env` with mode `0600`.

- [x] **Step 4: Start the stack and verify its health**

Run: `docker compose --env-file .env up -d && docker compose ps`

Expected: PostgreSQL is healthy and GlitchTip is running without a public host port binding.

### Task 2: Publish and secure the error-monitoring UI

**Files:**
- Modify: `/opt/glitchtip/compose.yml`

**Interfaces:**
- Consumes: Traefik Docker provider and the existing DNS wildcard.
- Produces: HTTPS endpoint `https://glitchtip.app.marketshelper.ru`.

- [x] **Step 1: Add a Traefik dynamic router**

Route `Host(\`glitchtip.app.marketshelper.ru\`)` to GlitchTip port 8000 using the existing resolver. Add a non-published Traefik entrypoint that routes only `/api/` for tenant SDK ingestion; do not add a broad public wildcard router.

- [x] **Step 2: Test public TLS and private application response**

Run: `curl -fsS https://glitchtip.app.marketshelper.ru/`

Expected: an HTTPS response from GlitchTip; no service port is published from Docker to the host.

### Task 3: Bootstrap monitoring projects and connect applications

**Files:**
- Modify: `/opt/wb-saas/gateway/` Sentry integration files only after production source is reconciled.
- Modify: `/opt/wb-saas/mh-redesign-lab/tenant-api/` only after the tenant API entrypoint is identified.

**Interfaces:**
- Consumes: GlitchTip organization, projects, and DSNs.
- Produces: `WB_SAAS_SENTRY_DSN` and tenant DSN configuration without client tokens or request bodies.

- [x] **Step 1: Bootstrap one administrator and two projects**

Create a non-shared administrator credential, then one gateway and one tenant project. Keep public self-registration disabled.

- [x] **Step 2: Store DSNs in the production secret environment**

Write only DSN variables to the appropriate protected server environment file; do not commit them or print them.

- [x] **Step 3: Add SDK initialization with PII disabled**

Initialize `sentry-sdk` only when the corresponding DSN is present, set `send_default_pii=False`, and tag each service name.

- [x] **Step 4: Send a synthetic event and verify it arrives**

Use a unique, non-user-data test exception and confirm its presence in the expected GlitchTip project. Test events must be clearly labelled `audit`.

### Task 4: Verify retention and operational state

**Files:**
- Modify: `/opt/glitchtip/compose.yml`
- Modify: production operations documentation after source reconciliation.

**Interfaces:**
- Consumes: running GlitchTip services and UptimeRobot tenant health monitor.
- Produces: documented retention and routine verification steps.

- [x] **Step 1: Set bounded error-event retention**

Set `GLITCHTIP_EVENT_RETENTION_DAYS=30`; keep log ingestion disabled until its storage budget is approved.

- [x] **Step 2: Run final verification**

Run: `docker compose ps`, inspect GlitchTip logs for startup errors, request the HTTPS UI, and confirm the pre-existing UptimeRobot health monitor remains `Up`.

- [ ] **Step 3: Commit only service-owned configuration**

Commit only the new GlitchTip deployment files after verification; leave all unrelated server and repository edits untouched.
