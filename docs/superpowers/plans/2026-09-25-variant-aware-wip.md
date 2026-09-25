# Variant-Aware WIP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carry exact Wildberries item and variant identity through WIP issuance, completion, consumption, reversal, and finished-goods FIFO.

**Architecture:** Extend the WIP batch ledger with `nm_id/chrt_id`, preserve old rows as unassigned `0/0`, and scope all availability/consumption queries by both identifiers. Validate variant choices against the product card; propagate the identity through movements and cost/FIFO layers; reject unsafe legacy or cross-variant consumption.

**Tech Stack:** Python 3.12, SQLite migrations, FastAPI/Pydantic, React/TypeScript, pytest, Vitest, Docker.

**Spec:** `docs/superpowers/specs/2026-09-25-variant-aware-wip.md`

## Global Constraints

- Keep `chrt_id=0` as the legacy/base identity; never infer a variant for old WIP batches. Existing legacy WIP may remain consumable only in base/non-variant FIFO.
- Never consume WIP across different `(nm_id, chrt_id)` identities.
- Validate catalog variants from `products_catalog.raw_json` before issuing or packaging variant stock.
- Keep issue/completion/package idempotency and reversal guards inside database transactions.
- Do not change production tenant state or expose credentials.

---

### Task 1: Migrate and scope WIP batch storage

**Files:**
- Modify: `tenant-app/db/core.py`
- Modify: `tenant-app/db/wip.py`
- Modify: `tenant-app/pages/production.py` only if the new manual React identity needs a parallel user-visible label; retain current Streamlit base callers without forced variant assignment.
- Test: `tenant-app/tests/test_production_workspace.py` or the existing WIP DB test module

**Interfaces:**
- `wip_blank_batches` gains `nm_id INTEGER NOT NULL DEFAULT 0`, `chrt_id INTEGER NOT NULL DEFAULT 0`.
- `issue_wip_material(..., nm_id: int = 0, chrt_id: int = 0)` stores the identity on the batch and issue movement.
- `_wip_available_units(conn, material_key, blank_type, nm_id, chrt_id)` and `_consume_wip_fifo(..., nm_id, chrt_id)` operate on an exact identity.

- [ ] Add a populated-database migration test: create an old-format WIP batch and allocations/movements, run `init_db`, then assert old values/links are unchanged and new fields are `0`.
- [ ] Add tests for two otherwise identical WIP batches with distinct variants; consuming variant A must allocate only A, and insufficient A must fail without changing B or A. Also assert an unassigned legacy batch can be used only for `chrt_id=0`, never for a positive variant.
- [ ] Run those tests and confirm they fail before implementation.
- [ ] Add columns to new table creation and idempotent `PRAGMA table_info` migrations; add a FIFO index ending in `(nm_id,chrt_id,batch_date,id)`.
- [ ] Store exact identity in issue movement and WIP batch; filter availability and FIFO rows by identity; retain legacy 0/0 rows without auto-assignment.
- [ ] Re-run migration and WIP tests; verify transaction rollback leaves quantities and allocations unchanged on shortage.

### Task 2: Carry identity through completion and reversal

**Files:**
- Modify: `tenant-app/db/wip.py`
- Modify: `tenant-app/db/inventory_movements.py`
- Test: WIP DB and inventory movement tests

**Interfaces:**
- `complete_wip_blank_batch(batch_id, ...)` copies batch `nm_id/chrt_id` into its movement and cost batch.
- `_post_wip_packaging_conn(..., chrt_id: int = 0)` consumes exact identity and writes it to movement, cost batch, and finished-goods layer.
- `post_manual_wip_packaging(..., chrt_id: int = 0)` validates the exact production settings/product identity.

- [ ] Test completion movement/cost batch identity and exact finished-goods layer after packaging.
- [ ] Test completion reversal is refused after any active downstream WIP allocation; test package reversal restores units to the same batch only.
- [ ] Preserve current Streamlit callers and legacy/base path with `chrt_id=0`; ensure an unassigned legacy batch never satisfies a positive variant package.
- [ ] Implement minimal identity propagation and add exact-variant predicates to consume queries.
- [ ] Run all WIP, manufacturing release, FIFO finished-goods, and inventory movement tests.

### Task 3: Validate WIP API inputs and production tasks

**Files:**
- Modify: `mh-redesign-lab/tenant-api/production_workspace_api.py`
- Modify: `tenant-app/db/production.py`
- Test: `mh-redesign-lab/tenant-api/tests/test_production_workspace.py`
- Test: `tenant-app/tests/test_procurement_wip_integration.py`

**Interfaces:**
- `IssueInput` includes `nm_id`, `chrt_id`, and optional `task_key`; if task key is supplied, use its saved identity and reject any mismatch.
- `PackageInput` includes nonnegative `chrt_id`; idempotency comparison includes it.
- Product/variant validator checks `nm_id/chrt_id` against the matching product card in `products_catalog.raw_json`.
- Production shift close may process `chrt_id>0` WIP only after exact availability validation; otherwise return an error before any movement/cost write.

- [ ] Add API tests for valid catalog variant, unknown variant, mismatched task identity, exact retry, and same UUID with changed variant returning 409.
- [ ] Add a production shift test proving cross-variant shortage creates no movement, no cost batch, and no WIP allocation.
- [ ] Implement catalog validation and wire identity through issue/package/task close.
- [ ] Run the full tenant-app and tenant API suites.

### Task 4: Add explicit variant selection in the React workspace

**Files:**
- Modify: `mh-redesign-lab/tenant-api/production_workspace_api.py`
- Modify: `mh-redesign-lab/src/ProductionWorkspace.tsx`
- Modify: `mh-redesign-lab/src/ProductionWorkspace.test.tsx`

**Interfaces:**
- Workspace response returns product options as `{nm_id, chrt_id, variant_label, product_name, supplier_article}`.
- Issue and packaging forms submit the selected pair; batch history displays product and variant and labels legacy unassigned stock as such.

- [ ] Add component tests selecting two variants of one nmID and asserting distinct issue/package payloads.
- [ ] Add a legacy batch test asserting the UI labels it unassigned and does not offer it as a variant batch.
- [ ] Implement variant option parsing from `products_catalog.raw_json`; reuse the same size/barcode labels as the manufacturing-costs selector.
- [ ] Run the entire frontend suite, `npx tsc -b`, and `npm run build`.

### Task 5: Validate candidate image and release evidence

**Files:**
- Modify: `docs/marketing/PRODUCT_AUDIT_RELEASE_STATUS_2026-09-25.md`
- Verify: `scripts/check_clean_clone.py`, `scripts/check_promotion_sources.py`, `mh-redesign-lab/Dockerfile.tenant-staging`

- [ ] Run full tenant-app/API/frontend tests and `git diff --check`.
- [ ] Commit the verified code in the isolated release branch; run the clean-release gate against the clean commit.
- [ ] Build tenant image with `REDESIGN_BUILD_ID` equal to the full code commit SHA.
- [ ] Run the image locally on `127.0.0.1`, wait on tenant API readiness and Docker `healthy`, verify WIP and expense APIs, then remove the smoke container.
- [ ] Update the candidate status with exact tests, commit, image revision, and remaining backup/staging/production gates.
