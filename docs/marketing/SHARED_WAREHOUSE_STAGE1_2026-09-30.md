# Shared warehouse: schema and allocator stage

This is the first dependency of the full multi-shop operations audit item, not a completed public workflow. No API router, UI, child snapshot bridge, or production deployment is included.

## Implemented boundary

- Seller identity registry is unique including primary shop 0. The caller must supply a previously verified seller identity. Primary memberships are explicitly migrated; secondary access is owner-only until explicitly granted, within role limits.
- Canonical production items use verified exact catalog variants and explicit shop mappings. Matching external product identifiers never merges inventory automatically.
- Read-only preview selects actual chronological FIFO layers, subtracting every active shop reservation and incident hold. Missing physical stock fails closed; there is no synthetic physical recovery.
- Reserve, release, dispatch commit and WB receipt run under one SQLite writer transaction. Exact replay is idempotent; changed content, stale preview, wrong revision and reverse chronology are rejected.
- Dispatch releases only its own reservation before reducing ready stock. Other shops' reservations remain protected. FIFO layers, legacy product pipeline, immutable document journal and movement costs change atomically.
- SQL guards protect header identity, layer provenance, reservation provenance, immutable receipts/events, physical quantities and allocation/quarantine conflicts, including INSERT OR REPLACE.
- A managed product card cannot bypass the allocator via missing/wrong variant in legacy sale or return processing. The raw sales fact remains, with an explicit FIFO error; no physical layer or movement is manufactured.
- Incident workspace and hold operations report/subtract shared allocation. Owned inbound/WB quarantine is deliberately rejected until its shop-scoped ownership contract is implemented.

## Acceptance and scope

`tenant-app/tests/test_shared_warehouse.py` contains 22 regressions, including concurrent reservations, rollback after second-layer failure, exact catalog identities above one billion, typed synthetic guards, chronological receipt, SQL replace protection, quarantine conflicts and the existing incident hold/workspace/release path with stale warehouse preview. Supply planning subtracts shared reservations and fingerprints reservation/release revisions; manufacturing preview subtracts holds and shared reservations.

Fresh final gates: 433 tenant tests plus 8 subtests, 378 API tests passed. Independent review of allocator plus incident workflow passed; identified blockers were fixed and covered with regressions. Graphify was updated and diff whitespace validation passed. There is no frontend change in this stage.

## Required next dependencies before activation

1. Bind existing verified primary and secondary credentials to the registry; implement role-capped ACL administration.
2. Finish sale, matched return, cancellation and owned quarantine with exact original allocation provenance and no duplicate quantities.
3. Prepare isolated secondary business snapshots, then commit their blob and central warehouse facts under one main SQLite transaction. Do not independently consume secondary physical FIFO.
4. Independently classify all 170 routes in the route inventory; preserve tenant-global production/admin/cash/backup resources and target-shop assignment.
5. Implement explicit selected-shop context and mappings/reserve/confirm UI; failures never fall back to primary.
6. Full private two-shop lifecycle, backup/restore, security and frontend verification before the root releases this feature.

## Rollback

An older image does not understand reservations and could consume protected stock. After any shared warehouse write, rolling back a container alone is unsafe. Restore a verified pre-deployment database and first preserve/reconcile any newer documents and snapshots. Initial production acceptance must use read-only requests; full write acceptance belongs in private fixtures.
