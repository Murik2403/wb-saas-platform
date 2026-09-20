# Tenant Manual Expenses Implementation Plan

> **For agentic workers:** implement this plan task-by-task and verify each task before continuing.

**Goal:** Store seller-entered external expenses per tenant, SKU, article, and period without changing accounting-backed WB profit.

**Architecture:** Each tenant container already owns an isolated SQLite database. Add a validated `manual_expenses` table and a small CRUD API. The table is an input register for future management calculations; current WB financial reports remain unchanged.

**Tech Stack:** Tenant SQLite, Python DB helpers, FastAPI/Pydantic, pytest.

**Spec:** `gateway/logic/unit_economics.py` article and allocation contract.

## Global Constraints

- Never subtract WB commission twice; `ppvz_for_pay` remains the accounting source.
- Manual expenses are not included in current accounting-backed profit until a separate calculation view is implemented.
- Every row has an `nm_id`, article, period, allocation basis, source, and audit timestamps.
- Tenant isolation is provided by the existing per-tenant database/container boundary.

### Task 1: Tenant register and API

**Files:** `tenant-app/db/core.py`, `tenant-app/db/manual_expenses.py`, `tenant-app/api_server.py`

- [x] Add the constrained table and index.
- [x] Add validated save/read/delete helpers.
- [x] Add GET/POST/DELETE API routes with neutral validation errors.

### Task 2: Verification

**Files:** `tenant-app/tests/test_manual_expenses.py`, `tenant-app/tests/test_manual_expenses_api.py`

- [x] Cover date overlap filtering, allocation bases, invalid articles/periods, and delete behavior.
- [x] Cover API request/response contract.
- [x] Run the full tenant test suite before any deployment.

### Task 3: Follow-up integration

- [x] Add an explicit management calculation view that reads this table through the shared nine-article contract.
- [x] Add UI editing with source and coverage indicators.
- [x] Keep accounting-backed profit and WB commission semantics unchanged.
