# Commercial release gates

**Goal:** Close confirmed release blockers and record evidence for a paid pilot.
**Architecture:** Preserve the React tenant UI and existing data stores. Verify writes on isolated databases, then ship one verified image. Process health alone is not business acceptance.
**Tech stack:** React/TypeScript, FastAPI, SQLite, Docker.
**Spec:** User-approved blockers from this conversation; existing full-product-mobile-release plan.

## Constraints
- Preserve tenant data, redesign, backups and Docker storage.
- No live WB price changes or customer messages as test fixtures.
- No completion on navigation or fabricated price success.
- Deployment commands must stop after any failure.

## Tasks
- [x] Task execution: App.tsx and action-plan API; persist results and timestamps, leave navigation tasks open, prevent success after sync timeout. Regression tests must cover reload and FIFO navigation.
- [x] Finance: exact start/end propagation including export; reject unsupported analytics windows explicitly, never silently replace selected dates. Test historical and invalid windows.
- [x] Prices: verify WB contract, numeric terminal statuses and per-item history; guard duplicate submissions, unknown data and failures. Tests for pending, partial and missing status.
- [x] Reports: queue, scheduler, PDF sections and delivery result persistence; run pipeline against isolated DB and delivery transports. Separate provider receipt from transport mocks.
- [x] Tenant release gates: registration/provisioning, billing access, isolation, backup restore, mobile/browser workflows. Run package suites, report failures and limits.
- [x] Release: inspect diff, build immutable image, verify Docker workflows on isolated data; deploy once with rollback and verify actual API/artifact, not login-page HTTP 200.

## Evidence
See ../../RELEASE_EVIDENCE_2026-09-17.md for 518 automated tests, Docker/PDF/restore proof, deployed image identity and outstanding external acceptance. These checks close the implementation package; they do not assert real provider receipt, payment or store publication.
