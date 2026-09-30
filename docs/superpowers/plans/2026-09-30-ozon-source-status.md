# Ozon Source Status Implementation Plan

**Goal:** Distinguish a completed API collection from an unverified monetary interpretation in the existing Ozon screen.

**Architecture:** Consume the existing independent backend flags in `OzonAccounting.tsx`. No backend, ledger, price, stock or accounting changes. Missing flags stay unverified; do not infer collection completeness from legacy `source_complete`.

**Tech Stack:** React, TypeScript, Vitest, existing tenant container.

**Spec:** `docs/marketing/AUDIT_CLOSURE_MATRIX_2026-09-30.md`, P2 Ozon and source transparency.

## Global Constraints

- API normalization is not implemented; no claim of exact API profit.
- Unknown results remain unknown; no customer data mutations for acceptance.
- Review, tests, fresh exact build, encrypted restored backup, rollback, production release and live verification precede completion.

## Task: Correct collection/interpretation status

Files: `mh-redesign-lab/src/OzonAccounting.tsx`, `OzonAccounting.test.tsx`.

- [ ] Add a regression with `source_collection_complete=true`, `source_complete=false`, `financial_normalization_verified=false`; expect complete collection and unverified interpretation, never incomplete collection or calculated profit.
- [ ] Add missing/false collection cases, including legacy `source_complete=true`; do not claim all pages collected.
- [ ] Reproduce failing test using `npm test -- --run src/OzonAccounting.test.tsx`.
- [ ] Add optional boolean flags to Workspace. Render `Загрузка API` using only strict `source_collection_complete === true`; render monetary interpretation separately using strict `financial_normalization_verified === true`.
- [ ] Run targeted tests, all frontend tests and production build from a fresh exact Git archive.
- [ ] Obtain independent review of the complete diff; commit source.
- [ ] Build exact runtime, compare actual image bytes and immutable base lineage, restore a fresh encrypted backup and test new/rollback images.
- [ ] Deploy tenant and pin provisioner; verify live revision, API/assets and source-status screen without changing client data.

## Wider audit

Parallel agents reconcile every original requirement, investigate official Ozon monetary contracts, and perform CSV/keyboard acceptance. Interviews, real historical documents and conditional marking remain evidence-dependent; this status fix cannot close those requirements.
