# Safe CSV migration wizard implementation plan

**Goal:** Import local product catalog, material catalog and explicitly valued opening material FIFO balances without overwriting existing records.
**Base:** integration `8a34de29eac3211d6ebc9afc75003a7534b2ccc8`.
**Architecture:** Text CSV + explicit field mapping → pure row validation/read-only tenant preview → fresh validation and one atomic confirmed transaction. User-supplied source document identifiers produce durable import provenance. Requests and complete file imports are idempotent. No bank/API activity; no production customer writes during development.

- [x] Implement bounded CSV parsing, three typed schemas, semantic duplicates and read-only preview.
- [x] Implement fresh fingerprint confirmation, no-overwrite guards, material physical/FIFO initialization and batch journal.
- [x] Add role/origin-protected API and Settings wizard with mapping, templates, preview, explicit confirmation and retry.
- [x] Verify duplicates, stale data, mixed failures/rollback, source provenance, zero/units/date/rate validation, API permissions, UI and build.
- [x] Commit explicit source and report limits. Root handles integrated release.

Opening imports use base quantities (м/кг/л/шт) into an empty quantity-tracked material only, one opening layer per material, positive explicit unit cost and ISO date. Already populated physical/FIFO history is never replaced. Existing products/material catalogs are rejected rather than silently merged. WB synchronization may later replace local catalog descriptions; imported data is not claimed to be an authoritative WB API response. This initial migration wizard does not import completed manufacturing movements or sales.


Verification 2026-09-30: tenant-app 331 tests + 8 subtests; tenant API 312 tests; frontend 99 tests; TypeScript/Vite build passed. Existing Vite bundle warning remains. Scope excludes finished-goods historical opening layers, sales import, bank transactions, automatic WB catalog writes. Owner/manager confirmation only; viewer/shop-floor preview is read-only. Source journal preserves document/row identifiers, values, actor and batch. Production deployment is handled by integration/release agent after its gate.
