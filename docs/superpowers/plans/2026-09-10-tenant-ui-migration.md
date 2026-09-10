# Tenant UI Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Declare one supported tenant UI, freeze the two legacy UI paths, and cut existing tenants over safely after page-level acceptance gates.

**Architecture:** `tenant-app` continues to own the SQLite schema and business services. The target tenant runtime is the `mh-redesign-lab` React bundle served with its FastAPI adapter; it imports the established tenant services rather than copying accounting or marketplace rules. Gateway, provisioning, tenant domains, and tenant volumes remain unchanged, so a rollback is an image replacement that keeps the same tenant volume.

**Tech Stack:** Python 3.12, FastAPI, SQLite, React 19, TypeScript, Vite, nginx, Docker Compose, Traefik.

**Spec:** `C:\Users\mvvor\Downloads\audittracker.html` (P3-5: define a target architecture, cutover date, legacy freeze, and page migration status).

## Global Constraints

- Do not migrate or mutate tenant SQLite data for a UI rollout.
- Preserve `tenant-app/Dockerfile` as the rollback image until the stabilisation window closes.
- New legacy features are forbidden; only security, data-integrity, recovery, compliance, and production-blocking hotfixes are allowed there.
- The cutover is gated by acceptance evidence; a calendar date alone must not promote an unverified tenant.
- Never put credentials or production tenant data in documentation, test fixtures, screenshots, or commits.

---

### Task 1: Publish the architecture decision and page matrix

**Files:**
- Create: `docs/TENANT_UI_MIGRATION_2026-09.md`
- Test: document section and status validation command

**Interfaces:**
- Consumes: tenant runtime boundaries in `tenant-app/Dockerfile`, `mh-redesign-lab/Dockerfile.tenant-staging`, `gateway/provisioning.py`, and `mh-redesign-lab/src/data-source.ts`.
- Produces: one decision record naming the canonical UI, fallback, page status, freeze policy, and dated rollout gates.

- [x] **Step 1: Record the observed runtime boundaries**

Document that `tenant-app` owns state and business logic, `mh-redesign-lab/tenant-api` is an adapter, the staging image serves React through nginx, and provisioning remains responsible for tenant host/volume lifecycle.

- [x] **Step 2: Add the target state and legacy freeze**

Set the React/FastAPI tenant image as the post-cutover canonical runtime. Mark Streamlit and `frontend/` as no-feature-change paths, retaining Streamlit only for rollback/hotfixes within the stated exceptions.

- [x] **Step 3: Add the page-status matrix and dated gates**

List Today, Stock, Ads, Finance, Production, Supply, AI agents, Reports, Products, Control, and Settings. Separate connected API views from views requiring destructive-workflow acceptance. Set 2026-10-15 as the general cutover target after staging and a single-tenant canary; retain a two-week rollback window.

- [x] **Step 4: Validate the decision record**

Run:

```powershell
$doc = Get-Content -Raw docs/TENANT_UI_MIGRATION_2026-09.md
$required = @('2026-10-15', 'Статус миграции по страницам', 'Заморозка legacy', 'Откат')
$missing = $required | Where-Object { $doc -notmatch [regex]::Escape($_) }
if ($missing) { throw "Missing sections: $($missing -join ', ')" }
```

Expected: exits 0.

### Task 2: Verify and commit the migration decision

**Files:**
- Create: `docs/superpowers/plans/2026-09-10-tenant-ui-migration.md`
- Create: `docs/TENANT_UI_MIGRATION_2026-09.md`

**Interfaces:**
- Consumes: the completed decision record.
- Produces: a reviewable local commit with no runtime or tenant-data change.

- [x] **Step 1: Check document scope and whitespace**

Run:

```powershell
git diff --check -- docs/TENANT_UI_MIGRATION_2026-09.md docs/superpowers/plans/2026-09-10-tenant-ui-migration.md
git diff --name-only -- docs/TENANT_UI_MIGRATION_2026-09.md docs/superpowers/plans/2026-09-10-tenant-ui-migration.md
```

Expected: no whitespace errors; only the two migration documents are listed.

- [x] **Step 2: Commit only the decision and plan**

```bash
git add docs/TENANT_UI_MIGRATION_2026-09.md docs/superpowers/plans/2026-09-10-tenant-ui-migration.md
git commit -m "docs: define tenant UI migration"
```
