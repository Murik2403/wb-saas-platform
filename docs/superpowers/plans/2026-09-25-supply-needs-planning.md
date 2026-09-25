# Supply Needs Planning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tenant-isolated raw-material planning flow that computes BOM-based shortages without writes, lets a user edit a draft, and creates internal purchase requests only after explicit, retry-safe confirmation.

**Architecture:** Keep demand/BOM/inventory math in a deterministic tenant-app domain module with a read snapshot and pure preview builder. Expose read-only preview and transactional confirm through the authenticated tenant API; mount an explicit draft editor in the existing React Supply view. A confirmation ledger binds the idempotency key to the canonical request hash and created order IDs in the same transaction as the orders.

**Tech Stack:** Python 3.12, SQLite tenant database, FastAPI/Pydantic, React/TypeScript, pytest, Vitest, existing procurement and manufacturing-cost ledgers.

**Spec:** `docs/superpowers/specs/2026-09-25-supply-needs-planning.md`

## Global Constraints

> **Completion status 25.09.2026:** core planner, tenant API, React draft/confirmation flow, HTTP confirm → receipt → FIFO proof, concurrent-confirm coverage, and an isolated-container preview smoke are implemented in the candidate. The acceptance cases added during the 25.09 follow-up are marked below; remaining gaps are intentionally open. See [PRODUCT_AUDIT_RELEASE_STATUS_2026-09-25.md](../../marketing/PRODUCT_AUDIT_RELEASE_STATUS_2026-09-25.md). No production deploy is claimed.

- Preview performs no database writes and creates no reservation, order, movement, FIFO layer, or production task.
- Never infer a variant when `nm_id` demand is ambiguous across different variant BOMs; require a complete explicit allocation or block confirmation.
- Select exact `(nm_id, chrt_id, bom_version_id)` and use a base BOM for a variant only when the existing release fallback rule permits it; label the fallback.
- Unknown physical balances and unknown/incompatible units never count as zero or available stock.
- Subtract only unreceived quantities on active raw-material orders, once, and only when their ETA is on or before the production need date.
- Revalidate the entire preview snapshot at confirm; stale data returns HTTP 409 before any write.
- Idempotency key plus the canonical edited request hash is unique and transactional; changed content with a reused key returns 409.
- Confirmation creates only internal procurement orders with status `Запланировано`; it never contacts a supplier or changes inventory.
- Preserve tenant isolation, tenant roles, demo/viewer read-only behavior, and existing legacy procurement records.

---

### Task 1: Define planner snapshot and pure material-needs calculation

**Files:**
- Create: `tenant-app/db/supply_planning.py`
- Test: `tenant-app/tests/test_supply_planning.py`
- Reference: `tenant-app/db/manufacturing.py`, `tenant-app/db/procurement.py`, `tenant-app/db/production.py`

**Interfaces:**
- `load_supply_plan_snapshot(conn, start_date, end_date) -> SupplyPlanSnapshot` reads saved production tasks, product variants, BOM versions, known material balances/FIFO availability, active procurement items, suppliers, and source revision data from one consistent read transaction.
- `build_supply_preview(snapshot, variant_allocations=()) -> SupplyPlanPreview` is pure: it returns canonical rows, blockers, warnings, and `preview_fingerprint`; it never writes.
- Each preview row includes `material_key`, `material_name`, `unit`, `required_base_qty`, `physical_available_base_qty`, `open_order_covered_base_qty`, `shortage_base_qty`, `pack_size_base_qty`, `suggested_purchase_qty`, `due_date`, `supplier_candidates`, and source BOM/demand references.

- [x] Add failing tests proving an empty snapshot yields an empty preview, a 3-unit task with a BOM requiring 2 m of A and 0.5 kg of B yields 6 m and 1.5 kg, and preview creation leaves order/movement/FIFO/task row counts unchanged.
- [x] Add a test with two SKUs consuming the same material and assert aggregation occurs by `(material_key, normalized_unit)`, while the original SKU/BOM references remain in the preview explanation. `tenant-app/tests/test_supply_planning.py` also verifies that identical size-agnostic variant BOMs retain every contributing variant reference.
- [x] Add exact-variant, base-fallback, no-BOM, and BOM-version tests. Latest exact BOM selection, explicit base fallback, missing BOM blockers, and fingerprint change after a source version change are covered in `tenant-app/tests/test_supply_planning.py`.
- [x] Add ambiguous-demand tests: a size-agnostic plan with differing variant BOMs blocks; an explicit allocation summing exactly to the source units calculates; under-allocation and over-allocation both block.
- [x] Add unit/stock tests: unknown inventory does not cover demand; `m` and `kg` never merge; explicit roll length converts rolls to base units and rounds order packs upward; a missing pack size or incompatible unit blocks. Open-order incompatible units also block.
- [x] Add open-PO tests for eligible, partially received, fully received, cancelled, too-late ETA, and unknown ETA cases. Coverage uses the unreceived quantity, received/cancelled rows never cover twice, and cancelled residual or unknown status blocks for reconciliation.
- [ ] Run `pytest tenant-app/tests/test_supply_planning.py -q` and confirm these cases fail before implementation. Final focused/full suites passed; a saved pre-implementation red-run is not available in the current evidence set.
- [x] Implement immutable typed snapshot/preview values, a canonical JSON serializer, source fingerprint, and pure demand/BOM/coverage/rounding functions. Do not add writes to the planner.
- [x] Re-run the focused tests and verify `load_supply_plan_snapshot` uses one read transaction for all inputs.

### Task 2: Persist confirmation idempotency atomically with procurement orders

**Files:**
- Modify: `tenant-app/db/core.py`
- Modify: `tenant-app/db/procurement.py`
- Create or modify: `tenant-app/db/supply_planning.py`
- Test: `tenant-app/tests/test_supply_planning.py`
- Reference: `tenant-app/tests/test_procurement_wip_integration.py`

**Interfaces:**
- Add `supply_plan_confirmations(confirmation_key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, preview_fingerprint TEXT NOT NULL, order_ids_json TEXT NOT NULL, created_at TEXT NOT NULL)` with a migration safe on populated databases.
- `confirm_supply_plan(conn, confirmation_key, request_hash, preview_fingerprint, preview, edited_lines) -> ConfirmationResult` validates allowed edits, creates one internal order per supplier, creates positions, and records the idempotency result in one transaction.
- Canonical request hash covers the confirmation key's intent: source fingerprint, variant allocations, line identities, edited quantities, supplier IDs, expected dates, currency, user-entered price fields, and notes.

- [x] Add migration tests on a populated legacy tenant database; assert old procurement data remains byte/value-equivalent and the new table starts empty.
- [x] Add transaction tests proving a valid confirmation creates the expected `Запланировано` orders/items and an exception after first line rolls back every order, item, and confirmation row.
- [x] Add idempotency tests: identical key/hash returns the exact existing order IDs without duplicate rows; identical key with a different request hash returns conflict and makes no changes.
- [x] Add authorization-layer/API-independent domain tests proving draft-only edits cannot change material identity, BOM version, variant, or exceed recomputed purchase quantity rules.
- [x] Refactor order creation only as needed to expose one transaction-level helper; retain `create_procurement_order` public behavior and existing `source_key` semantics for callers outside this flow.
- [x] Add the migration and transaction-level confirmation operation. Persist confirmation ledger and all linked orders/items using the same SQLite connection and transaction.
- [x] Run the new tests plus `pytest tenant-app/tests/test_procurement_wip_integration.py -q` to ensure receipt-to-FIFO behavior is unchanged.

### Task 3: Add preview and confirm tenant API routes

**Files:**
- Create: `mh-redesign-lab/tenant-api/supply_planning_api.py`
- Modify: `mh-redesign-lab/tenant-api/api_server.py`
- Modify: `mh-redesign-lab/tenant-api/production_permissions.py`
- Test: `mh-redesign-lab/tenant-api/tests/test_supply_planning.py`
- Reference: `mh-redesign-lab/tenant-api/production_workspace_api.py`, `mh-redesign-lab/tenant-api/manufacturing_costs_api.py`

**Interfaces:**
- `POST /api/v1/supply-planning/preview` accepts `{start_date, end_date, variant_allocations}` and returns the typed preview, blockers, warnings, and fingerprint. It is read-only.
- `POST /api/v1/supply-planning/confirm` accepts `{confirmation_key, preview_fingerprint, start_date, end_date, variant_allocations, edits}` and returns `{confirmation_key, order_ids, order_numbers, replayed}`.
- The confirm route rebuilds the preview from current tenant state. A fingerprint mismatch returns `409` with `code: "stale_preview"`; a reused key with changed content returns `409` with `code: "idempotency_conflict"`; blockers return `422`; read-only role/demo writes preserve existing application policy.

- [x] Add API tests with isolated temporary tenant databases for preview read-only behavior, owner success, viewer write denial, invalid variants, and unknown balance. Domain tests cover missing BOM and open/partial PO coverage; preview/confirm remains tenant-local.
- [x] Add confirm tests for no writes when blocked, stale preview, exact retry, changed retry payload, and rollback after a second supplier order fails. Multi-supplier atomicity is tested at the database transaction boundary; route success/replay and permission contracts are covered separately.
- [x] Implement strict Pydantic request models (`extra="forbid"`), UUID confirmation key, finite nonnegative quantities, and server-side row identity validation.
- [x] Register the router using the existing tenant API mount pattern. Keep preview source loading within the tenant's database path and request context.
- [x] Run `pytest mh-redesign-lab/tenant-api/tests/test_supply_planning.py -q` and the complete tenant API test suite.

### Task 4: Add the React preview, editable draft, and explicit confirmation

**Files:**
- Create: `mh-redesign-lab/src/SupplyPlanning.tsx`
- Create: `mh-redesign-lab/src/SupplyPlanning.test.tsx`
- Modify: `mh-redesign-lab/src/data-source.ts`
- Modify: `mh-redesign-lab/src/App.tsx`
- Modify: `mh-redesign-lab/src/styles.css`

**Interfaces:**
- Add typed `SupplyPlanPreview`, `SupplyPlanLine`, `SupplyPlanBlocker`, and `SupplyPlanDraftEdit` types in `data-source.ts`.
- Add `previewSupplyPlan(input)` and `confirmSupplyPlan(input)` transport functions; do not call confirm from preview or draft-edit handlers.
- `SupplyPlanning` receives the tenant role/demo read-only flag and offers date/source controls, variant allocation, a calculation table, editable supplier/quantity/date fields, and a separate confirmation action.

- [x] Add component tests that render a preview, edit quantity/supplier/ETA, and assert no POST to `/confirm` occurs until the user activates the explicit confirmation button.
- [x] Add UI tests that block confirmation for ambiguous variants, incomplete allocation, unknown balance, missing BOM, unit mismatch, invalid pack quantity, and stale preview.
- [x] Add success/retry tests showing order numbers once, retaining the draft on a stale/error response, and disabling duplicate submit while a confirmation is in flight.
- [x] Add API wrapper tests for request paths, typed response fields, and structured error-code parsing.
- [x] Implement the React flow with accessible labels/status announcements. Clearly distinguish forecast/recommendation, internal planned request, and supplier-confirmed order.
- [x] Mount the component in the existing Supply view without replacing supplier, receipt, payment, or WB shipment operations.
- [x] Run `npm test -- --run SupplyPlanning`, the entire frontend suite, `npx tsc -b`, and `npm run build`.

### Task 5: End-to-end invariants and local release verification

**Files:**
- Test: `tenant-app/tests/test_supply_planning.py`
- Test: `mh-redesign-lab/tenant-api/tests/test_supply_planning.py`
- Test: `mh-redesign-lab/src/SupplyPlanning.test.tsx`
- Verify: `scripts/check_clean_clone.py`, `scripts/check_promotion_sources.py`, `mh-redesign-lab/Dockerfile.tenant-staging`

- [x] Add an end-to-end API/database test: saved exact-variant plan → exact BOM preview → physical/open-PO coverage → edited draft → confirmed planned PO → receipt → material FIFO layer; assert no movement/FIFO change before receipt. Covered by `tenant-app/tests/test_supply_planning_receipt_e2e.py`; HTTP preview/confirm routes are exercised before the real receipt/FIFO path.
- [x] Add a stale-source test that changes BOM, stock, task quantity, or open PO after preview and confirms no order is written until a refreshed preview is confirmed.
- [x] Add concurrent/replayed confirm coverage for the same UUID so only one order set exists and both callers observe identical IDs or a typed conflict. The concurrent database test confirms one planned order set and identical IDs; it passed in eight repeated runs.
- [x] Run all tenant-app tests, all tenant API tests, all frontend tests, TypeScript/build, `python scripts/check_promotion_sources.py`, `python scripts/check_clean_clone.py`, and `git diff --check` on a clean commit.
- [x] Build `mh-redesign-lab/Dockerfile.tenant-staging` with `REDESIGN_BUILD_ID` equal to the exact candidate code commit and run the tenant image locally bound to `127.0.0.1`.
- [x] Verify the final candidate image `mh-tenant-audit:02340b0` (code SHA `02340b08a8b72c8ff06a6505d0d1f186d7baeca9`) in an isolated loopback Docker fixture: healthy, `/healthz` and `/` return HTTP 200; preview is read-only; explicit confirm creates only an internal planned PO; API receipt creates the expected FIFO material layer. The full smoke also generates a 21-section PDF. Image OCI revision and `/tmp/redesign-build-id` match the source SHA.
- [ ] Do not deploy to production until the standard encrypted production backup succeeds and the release owner authorizes the production rollout; do not generate, replace, or expose encryption keys.
