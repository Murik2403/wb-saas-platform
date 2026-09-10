# Full Dashboard Data Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make every redesigned dashboard section usable with tenant-backed data and honest empty states, while preserving the existing Streamlit application.

**Architecture:** Extend the tenant API with read-only, page-oriented JSON endpoints backed by the existing calculation and database modules. Keep the React redesign isolated under `mh-redesign-lab`, use typed adapters in `src/data-source.ts`, and make each page render API data or an explicit unavailable state. Deploy only the redesign image to the authorized `mvvorokov` tenant after local and production checks.

**Tech Stack:** Python/FastAPI, existing SQLite/data services, React, TypeScript, Vite, Docker Compose, Nginx.

**Spec:** Existing redesign contracts in `mh-redesign-lab/src/contracts.ts` and existing tenant API/service contracts in `tenant-app/api_server.py`.

## Global Constraints

- Do not modify or remove the current Streamlit entrypoint or other tenant containers.
- Production must never use `mock-dashboard.ts`; mock mode is local development only.
- Missing source data must render an explicit empty state, never invented business values.
- Preserve tenant isolation and existing API authentication/routing behavior.
- Verify local build, backend tests, container health, and real `mvvorokov` responses before claiming completion.

---

### Task 1: Freeze API contracts and page data requirements

**Files:**
- Modify: `mh-redesign-lab/src/contracts.ts`
- Modify: `mh-redesign-lab/src/data-source.ts`
- Inspect: `tenant-app/api_server.py`, `tenant-app/calculations.py`, `tenant-app/pages/*.py`
- Test: `mh-redesign-lab` TypeScript build

**Interfaces:**
- Preserve `DashboardViewModel` as the page-facing contract.
- Define response types for overview, ads, finance, production, supply, assistant, and reports using fields already produced by the tenant services.

- [ ] Document, in code types, which API fields are optional and which values are derived locally.
- [ ] Map each `PageId` to one endpoint or a documented endpoint gap.
- [ ] Run `npm run build` and retain a clean baseline before backend additions.

### Task 2: Add complete read-only tenant API payloads

**Files:**
- Modify: `tenant-app/api_server.py`
- Modify: `tenant-app/calculations.py` only when an existing calculation is not serializable or lacks a required field
- Test: `tenant-app/tests/test_api_server.py`

**Interfaces:**
- `GET /api/dashboard/today`
- `GET /api/dashboard/stock`
- `GET /api/dashboard/ads`
- `GET /api/dashboard/finance`
- `GET /api/dashboard/production`
- `GET /api/dashboard/supply`
- `GET /api/dashboard/assistant`
- `GET /api/dashboard/reports`

- [ ] Reuse `build_dashboard`, `compute_overview`, report readers, production readers, and procurement readers rather than duplicating SQL.
- [ ] Return `sync`, `generated_at`, typed page payloads, and empty arrays when source tables are empty.
- [ ] Return structured HTTP errors without HTML responses so the frontend can distinguish unavailable data from an API failure.
- [ ] Add endpoint tests for non-empty, empty, and malformed-period cases.
- [ ] Run the focused API test file and the existing tenant test suite.

### Task 3: Replace frontend mock fallbacks with page adapters

**Files:**
- Modify: `mh-redesign-lab/src/data-source.ts`
- Modify: `mh-redesign-lab/src/App.tsx`
- Modify: `mh-redesign-lab/src/styles.css` only for new empty/loading/error states
- Test: `mh-redesign-lab` TypeScript/Vite build

**Interfaces:**
- Add `loadFullDashboard(signal: AbortSignal): Promise<DashboardViewModel>`.
- Add one adapter per page payload, each returning real arrays or empty arrays.
- Keep `createMockViewModel()` reachable only when `VITE_MH_API_MODE=mock`.

- [ ] Fetch page payloads with bounded timeouts and preserve partial successful page data.
- [ ] Remove all production paths that spread `createMockViewModel()`.
- [ ] Keep the existing action-plan writes isolated from page reads.
- [ ] Ensure every metric, hero, chart, table, and list has an honest no-data rendering path.
- [ ] Run `npm run build`.

### Task 4: Complete Today and Stock workflows

**Files:**
- Modify: `mh-redesign-lab/src/App.tsx`
- Modify: `mh-redesign-lab/src/data-source.ts`
- Modify: `tenant-app/api_server.py` if the page payload needs missing fields
- Test: API tests and frontend build

- [ ] Render real KPIs, actions, inventory, procurement, and revenue trend on Today.
- [ ] Render real stock metrics, replenishment plan, warehouse coverage, and rules on Stock.
- [ ] Preserve the current empty states when the legacy API has no rows.
- [ ] Verify no demo product names, warehouse values, percentages, or timestamps appear in legacy production mode.

### Task 5: Complete Ads and Finance workflows

**Files:**
- Modify: `mh-redesign-lab/src/App.tsx`
- Modify: `mh-redesign-lab/src/data-source.ts`
- Modify: `tenant-app/api_server.py`, `tenant-app/ads_service.py`, or `tenant-app/overview_service.py` only for missing read-only fields
- Test: focused backend tests plus frontend build

- [ ] Render campaigns, spend, revenue, ACOS/DRR, funnel, and creative signals from the ads payload.
- [ ] Render finance KPIs, ledger, expense split, report freshness, and source notes from accounting data.
- [ ] Mark unavailable advertising scopes or incomplete realization reports explicitly in the UI.
- [ ] Ensure zero spend or missing report rows are represented as source facts, not as mocked success.

### Task 6: Complete Production and Supply workflows

**Files:**
- Modify: `mh-redesign-lab/src/App.tsx`
- Modify: `mh-redesign-lab/src/data-source.ts`
- Modify: `tenant-app/api_server.py`, `tenant-app/db/production.py`, `tenant-app/db/procurement.py` only where existing read functions are insufficient
- Test: existing production/procurement tests plus new API tests

- [ ] Render production capacity, stages, tasks, material needs, and blockers from existing tables.
- [ ] Render supply metrics, shipments, supplier pulse, payment/ETA state, and empty states.
- [ ] Keep all write actions behind the existing APIs and do not invent operational statuses.
- [ ] Run production and procurement regression tests.

### Task 7: Complete Assistant and Reports workflows

**Files:**
- Modify: `mh-redesign-lab/src/App.tsx`
- Modify: `mh-redesign-lab/src/data-source.ts`
- Modify: `tenant-app/api_server.py`, `tenant-app/agents/*.py`, `tenant-app/reports/*.py` only for read contracts
- Test: agent/report test suites and frontend build

- [ ] Render assistant status, available automations, recent runs, and permissions based on actual agent state.
- [ ] Render report cards, periods, delivery/export status, and actual downloadable report metadata.
- [ ] Keep destructive or external delivery actions out of the migration until their existing confirmation/API contracts are wired.
- [ ] Run all agent and report tests.

### Task 8: Responsive and interaction QA

**Files:**
- Modify: `mh-redesign-lab/src/styles.css`
- Modify: `mh-redesign-lab/src/App.tsx`
- Test: local browser at desktop and mobile viewports

- [ ] Check every navigation item and query-string route.
- [ ] Check loading, live, stale, API error, empty, and partial-data states.
- [ ] Check mobile navigation, tables, charts, action buttons, and text wrapping at 390px and desktop width.
- [ ] Verify browser console has no runtime errors and no failed JSON parsing from HTML fallbacks.

### Task 9: Production rollout and final verification

**Files:**
- Modify: `mh-redesign-lab/Dockerfile.tenant-staging` only if required by the final build
- Use: `/root/mh-redesign-backups/docker-compose.mvvorokov-staging.yml`

- [ ] Build and inspect the final image from a clean context.
- [ ] Transfer the image to the server and recreate only `wb-tenant-mvvorokov`.
- [ ] Verify container health, `/healthz`, all page endpoints, and tenant isolation.
- [ ] Keep the legacy container backup available for rollback.
- [ ] Verify the authenticated production UI with the user’s session or clearly report the session limitation.
- [ ] Report completion only after all checks pass and identify any remaining source-data limitations.
