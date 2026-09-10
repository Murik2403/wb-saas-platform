# Redesign Functional Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the React redesign a functional replacement for the existing Streamlit tenant dashboard without changing its accounting or marketplace rules.

**Architecture:** The existing `tenant-app` services remain the source of truth. `mh-redesign-lab/tenant-api/api_server.py` exposes narrow, typed JSON endpoints around those services; the React client owns responsive workflows, input validation, loading states, and confirmation dialogs. Potentially destructive FIFO operations remain explicit, reviewed, and backend-validated.

**Tech Stack:** Python 3.12, FastAPI, SQLite, pandas, React 19, TypeScript, Vite.

**Spec:** Existing production functionality in `tenant-app/app.py` and `tenant-app/pages/`.

## Global Constraints

- Preserve the tenant SQLite schema and all existing `tenant-app` business functions.
- Never auto-apply prices, advertising budgets, FIFO write-offs, or supplier changes.
- Load heavy dashboard calculations through the existing serialized response guard.
- Keep the current Streamlit app deployable until each corresponding React workflow is verified.
- Verify desktop and mobile views after each user-facing task.

---

### Task 1: Shared Period and Custom Date Range

**Files:**
- Modify: `mh-redesign-lab/src/App.tsx`
- Modify: `mh-redesign-lab/src/data-source.ts`
- Modify: `mh-redesign-lab/tenant-api/api_server.py`
- Test: `mh-redesign-lab/tenant-api/tests/test_periods.py`

**Interfaces:**
- Consumes: `resolve_window(period, today_msk, start, end)` from `tenant-app/overview_service.py`.
- Produces: `DashboardPeriod { preset: "today" | "7d" | "30d" | "90d" | "custom"; start?: string; end?: string }` and period-aware finance, ads, and products requests.

- [ ] Write tests for valid custom ranges, reversed dates, and invalid partial custom ranges.
- [ ] Add `GET /api/v1/dashboard/finance` and `GET /api/v1/dashboard/ads` query parameters `period`, `start`, and `end`; delegate validation to `resolve_window`.
- [ ] Replace static "30 дней" controls with a labelled segmented period picker and two native date inputs for custom ranges.
- [ ] Persist the selected period in the URL query string and reload affected dashboard data.
- [ ] Run API tests, `npm run build`, and responsive UI QA.

### Task 2: Agent Queue and Explicit Run State

**Files:**
- Modify: `mh-redesign-lab/src/App.tsx`
- Modify: `mh-redesign-lab/src/data-source.ts`
- Modify: `mh-redesign-lab/tenant-api/api_server.py`
- Test: `mh-redesign-lab/tenant-api/tests/test_agent_payload.py`

**Interfaces:**
- Consumes: `POST /api/v1/agent/refresh` and `GET /api/v1/agent/candidates`.
- Produces: populated central queue with candidate rows and visible `refreshing`/error state.

- [ ] Write a fixture-backed test asserting the dashboard assistant payload includes pending candidate identifiers and fields required for apply/reject.
- [ ] Return candidates from `/api/v1/dashboard/assistant` or fetch `/api/v1/agent/candidates` after refresh.
- [ ] Wire "Запустить" to a disabled-while-running command and refresh the central queue when it completes.
- [ ] Keep apply/reject behind the existing confirmation dialog and surface backend rejection details.
- [ ] Run API tests and UI QA for empty, pending, and failed agent runs.

### Task 3: FIFO Reconciliation Workspace

**Files:**
- Modify: `mh-redesign-lab/tenant-api/api_server.py`
- Modify: `mh-redesign-lab/src/contracts.ts`
- Modify: `mh-redesign-lab/src/data-source.ts`
- Modify: `mh-redesign-lab/src/App.tsx`
- Test: `mh-redesign-lab/tenant-api/tests/test_fifo_api.py`

**Interfaces:**
- Consumes: diagnostic functions from `tenant-app/db/wb_fifo_reconciliation.py` and existing safe FIFO status functions.
- Produces: read-only FIFO diagnostic summary, explicit "Run reconciliation" command, and an auditable result view.

- [ ] Write tests that FIFO diagnostics are read-only and that a run refuses concurrent execution.
- [ ] Add GET diagnostic and POST run endpoints; execute reconciliation in a guarded background runner and store errors in the existing run log.
- [ ] Add a Stock-page FIFO panel with current delta, affected SKU count, last run, and a confirmation dialog before a new run.
- [ ] Link action-row "Открыть сверку FIFO" to this panel instead of the generic plan handler.
- [ ] Run API tests and confirm no endpoint can create FIFO write-offs.

### Task 4: Stock, Product, and Control Parity

**Files:**
- Modify: `mh-redesign-lab/tenant-api/api_server.py`
- Modify: `mh-redesign-lab/src/App.tsx`
- Modify: `mh-redesign-lab/src/contracts.ts`
- Test: `mh-redesign-lab/tenant-api/tests/test_stock_control_api.py`

**Interfaces:**
- Consumes: existing products, stock, cost, FIFO, and control services.
- Produces: product economics table, stock details, data-quality checks, and filterable exception lists.

- [ ] Write endpoint tests for SKU filters and empty results.
- [ ] Add product table/detail API with period context and server-side limits.
- [ ] Add stock filters, FIFO diagnostics, and a Control page for data quality, backups, and sync history.
- [ ] Add explicit mobile drill-down routes instead of squeezing dense tables into cards.
- [ ] Run API tests and desktop/mobile visual QA.

### Task 5: Settings, Reports, Procurement, and Production Completion

**Files:**
- Modify: `mh-redesign-lab/tenant-api/api_server.py`
- Modify: `mh-redesign-lab/src/App.tsx`
- Modify: `mh-redesign-lab/src/data-source.ts`
- Test: `mh-redesign-lab/tenant-api/tests/test_operations_api.py`

**Interfaces:**
- Consumes: existing settings, report, procurement, and production persistence functions.
- Produces: settings screen, report schedules/history, full procurement forms, and production task editing.

- [ ] Write tests for settings validation and each operation's required confirmation fields.
- [ ] Add Settings and Control navigation entries only after their APIs are ready.
- [ ] Replace remaining generic plan actions with explicit forms or clearly labelled read-only links.
- [ ] Surface report jobs, backups, and operation history from their existing stores.
- [ ] Run all tenant tests, API tests, build, and full route/mobile QA before declaring Streamlit replacement-ready.

## Coverage Review

- Shared period/custom dates: Task 1.
- Agent "Запустить" and visible decision queue: Task 2.
- FIFO reconciliation: Task 3.
- Products, stocks, and control depth: Task 4.
- Settings, reports, procurement, and production parity: Task 5.
- Existing Streamlit remains intact through all tasks: Global Constraints.
