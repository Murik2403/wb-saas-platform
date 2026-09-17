# Commercial Release Gap Closure Implementation Plan

**Goal:** Close every confirmed release gap from `docs/RELEASE_GAP_AUDIT_2026-09-17.md`, verify each behavior, and publish a reproducible production image.

**Architecture:** Keep all new tenant functionality in `mh-redesign-lab` and reuse `tenant-app` domain services and encrypted credential storage. Keep gateway billing/account work isolated in `gateway`, and keep legacy Streamlit/frontend frozen except for read-only parity verification.

**Tech Stack:** FastAPI, Pydantic, SQLite, React/Vite, Vitest, pytest, Docker, Nginx, YooKassa API.

**Spec:** `docs/RELEASE_GAP_AUDIT_2026-09-17.md`

## Global Constraints

- Never return WB/Ozon credentials in API responses, logs, exports, or JS bundles.
- Do not duplicate tenant calculations in the adapter or UI.
- Never perform real WB mutations or payments in tests; use mocks and isolated databases.
- New tenant features belong in `mh-redesign-lab`; Streamlit and `frontend/` remain frozen.
- Every change gets API pytest coverage, UI Vitest coverage where applicable, `npm test`, `npm run build`, and production smoke checks.
- Do not delete backups, tenant data, Docker storage, or unrelated files without a reviewed inventory.

---

### Task 1: Credentials and first-run onboarding

**Files:** `mh-redesign-lab/tenant-api/api_server.py`, `mh-redesign-lab/src/data-source.ts`, `mh-redesign-lab/src/App.tsx`, related tests.

- [ ] Add secure WB and Ozon connect/disconnect routes with validation-before-persisting.
- [ ] Add settings forms, scope state, first-run CTA, history-depth control, and sync launch/status messaging.
- [ ] Test invalid credentials are not persisted and credentials never appear in responses or rendered text.

### Task 2: Customer quality and irreversible-action UI

**Files:** `mh-redesign-lab/tenant-api/api_server.py`, `mh-redesign-lab/src/App.tsx`, UI/API tests.

- [ ] Surface sanitized WB errors in the customer-quality panel.
- [ ] Replace native prompt/alert/confirm flows with controlled modal/toast components.
- [ ] Require explicit confirmation before approving or rejecting returns; add pagination/filter state.

### Task 3: FBS supply parity

**Files:** `tenant-app/wb_api.py`, `mh-redesign-lab/tenant-api/api_server.py`, `mh-redesign-lab/src/data-source.ts`, `mh-redesign-lab/src/App.tsx`, tests.

- [ ] Preserve separate FBS orders, supplies, barcodes, and acceptance coefficients with section-level errors.
- [ ] Add read-only supply detail and QR/barcode download route backed by existing WB client method.
- [ ] Add API/UI regression tests for empty, error, and populated payloads.

### Task 4: Financial/reporting completeness

**Files:** `gateway/yookassa_client.py`, `gateway/config.py`, `gateway/billing_routes.py`, `gateway/logic/*`, `mh-redesign-lab/tenant-api/api_server.py`, report tests/docs.

- [ ] Add configurable 54-FZ receipt payload to initial and recurring YooKassa payments.
- [ ] Add refund handling, payment history, receipt links, and access-period recalculation.
- [ ] Ensure report definitions default to all selected metrics, expose delivery setup, and verify finance workbook parity.

### Task 5: Account/SaaS controls

**Files:** `gateway/app.py`, `gateway/templates/*`, `gateway/logic/*`, tests.

- [ ] Add email confirmation, account password/email management, logout-all-sessions, account deletion/data export, and links from tenant profile.
- [ ] Gate provisioning and destructive actions with CSRF, confirmation, and audit logging.

### Task 6: WB retention pagination and price history

**Files:** `tenant-app/wb_api.py`, sync/report services, `mh-redesign-lab` UI/API, tests.

- [ ] Implement bounded multi-page retention retrieval with cache/status.
- [ ] Add price-upload history, per-item errors, preview, and confirmed bulk update.

### Task 7: Release reproducibility, hygiene, and acceptance

**Files:** Docker/deploy scripts, `.gitignore`, CI/docs.

- [ ] Add image git-SHA labels and deployment refusal for images without a source SHA.
- [ ] Inventory artifacts and temporary directories; update ignore rules without deleting tenant data.
- [ ] Run clean-clone, Docker, mobile, all-page, API, UI, dependency, and production checks; update the audit with evidence and remaining manual gates.

