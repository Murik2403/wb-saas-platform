# Complete remaining product audit implementation

Base: deployed tenant runtime e7c39ff and acceptance documentation 3d53991. Existing production fixes remain authoritative. Dirty main and Claude work are preserved. No comparison or links to competitor accounting software return to public content.

## Work allocation and acceptance

1. Procurement agent: port existing planned-material needs implementation onto this base; chronological task/BOM/variant stock and incoming coverage, editable non-writing preview, atomic idempotent confirmation, roles and tests.
2. Manufacturing agent: port nested semi-finished FIFO; stable intermediate SKU/variant identity, cycle guards, immutable consumed producer provenance, parent/child reversal guards, transactional shortage/idempotency and React/API tests.
3. Cash-flow agent: separate actual/planned cash ledger and dated opening anchor, validated RUB conversion, cancellation history, forecast/actual roll-forward; no accrual revenue counted as cash; full API/UI tests.
4. Root: multiple shop workspaces with isolated data/credentials/sync and source-specific financial coverage; verify actual current Ozon contracts before implementing finance ingestion, sales/returns/stock mapping and P&L. Unknown mappings must not become fabricated zero profit. New shops must not overwrite main WB data or credentials.
5. Root integrates only scoped verified commits into an isolated release branch, runs all DB/API/UI suites/build, graph update, clean source gate, exact-image build, encrypted independent backup restore and old-image rollback rehearsal. Deploy user tenant, verify all existing and new business APIs, static assets and source revision; retain rollback. New-shop source data must be included in native backup and independent restoration.

## Multiple shop technical approach

Files: new tenant-app/db/shop_workspaces.py (metadata and encrypted credentials), tenant-app/shop_workspace_worker.py (isolated child process), new tenant API shop_workspaces_api.py, new React ShopWorkspaces.tsx/CSS/tests and API/db tests; narrow App/router integration.

Persist each secondary WB workspace SQLite snapshot in the parent SQLite row, including per-shop sync state/product identities/costs/FIFO data. This intentionally reuses existing tested WB business calculations without changing global DB_PATH inside the shared API process. Child execution uses private temporary DATA_DIR, credentials only on stdin, existing tenant key only in inherited environment, own locks/settings and DB, hard timeout and bounded snapshot size. Save a completed new snapshot atomically with an optimistic revision check; failure leaves previous snapshot available. Binary snapshot and encrypted credentials are included in the parent's existing encrypted database backup. Report/update actions operate against exactly one selected shop; reporting never syncs silently. Default WB data remain in the original main database and report.

Tests: duplicate marketplace account identity, cipher/no credential responses, malformed identity/path prevention, isolated shop costs/sync, snapshot rollback on failed worker, concurrent revision rejection, consolidated amounts/coverage and cancelled/inactive shop exclusion, viewer mutation denied, UI filters/errors/read-only controls. Integration acceptance includes native encrypted restore preserving secondary snapshot bytes.

## Current Ozon evidence

Read-only production probe 2026-09-30: existing configured prices endpoint200; formerly used finance transaction endpoint404 (nonJSON); accrual/by-day400 validates mandatory 10-character Date, valid date200 with accruals and last_id. Full contract/financial row mapping remains unverified. Official documentation currently challenges both HTTP and in-app browser access. This is not permission to use a guessed mapping or declare full Ozon complete.

## Closure criteria

Each requirement must have source, test and deployment evidence. Original intentionally excluded market analytics/browser plugin/price controls are not added. Mobile/form acceptance and marketing claims remain grounded in implemented capabilities. No blanket 'audit complete' while any accepted implementation or production acceptance is missing. Account privacy and secrets stay protected; synthetic accounting operations are limited to isolated copies.
