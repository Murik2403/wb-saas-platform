# Nested Semi-Finished FIFO and Reversal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use inline execution in this task; the user has authorized completion of the product audit. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support versioned production routes that consume semi-finished product lots at historical FIFO cost, preserve stage lineage, and permit only dependency-safe reverse-order cancellation.

**Architecture:** Extend the existing variant-specific BOM component contract with a typed `raw_material` or `semi_finished` component. Each production release remains an immutable movement and creates its own output FIFO layer; a semi-finished component consumes `ready` units from the exact `(nm_id, chrt_id)` finished-goods FIFO via an allocation linked to the consumer movement. The BOM dependency graph is validated as a DAG before saving or releasing. Reversal replays the dependency chain in reverse: a producer cannot be undone while its output layer has downstream allocations, and a consumer undo restores the exact layers it consumed. Existing raw-material BOMs and WIP blank batches retain their behavior.

**Tech Stack:** Python, SQLite, pytest, FastAPI, React, Vitest, Graphify.

**Spec:** `docs/marketing/PRODUCT_AUDIT_2026-09-23.md` and the remaining-gap statement in `docs/marketing/PRODUCT_AUDIT_RELEASE_STATUS_2026-09-25.md`.

## Global Constraints

> **Completion status 25.09.2026:** implementation and normal test suites for Tasks 1–6 are complete in the release candidate; see [PRODUCT_AUDIT_RELEASE_STATUS_2026-09-25.md](../../marketing/PRODUCT_AUDIT_RELEASE_STATUS_2026-09-25.md) for exact revisions, counts, and limitations. Task 7's full image-level multi-stage business probe and production release gate remain open. Checkboxes are updated only for work confirmed by the candidate and its tests.

- Keep tenant data isolated in the tenant SQLite database; never use production data in tests.
- Preserve the current BOM version and exact WB-variant (`nm_id`, `chrt_id`) rules; `chrt_id=0` is valid only for a product explicitly verified to have no variants.
- Every release uses an immutable source key and idempotent request hash; a replay with changed inputs must fail without postings.
- Never estimate a FIFO allocation when exact lots do not cover the required semi-finished quantity; reject the whole release atomically.
- Keep each FIFO allocation, inventory movement, physical stock/pipeline delta, equipment/labor snapshot, and cost batch in one transaction.
- A downstream allocation of an intermediate lot prevents reversing its producer until downstream releases have been reversed.
- Preserve legacy raw-material BOM records and the separate `wip_blank_batches` workflow; migration must not reinterpret old JSON or mutate historical rows.
- Every exact allocation reversal restores only its recorded units and marks the allocation reversed; reversal twice is idempotent.
- Do not deploy this feature without encrypted backup/restore verification, staging, rollback, and business probes.

---

### Task 1: Lock component and route contracts with failing tests

**Files:**
- Modify: `tenant-app/tests/test_manufacturing.py`
- Modify: `tenant-app/tests/test_fifo_finished_goods.py`
- Reuse: `tenant-app/db/manufacturing.py`, `tenant-app/db/fifo_finished_goods.py`

**Interfaces:**
- A raw BOM component keeps the existing shape `{material_key, material_name, quantity_per_unit, unit}`.
- A semi-finished BOM component is `{component_type: "semi_finished", nm_id: int, chrt_id: int, quantity_per_unit: number, unit: "шт"}`; raw components serialize with `component_type: "raw_material"` when newly saved.
- Each saved `manufacturing_releases.materials_json` snapshot stores the normalized typed components and per-component exact FIFO allocations, so undo does not re-resolve a changed BOM.

- [ ] Add failing route tests: raw-only existing BOM still saves/posts exactly as before; a semi-finished component consumes 2 pieces per final unit from two different dated intermediate layers and records both exact layer IDs and costs; wrong `chrt_id` and insufficient ready units fail with no movement, stock, FIFO, release, or equipment change.
- [ ] Add failing graph tests: `A -> B -> C` is valid; `A -> B -> A` and a self-edge fail; edges are variant-specific; a dangling/inactive product identity fails closed.
- [ ] Add failing reversal tests: reverse the final consumer restores each exact intermediate layer; producer reversal is blocked while any active downstream allocation exists; after reversing consumers, producer reversal restores its raw-material layers and deletes/reverses its output layer; replayed undo changes nothing.
- [ ] Run focused tests and confirm they fail on the current implementation because `save_bom` accepts only raw materials and `_post_release_conn` consumes only material FIFO. The final implementation and passing regressions are verified; a saved pre-implementation red-run is not available in the current evidence set.

### Task 2: Normalize and validate typed, versioned BOM components

**Files:**
- Modify: `tenant-app/db/manufacturing.py` (`save_bom`, `_post_release_conn` validation)
- Modify: `mh-redesign-lab/tenant-api/manufacturing_costs_api.py` (request/response validation)
- Test: `tenant-app/tests/test_manufacturing.py`
- Test: `mh-redesign-lab/tenant-api/tests/test_manufacturing_routes.py`

**Interfaces:**
- Add a private pure helper `normalize_bom_components(conn, components) -> list[dict]` that validates every component and returns a canonical representation.
- Add a private `validate_bom_dependency_graph(conn, target_nm_id, target_chrt_id, components) -> None` that resolves each exact component variant's latest saved BOM and rejects cycles, missing recipes, invalid catalog identities, and depth greater than 8 with a Russian `ValueError`.
- Keep the external BOM endpoint paths and version IDs unchanged; both kinds of component use the existing `materials` JSON field in the API contract.

- [x] Implement the test-first raw/semi-finished parsing, normalization, and unit checks; raw components resolve their unit through `material_inventory_color`; semi-finished components require a known no-size product or a catalog-listed exact `chrt_id` and use units `шт`.
- [x] Implement a depth-first route validator using a visited recursion stack and memoized resolved BOM IDs; reject cycles and missing descendant BOMs without writing a new BOM version.
- [x] Preserve `chrt_id=0` base-BOM fallback only under the existing catalog rule and serialize which exact BOM version each nested component resolves to.
- [x] Run the focused manufacturing route tests; verify duplicate source keys with identical payload skip and changed payload conflicts before any write.

### Task 3: Consume intermediate product layers into a downstream release

**Files:**
- Modify: `tenant-app/db/manufacturing.py` (`_post_release_conn`)
- Modify: `tenant-app/db/fifo_finished_goods.py` only if a narrowly scoped internal helper is required
- Test: `tenant-app/tests/test_manufacturing.py`
- Test: `tenant-app/tests/test_fifo_finished_goods.py`

**Interfaces:**
- Add internal `consume_finished_goods_component(conn, movement_id, component) -> dict` that calls `_move_finished_goods_fifo` from `ready` to `out`, for the exact component variant, and returns `{cost_rub, allocations}`.
- The release snapshot records each component's kind, identity, BOM version, required units, exact allocations, and amount; historical release cost reads this snapshot and is not recalculated from current BOMs.

- [x] For each semi-finished component calculate `ceil-free required units = release quantity × quantity_per_unit`; reject fractional piece counts rather than silently rounding.
- [x] Consume exact ready FIFO layers ordered by existing source date then layer ID; if any component is short, roll back the enclosing `BEGIN IMMEDIATE` transaction so no partial BOM material or intermediate component consumption survives.
- [x] Combine raw and semi-finished costs into the release material cost without double-counting; preserve labor, depreciation, packaging, other cost, and output layer creation.
- [x] Assert the intermediate item's `nm_id`/`chrt_id`, layer variant, allocation units, and amount sum exactly match the immutable release snapshot within 0.02 ₽.
- [x] Run tenant manufacturing and finished-goods FIFO suites.

### Task 4: Make nested route reversal dependency-safe and atomic

**Files:**
- Modify: `tenant-app/db/manufacturing.py` (`_undo_manufacturing_conn`)
- Reuse: `tenant-app/db/fifo_finished_goods.py` (`_reverse_finished_goods_allocations`, `_reverse_finished_goods_source_layer`)
- Test: `tenant-app/tests/test_manufacturing.py`

**Interfaces:**
- Add `reverse_release_components(conn, movement_id, release_snapshot) -> None`, reversing each recorded finished-goods component allocation and raw-material allocation inside the existing savepoint.
- Preserve the generic inventory undo route; a producer reversal reports the first dependent downstream movement date/key only if safe to show that operational detail, otherwise returns a generic “reverse later stages first” message.

- [x] Reverse consumer component allocations before its own produced output layer, so the output layer reversal remains guarded by downstream allocations.
- [x] Retain existing equipment chronology guard; additionally block producer reversal if any non-reversed allocations reference its finished layer.
- [x] Verify one reversal restores raw physical stock, exact raw FIFO, exact intermediate ready FIFO, finished output FIFO, equipment lifetime, pipeline quantity, and release status atomically.
- [x] Verify injected failure during any reversal step rolls the entire savepoint back and replaying an already reversed movement remains a no-op.
- [x] Run all production, inventory movement, WIP, raw FIFO, and finished FIFO tests.

### Task 5: Expose typed routes safely in the React manufacturing interface

**Files:**
- Modify: `mh-redesign-lab/src/ManufacturingCosts.tsx`
- Modify: `mh-redesign-lab/src/contracts.ts`
- Modify: `mh-redesign-lab/src/data-source.ts` only if API typing requires it
- Test: `mh-redesign-lab/src/ManufacturingCosts.test.tsx`
- Modify: `mh-redesign-lab/tenant-api/tests/test_manufacturing_routes.py`

**Interfaces:**
- BOM editor component rows discriminate `raw_material` from `semi_finished`; raw rows select a material and unit, semi-finished rows select a catalog product + exact variant and units/piece.
- API response includes the canonical nested BOM snapshot, resolved BOM-version chain, and block reason; UI does not offer a save/release action for invalid or cyclic routes.

- [x] Add component-type selection and variant-aware semi-finished picker without changing default raw-material row behavior.
- [x] Render the nested route/version chain and FIFO consumption summary at release confirmation, including exact layer count, units, cost, and missing-stock blockers.
- [x] Add tests for variant selection, cycle/missing recipe API errors, raw-only regression, insufficient intermediate stock, and disabled submit on blockers.
- [x] Run focused Vitest, TypeScript build, API route tests, and frontend production build.

### Task 6: Prove end-to-end FIFO conservation and stage reversal

**Files:**
- Create: `tenant-app/tests/test_nested_semi_finished_fifo.py`
- Test: `mh-redesign-lab/tenant-api/tests/test_manufacturing_routes.py`
- Verify: `scripts/check_promotion_sources.py`, `scripts/check_clean_clone.py`

- [x] Seed a temporary tenant SQLite database with two raw-material FIFO lots; post stage A to create two semi-finished lots at different costs; post stage B using both lots; assert stage-B unit cost equals summed exact allocations plus direct stage-B costs.
- [x] Post a second consumer against only the oldest lot; assert depletion ordering and per-stage layer balances.
- [x] Attempt reversing stage A first; assert rejection and byte-for-byte unchanged relevant rows. Then reverse stage B and its later consumer in reverse chronology, reverse stage A, and assert restored source balances plus reversed status/history rows.
- [x] Exercise duplicate source-key retry for each stage and idempotent undo; assert there are no duplicate movements, layers, or allocations.
- [x] Assert aggregate raw-material cost + semi-finished FIFO cost equals final output source cost with no double count and no uncosted free component.
- [x] Run the complete tenant-app, API, and frontend suites; run promotion-source and clean-clone gates; run `git diff --check` and `graphify update .`.

### Task 7: Stage a release without bypassing deployment safeguards

**Files:**
- Update: `docs/marketing/PRODUCT_AUDIT_RELEASE_STATUS_2026-09-25.md`
- Verify: `mh-redesign-lab/Dockerfile.tenant-staging`, `scripts/deploy_verified_tenant.py`

- [ ] Build an image labeled with the exact final Git SHA in a fresh local staging database; verify health, manufacturing API, production workspace, and read-only FIFO reconciliation return 200.
- [ ] Re-run a complete temporary-database multi-stage route and reverse-order business probe against the built image.
- [x] Record exact test totals, image SHA, and remaining limitation in the status doc; explicitly state production is unchanged.
- [ ] Do not deploy unless the existing tenant encryption and host backup keys are configured in the server secret store, an encrypted backup is restored and verified, and rollback plus business probes are available.

## Acceptance tests

1. Existing raw-only BOMs post and reverse with identical costs and state transitions.
2. A valid acyclic 3-stage route (raw materials → semi-finished A → semi-finished B → final SKU) creates a distinct exact FIFO layer per stage and version; each downstream release consumes exact same-variant lots at their original unit costs.
3. A route cycle, a missing nested BOM, a missing catalog variant, cross-variant layer, fractional piece requirement, or insufficient intermediate FIFO rejects without any partial writes.
4. Two intermediate source lots are consumed oldest-first, and the saved allocations conserve units and cost to 0.02 ₽.
5. Reversing a producer before every downstream consumer is reversed fails without changing any rows; reverse-order cancellation restores exact source allocations and inventory at all levels.
6. Duplicate request retries and duplicate reversal calls are idempotent; changing payload under an existing source key fails closed.
7. Equipment, labor, packaging, other cost, defects, and existing finished-goods sales/returns remain unchanged except for the exact material-cost composition of the new route.
8. Current P&L and per-SKU direct profit use the final output's released FIFO cost once; intermediate production cost is not counted as a second independent sale or expense.

## Self-review

- Spec coverage: typed components, variant identity, BOM version chain, cycle rejection, exact FIFO consumption, historical cost snapshots, dependency-aware reverse order, UI/API validation, temporary-database conservation, release gates, and the no-deploy safety boundary all have dedicated tasks.
- Scope boundary: this plan is for nested manufacturing routes in the existing manufacturing BOM/release ledger. The older `wip_blank_batches` sewing blank workflow remains distinct and is not migrated by this work.
- Explicit unresolved semantics: this first release rejects fractional semi-finished units and missing descendant BOMs; it does not infer scrap yields, by-products, alternate routes, automatic stage scheduling, or external subcontracts.
