# Shared warehouse physical lifecycle UI implementation plan

> **For agentic workers:** Implement task-by-task with independent review before integration; the root agent handles deployment.

**Goal:** Make the accepted physical shared-warehouse API usable from the selected-shop Stock screen without duplicating central stock or inventing marketplace facts.

**Architecture:** A focused React component consumes the frozen fourteen-route API through the existing selected-shop transport. Central catalog setup and target-shop mapping are separate explicit steps. Every state-changing operation uses an immutable submitted snapshot and UUID, retaining that exact request after a lost response; changed input creates a new request.

**Tech Stack:** React/TypeScript, Vitest/Testing Library, existing scopedFetch, FastAPI server contract.

**Spec:** Physical backend 80bdd492de3731eaee21ab414745a6df39bcedff and final financial-source binding contract 986bc260017e9c9fb1ba05499bc6b333754bb54e, mh-redesign-lab/tenant-api/shared_warehouse_api.py, shared_warehouse_setup.py and shared_warehouse_finance.py; integration base 86a590a07b714a6999e1ecfb337c74b707564c2c.

## Global constraints

- No backend changes, production writes or automatic WB writes. Financial-source binding follows only accepted 986bc260 DTO, with no source fabrication or automatic legacy attestation.
- URL target must equal selected shop; contextual stale responses are rejected by existing transport.
- Canonical physical inventory exists once in the tenant; shop ownership is allocation, not another stock database.
- Primary-binding changes trusted identity: only a narrowly validated bootstrap action may refresh session and navigate; generic identity guards remain strict.
- Item creation requires primary selection and owner role. Mapping preview and confirmation must use the actual selected product variant and canonical item UUID.
- Reserve and sale confirmation use server fingerprints. Return preview has no fingerprint: confirm the identical body and reservation revision, never a fabricated digest.
- An explicit source document/date is required; quarantine/release require proof/reason. Read-only permissions are checked before presenting actions.
- Refresh frozen route fixture from all 194 actual final backend records, preserving primary/secondary scope checks.
- UUID recovery is within the current page only. Reload does not recover a previous key automatically; the visible append-only journal shows document source/key/results so users can check uncertain outcomes before creating a fresh movement. No actor-ambiguous localStorage.

## Task 1: Typed transport and preview/replay contracts

**Files:** Create src/shared-warehouse.ts and src/shared-warehouse.test.ts; modify only dedicated selected-shop bootstrap helper if required; refresh src/test-fixtures/shop-route-scopes-stage4.json.

- [ ] Define typed workspace identities, reservations, ownership, documents, holds and operation bodies from source, with explicit unknown/optional fields.
- [ ] Add bounded GET/POST wrappers and a request controller preserving exact UUID/body for retries; preview is invalidated on input/scope change.
- [ ] Test target identity, changed-body new UUID, exact failed retry same UUID, stale revision and mapping/sale fingerprints.
- [ ] Add narrow primary-binding validation and full navigation after fresh session proof; test foreign identity and failed bootstrap remain blocked.

## Task 2: Usable Stock component

**Files:** Create src/SharedWarehouse.tsx and src/SharedWarehouse.test.tsx; modify src/App.tsx to mount it on stock view.

- [ ] Show central-vs-target labels, actual variants, canonical items, mappings and empty/unbound states.
- [ ] Provide owner primary setup and target mapping preview/confirm with plain-language review summary.
- [ ] Provide reserve preview/confirm, reservation commit/receipt/release, sale preview/confirm; show document provenance and allocated units/costs.
- [ ] Provide return source-sale selection, original allocation proof where necessary, available/quarantine disposition, plus quarantine/release forms.
- [ ] Preserve entered documents and exact replay after errors; clear previews after successful operations and context changes.
- [ ] Test read-only actions, explicit variant selection, fingerprint invalidation, failed retry, returned quantity/proof, hold release and wrong scope.

## Task 3: Integration acceptance

- [ ] Run focused transport/component/actual registry tests, full frontend suite, production build and git diff check.
- [ ] Update Graphify AST graph and commit only owned source/tests/plan.
- [ ] Independent source review before root cherry-pick.
- [ ] Root performs matching frozen browser and native API acceptance before production release. Secondary report scheduling remains a separate incomplete task.

## Accepted financial binding addition

Create src/SharedWarehouseFinance.tsx + tests; mount inside the warehouse workspace. GET the bounded 200 financial-source rows; forbid seller mismatch and unresolved variants, select only real sale/return documents. Legacy rows require explicit checkbox plus nonblank documentary proof, and show manual/preliminary source even when canonical cost is known. Preview uses the server fingerprint; confirmation preserves the exact body and UUID on same-page retry. Diagnostics adapt manual_source_fifo separately from exact_fifo; no classification as automated exact. Test missing attestation, foreign seller/ambiguous variant, manual preview and immutable retry.
