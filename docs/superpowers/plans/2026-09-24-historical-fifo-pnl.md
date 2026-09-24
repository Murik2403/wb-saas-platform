# Historical FIFO P&L Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use inline execution in this task; the user has already requested implementation. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make WB operation-date P&L use verified historical FIFO COGS per financial row, use the current SKU cost only as a labelled estimate for unmatched rows, and expose missing-cost coverage without hiding any source rows.

**Architecture:** Keep `financial_report.rrd_id` and WB `operation_date` as the reporting grain. Reuse `read_financial_fifo_reconciliation()` as the evidence gate: a row is exact only when SRID, operation type, quantity, trusted FIFO allocations, and return linkage all reconcile. Build one hybrid COGS projection from that result and the existing current `cost_per_wb_unit` baseline, then use that projection once for store P&L and once grouped by SKU; never add it on top of the legacy COGS formula.

**Tech Stack:** Python, pandas, SQLite, FastAPI, React, Vitest, pytest, Docker staging.

**Spec:** `C:\Users\mvvor\Downloads\wbsaasplatform\wb-saas-platform-clean\docs\marketing\FULL_PRODUCT_MARKET_AUDIT_2026-09-23.md`, section “P0-2. FIFO существует отдельно от основной прибыли”.

## Global Constraints

- Use WB `operation_date` for both finance and historical FIFO reporting periods.
- An exact row requires a unique `rrd_id` match on `nm_id + srid + operation type + absolute quantity`, verified source SRID, full active allocations from trusted layer sources, matching allocated/event cost within 0.02 ₽, and a verified original sale for returns.
- If any exactness condition fails, route the whole financial row to the current per-SKU cost estimate; never combine partial FIFO and fallback cost for one row.
- Sales COGS is positive; return COGS is negative only when exact or when a positive current baseline exists. Missing/zero baseline is `insufficient`, never certified zero.
- Hybrid COGS replaces the old positive-net-units cost in both store P&L and per-SKU direct profit; do not subtract or add old COGS a second time.
- Preserve all financial rows, including `nm_id=0` and unallocated products, in store-level totals and missing-coverage counts.
- Round only at the response boundary; source row costs and aggregated SKU/store totals must conserve within 0.02 ₽.
- Keep legacy API fields while adding explicit quality, exact, estimated, and insufficient coverage fields.
- Do not use a live tenant database for tests. Any production read-only validation must return only aggregate counts and sums, with no customer/product identifiers or secrets.

---

### Task 1: Specify and test the hybrid COGS projection

**Files:**
- Create: `tenant-app/db/historical_cogs.py`
- Test: `tenant-app/tests/test_historical_cogs.py`
- Reuse: `tenant-app/db/financial_fifo_reconciliation.py`

**Interfaces:**
- Consumes financial rows with `rrd_id`, `nm_id`, sale/return event type and signed/absolute units; reconciliation rows with `rrd_id`, `status`, `eligible_fifo_cogs_rub`, and `baseline_cogs_rub`.
- `project_historical_cogs(reconciliation: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]` consumes the complete output of `read_financial_fifo_reconciliation()` and returns per-`rrd_id` rows, per-`nm_id` totals, and store totals. Each per-row record keeps `rrd_id`, `nm_id`, `event_type`, positive `units`, signed `cogs_rub`, `cogs_source` (`exact_fifo`, `current_cost_estimate`, `insufficient`, `excluded`), and `reason`.
- Group totals include signed exact and estimated COGS, missing units and row counts, exact/estimated/known-unit coverage percentages, and quality (`exact`, `mixed`, `estimated`, `insufficient`, or `no_sales`). Missing COGS has no numeric estimate: it contributes zero to the provisional numeric total while the completeness flag stays false and the UI explicitly says the result is incomplete.

- [x] **Step 1: Add failing projection tests** using reconciliation DataFrames. Cover exact sale with eligible cost 100.00 ₽ across multiple layers; exact return with -40.00 ₽ in its operation period; exact and unmatched sale rows for the same SKU resulting in 100.00 ₽ exact + 30.00 ₽ estimated; duplicate-SRID/ambiguous row falling wholly back to its signed baseline; zero/absent baseline counted as insufficient rather than free; non-sale rows excluded from COGS; `nm_id=0` retained in store totals; duplicate `rrd_id` inputs fail closed as insufficient; and per-SKU sums conserving to store sums.
- [x] **Step 2: Run the focused tests** with `python -m pytest tenant-app/tests/test_historical_cogs.py -q`; the test-first run failed because `db.historical_cogs` did not yet exist.
- [x] **Step 3: Implement the pure projection** without database I/O. Exact rows use finite `eligible_fifo_cogs_rub`; non-exact sale/return rows use finite `baseline_cogs_rub` only if its absolute value is positive; excluded rows contribute zero and no units. Missing/zero baseline rows receive `cogs_source='insufficient'`, zero numeric contribution, and positive missing unit/row counts. If `rrd_id` is null or duplicated, mark every affected event row insufficient and do not silently deduplicate. Aggregate once from the row projection so SKU totals and store totals share identical evidence.
- [x] **Step 4: Run the projection tests** and assert every `rrd_id` occurs once and SKU/store COGS equals the sum of its source rows within 0.02 ₽. Nine pure-projection tests pass.
- [x] **Step 5: Commit** the projection and tests; included in `080812a` (`feat: use historical FIFO COGS in finance P&L`).

### Task 2: Replace legacy store and SKU COGS with the projection

**Files:**
- Modify: `tenant-app/calculations.py`
- Test: `tenant-app/tests/test_calculations.py` and the focused existing financial tests
- Modify if needed: `tenant-app/db/financial_fifo_reconciliation.py`

**Interfaces:**
- `build_dashboard(start, end)` uses the same `operation_date` financial window it already selected.
- The existing `financial.cost` and product `cost` fields become the hybrid COGS sums; added `cogs_quality` data reports exact/estimated/insufficient components and unit coverage.

- [x] **Step 1: Add failing dashboard tests** proving exact historic FIFO remains unchanged against a current rate of 999 ₽, a signed return reverses COGS in its own operation period, a fallback row uses 20 ₽, and old positive-net COGS is not double-counted.
- [x] **Step 2: Run the focused integration test**; it verifies a hybrid total of 105 ₽ versus the former 999 ₽ formula for both store and SKU, and a signed profit of 795 ₽.
- [x] **Step 3: Build the projection from `read_financial_fifo_reconciliation(start.isoformat(), end.isoformat())`**, which queries the same operation-date interval as `f`; assign `financial['cost']` from its store COGS and merge its per-SKU fields into `grouped` by integer `nm_id`. Fill missing SKU aggregation fields with zero but keep store-level `insufficient_units` from the full reconciliation, including `nm_id=0` and technical rows.
- [x] **Step 4: Recompute product direct profit and margin** from the hybrid SKU COGS; keep existing ad/WB/common-pool allocation rules unchanged.
- [x] **Step 5: Run tenant-app and FIFO suites**; all 331 tenant-app tests pass, including conservation and signed returns.
- [x] **Step 6: Commit** as `080812a` (`feat: use historical FIFO COGS in finance P&L`).

### Task 3: Expose the source and coverage in API and Finance UI

**Files:**
- Modify: `mh-redesign-lab/tenant-api/api_server.py`
- Modify: `mh-redesign-lab/src/contracts.ts`
- Modify: `mh-redesign-lab/src/data-source.ts`
- Modify: `mh-redesign-lab/src/App.tsx`
- Test: `mh-redesign-lab/tenant-api/tests/test_finance_api.py`
- Test: `mh-redesign-lab/src/App.test.tsx` and finance adapter tests

**Interfaces:**
- Finance API keeps existing cost and profit keys and adds `cogs_quality`, `exact_fifo_cogs_rub`, `current_cost_estimate_rub`, `insufficient_cogs_units`, and `exact_unit_coverage_percent`.
- Finance UI labels exact FIFO versus current-cost estimate and shows an explicit incomplete-data state when any units lack cost evidence.

- [x] **Step 1: Add API and UI adapter tests** for hybrid totals, incomplete state, and page-row source labels.
- [x] **Step 2: Run focused API and frontend tests**; after updating assertions for the added P&L table column, all related tests pass.
- [x] **Step 3: Map the projection contract through API serialization and TypeScript adaptation** without dropping current response fields.
- [x] **Step 4: Render source totals, coverage, missing-cost warning, and per-row COGS source** beside the existing finance result; the hero labels estimates/incomplete coverage as preliminary.
- [x] **Step 5: Run full finance API and frontend suites plus `npm run build`**; 225 API tests and 62 frontend tests pass; the build succeeds.
- [x] **Step 6: Commit** as `4d5b6c9` (`feat: expose FIFO evidence across finance UI and exports`).

### Task 4: Validate source completeness and stage the release

**Files:**
- Test: `tenant-app/tests/test_historical_cogs.py`, all `tenant-app/tests/`, all `mh-redesign-lab/tenant-api/tests/`, all `mh-redesign-lab/src/` tests
- Verify: `scripts/check_clean_clone.py`, `scripts/deploy_verified_tenant.py`, `mh-redesign-lab/Dockerfile.tenant-staging`

**Interfaces:**
- The staged image revision is the exact final Git commit SHA.
- Production deployment is allowed only after a verified encrypted tenant backup; `WB_SAAS_DATA_ENCRYPTION_KEY` must be configured and the backup archive must carry the encrypted format prefix.

- [x] **Step 1: Run complete suites**: tenant-app 331 passed; tenant-api 225 passed; frontend 62 passed and build succeeds; provisioner 25 passed.
- [x] **Step 2: Run release gates**: `scripts/check_clean_clone.py` reports clean, 18/18 required paths, zero tracked sensitive paths; `git diff --check` clean.
- [x] **Step 3: Build the tenant image** in PowerShell with `$env:REDESIGN_BUILD_ID = (git rev-parse HEAD).Trim(); docker build --build-arg "REDESIGN_BUILD_ID=$env:REDESIGN_BUILD_ID" -f mh-redesign-lab/Dockerfile.tenant-staging -t "marketshelper-tenant:$env:REDESIGN_BUILD_ID" .`; `docker image inspect` confirmed matching OCI revision and image ID `sha256:eef7ceda29f42dbed45b04b9026f91de6ba11db713639bb4a047865ae443e6a7` for `4d5b6c98b958d358c5d866b52b466b20aef446f9`.
- [x] **Step 4: Run network-isolated staging** with a new temporary volume; `/healthz`, React root, today API, custom-period finance API, and FIFO detail API all returned 200. Removed only the specifically named staging container and volume.
- [x] **Step 5: Read-only production validation** was performed against the tenant database without writing to production or exposing identifiers/secrets. For `2026-09-01..2026-09-19`: total COGS 218,604 ₽; exact FIFO 0 ₽ / 0 units; current-cost estimate 218,604 ₽ / 1,290 units; 1 positive unit lacks a cost; known unit coverage 99.92%; quality `insufficient`. The 133 insufficient rows include 132 zero-quantity/technical rows and one positive unit without a rate. The live tenant image predates the FIFO reconciliation endpoint, so these values were calculated read-only from the deployed database using the release's reconciliation code.
- [ ] **Step 6: Deploy with `scripts/deploy_verified_tenant.py` only after encrypted-backup validation succeeds; verify the existing health and four business probes, then confirm rollback container remains available.

## Self-review

- Spec coverage: historical operation-date FIFO, estimate fallback, missing-cost completeness, return reversals, SKU/store conservation, UI/API/export disclosure, and deployment guardrails are all covered by the tasks above.
- Evidence limits: finance line `rrd_id` is the reconciliation grain; absent/duplicate IDs fail closed. Zero baseline is insufficient, while a signed zero cost can be exact only if the reconciliation itself marks the row exact. The September production aggregate has no exact FIFO COGS yet; the new UI will correctly label that period as estimated/preliminary and surface the one positive unit without a rate.
- Staging and live release remain gated by image identity and an encrypted tenant backup. Production SSH is reachable, but the deployed tenant has no `WB_SAAS_DATA_ENCRYPTION_KEY` in its runtime environment and runs older code without the reconciliation endpoint.
- Deployment remains blocked by the encrypted-backup precondition. The same encryption key/keyring protects saved marketplace credentials, so do not generate or rotate it casually. Configure the existing key/keyring through the production secret manager; do not paste it into chat. Then rerun the verified deployment and health/business probes, retaining the rollback container.
