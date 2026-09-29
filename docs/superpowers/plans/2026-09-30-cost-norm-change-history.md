# Cost and production norm change history implementation plan

**Goal:** Preserve before/after values whenever manual product costs or production norms actually change; make history readable in Settings.
**Architecture:** Add an append-only SQLite table and typed DB helper. Writers append snapshots in the same transaction as current values. Add a read-only paginated API and independent Settings panel. Historical financial results and immutable manufacturing release snapshots keep their existing semantics.
**Tech stack:** SQLite/Python/FastAPI/React/TypeScript/pytest/Vitest.
**Spec:** `docs/marketing/PRODUCT_AUDIT_IMPLEMENTATION_2026-09-23.md`, open detailed cost/norm journal gap confirmed against runtime2472bdf.

## Constraints
- History begins at rollout; do not fabricate previous events for existing values.
- Only known non-secret cost/norm fields enter snapshots. Preserve source/actor when available; no invented identity.
- Old runtime must still read migrated schema. No destructive schema change, historical P&L recalculation, customer data test writes, or editing/deleting journal through the API.
- Existing main checkout remains untouched. Export exact committed source for release, encrypted restore gate and immutable-image rollout with rollback.

### Task 1: Atomic journal storage and writers
Files: `tenant-app/db/core.py`, new `tenant-app/db/settings_history.py`, `tenant-app/db/fifo_materials.py`, `tenant-app/db/production.py`, new DB tests.
- [ ] Add table `settings_change_history(id INTEGER PRIMARY KEY, kind TEXT, nm_id INTEGER, before_json TEXT, after_json TEXT, changed_at TEXT, source TEXT, actor TEXT)` with kind/nm/time index; reject UPDATE/DELETE with SQLite triggers.
- [ ] Define `append_settings_change(conn, *, kind, nm_id, before, after, source, actor=None)`; JSON snapshots compare known typed fields and skip unchanged values. Initial creation has before=null.
- [ ] Instrument save_costs/save_production_settings under their existing connection transaction. Preserve function signatures with optional keyword-only source/actor. Acquire write transaction before old-value reads; journal and mutation rollback together.
- [ ] Tests: creation and real change snapshots, no-op dedup, failed journal insertion rolls back current values/batch, repeated init, journal immutable, legacy writers covered.

### Task 2: API writes and paginated read
Files: `mh-redesign-lab/tenant-api/api_server.py`, new API tests.
- [ ] Preserve cost PATCH validation and call shared writer with source=tenant_api and forwarded x-tenant-account actor if present.
- [ ] Journal norm PATCH in the same SQLite transaction; validate the entire batch/duplicates/missing rows before mutation, reject nonfinite fields via existing models.
- [ ] GET `/api/v1/settings/change-history?kind=cost|production_norm&nm_id=<positive>&limit=25&offset=0` returns `{items:[{id,kind,nm_id,before,after,changed_at,source,actor}],total,limit,offset}`; optional filters, limit1..100. No mutations exposed.
- [ ] Tests prove transactional old/new values, no-op, invalid batch untouched, filters/paging, tenant viewer read allowed/write denied and manufacturing snapshots unchanged.

### Task 3: Settings panel
Files: new `mh-redesign-lab/src/SettingsChangeHistory.tsx` and tests, `src/data-source.ts`, `src/App.tsx`, small scoped styles if needed.
- [ ] Typed `getSettingsChangeHistory({kind?,nm_id?,limit?,offset?})` consumes exact API.
- [ ] Settings panel “История норм и себестоимости” shows date, product WB identity, change type, readable before→after fields, source/actor only when present. Known-field Russian labels only, no raw JSON dumps.
- [ ] Filter by product WB ID and type; pagination, refresh, loading/error/retry/empty states. Explain records start when tracking enabled. Panel independently loads; failure cannot hide Settings.
- [ ] UI tests: real before/after formatting, first creation, filtering resets offset, pagination, retry/empty. Build and full relevant frontend tests.

### Task 4: Release acceptance
- [ ] Independently inspect all deltas and full tenant/API/frontend tests plus build. Update code graph.
- [ ] Commit scoped source, export/build with exact full revision, verify image ID/label.
- [ ] Independently restore encrypted tenant backup and run candidate and original APIs on isolated migrated copy.
- [ ] Verified live rollout mvvorokov with previous container retained; new read API, existing business APIs, assets and immutable revision confirmed; document deployment and remaining audit gaps.
