# Commercial WB Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Bring the redesigned tenant dashboard to commercial parity with the legacy Streamlit workflow and expand it with the highest-value Wildberries API capabilities.

**Architecture:** Keep all WB credentials and data tenant-local. Extend the shared `tenant-app` business layer first, expose stable `/api/v1` tenant endpoints, then connect the React redesign through typed adapters. Every stage keeps the legacy calculations as the financial source of truth and adds a dedicated API contract plus smoke tests.

**Tech Stack:** Python 3.12, FastAPI, SQLite, existing `WBAPI` client, React/TypeScript/Vite, Docker, pytest, Vitest.

**Spec:** `docs/COMMERCIAL_PARITY_AUDIT_2026-09-16.md`

## Global Constraints

- Never expose WB tokens in API responses, logs, exports, or client bundles.
- Preserve tenant isolation and the existing `build_dashboard` financial calculations.
- Keep WB API rate limits and endpoint deprecations explicit in code and UI.
- Each task must have a focused test and a staging Docker smoke check before production rollout.

---

### Task 1: Financial export and current WB realization API

**Files:**
- Modify: `tenant-app/wb_api.py`, `tenant-app/sync.py`
- Modify: `mh-redesign-lab/tenant-api/api_server.py`
- Modify: `mh-redesign-lab/src/data-source.ts`, `mh-redesign-lab/src/App.tsx`, `mh-redesign-lab/src/contracts.ts`
- Test: `mh-redesign-lab/tenant-api/tests/test_finance_api.py`

**Deliverable:** Itemized finance, balance, current realization reports, XLSX export, and separate finance refresh in the redesign.

### Task 2: Finance freshness, retries, and report delivery

**Files:** `tenant-app/sync.py`, `sync_runner.py`, `mh-redesign-lab/tenant-api/api_server.py`, report UI and tests.

**Deliverable:** bounded retry/backoff, visible failure reason, scheduled financial report delivery through existing email/Telegram infrastructure.

### Task 3: Tariffs, storage, acceptance, and deductions

**Files:** `tenant-app/wb_api.py`, new `tenant-app/wb_reports.py`, API route, React finance/control views, tests.

**Deliverable:** tariff coefficients, storage/acceptance/retention detail, and reconciliation against realization reports.

### Task 4: FBS operational workflow

**Files:** `tenant-app/wb_api.py`, `tenant-app/db`, API routes, Supply view, tests.

**Deliverable:** seller warehouses, FBS orders, supplies, statuses, QR code retrieval, and actionable errors.

### Task 5: Product, price, discount, and stock management

**Files:** `tenant-app/wb_api.py`, product routes, Products/Stock views, tests.

**Deliverable:** card sync, price/discount upload jobs, seller warehouse stock, and job status tracking.

### Task 6: Analytics, customer communication, and returns

**Files:** `tenant-app/wb_api.py`, analytics/report storage, Ads/Control views, tests.

**Deliverable:** stock history, funnel/search reports, reviews/questions, returns, and freshness metadata.

### Task 7: Commercial release gate

**Files:** deployment scripts, audit docs, CI/test configuration.

**Deliverable:** tenant isolation checks, clean-clone build, Docker smoke, staging acceptance checklist, rollback procedure, and production release notes.

