# Ozon reconciled P&L and historical cost Implementation Plan

> For agentic workers: execute the bounded tasks below in an isolated worktree, independently review before integration.

**Goal:** separate Ozon source reconciliation and document-backed historical SKU FIFO, with unknown values retained and no WB inventory mutation.
**Architecture:** append-only raw source versions plus complete-day manifests choose the effective source set; deterministic normalization/projection links monetary events and Ozon cost lots by Ozon-only identities. Exact output requires proven quantities, return-to-sale identity and reconciled signed components; otherwise explicit error/unknown, never zero cost.
**Tech Stack:** existing Python/FastAPI/SQLite, Decimal, React/TypeScript. No external writes.
**Spec:** FULL_PRODUCT_MARKET_AUDIT_2026-09-23.md §5.4 (main docs/marketing), root work order 2026-09-30.

## Verified source and constraints

- Official documentation https://docs.ozon.ru/api/seller/ challenged this run (browse redirect loop, direct HTTP403). Do not claim its complete contract was read.
- Official Ozon Seller API notification https://t.me/s/OzonSellerAPI?before=678 (primary channel) fetched200: 9 July2026 renamed top-level type_id to accrual_id; 14 July2026 says old transaction endpoints retire8September;21/30July update nested delivery services/container fees and categories. Thus accrual_id alone is not assumed a globally unique external transaction ID.
- Primary current API read /v1/finance/accrual/by-day for2026-09-01,09-18,09-25 returned empty arrays/no cursor. Production raw ledger/snapshot tables empty. No contacts or credentials printed, no DB/marketplace writes.
- Existing collector confirmed endpoints retained. Exact POSTING money component interpretation must be validated by accessible primary schema or real sanitized source before claiming normalized complete P&L. Unknown shapes fail closed and remain inspectable; fixtures for invented business cases are explicitly synthetic.
- Separate identity (Ozon seller/sku/posting/event), never using a numeric Ozon SKU as WB nm_id or reusing WB cost movements automatically.
- Historical cost from explicit dated Ozon receipts/opening lots with source/document, actor and reason; current cost mapping remains estimated. No bank/tax/net-profit promises.

## Task1 — source contracts and immutable ledger

Files: new tenant-app/db/ozon_pnl.py, existing db/ozon_accounting.py and client, tests/test_ozon_pnl.py.
- [ ] Obtain/redact primary nonempty POSTING/ITEM/NON_ITEM/CONTAINER samples; record exact field/date/sign semantics and limits.
- [ ] Validate currencies/dates/amounts, full page completion and snapshot scopes before any write; use Decimal.
- [ ] Append immutable source versions and manifests. Repeat same complete-day payload is idempotent; overlapping imports reuse immutable rows, revisions never delete older history. Distinct observed occurrences are retained, ambiguities explicit.
- [ ] Define signed revenue/return/commission/service components, preserve unknown/residual and reconcile exact sums to authoritative source total. No nested fee counted twice, no arbitrary net-accrual→revenue inference.
- [ ] Tests revisions/overlap/duplicates/rollback and update/delete/replace history protection, invalid currency/unknown shapes.

## Task2 — Ozon-only historical FIFO projection

Files: db/ozon_pnl.py and focused tests.
- [ ] Immutable dated cost receipts/opening lots, explicit Ozon sku/unit quantities/currency/source/reason and replayUUID.
- [ ] Match documented movement identity/quantity chronologically to eligible Ozon lots; allocations and projection revisions retain lot provenance.
- [ ] Returns link original posting/SKU/sale event and reverse exact allocated cost proportionally, bounded by original sold quantity. Ambiguous/unmatched/excess returns remain unknown and do not invent stock/profit.
- [ ] No real stock or WB tables modified by financial import. Repeated sync/recalculation does not consume twice. Historical lots added after a prior report produce a new labelled projection version.
- [ ] Tests2pricedlots/backdatedreceipt/dateboundary/partial/excess/unmatchedreturn/reimport/crossSKU/crossWB isolation/unknown costs.

## Task3 — protected API and usable Finance interface

Files: tenant-api/ozon_accounting_api.py, src/OzonAccounting.tsx/css, respective tests.
- [ ] GET separate period-based reconciled Ozon P&L and provenance lines; coherent scope and selected-source revision.
- [ ] Owner/manager dated receipt input with origin/role/demo/replay guards; unknown IDs/provenance rejected. Readers can inspect.
- [ ] Show monetary reconciliation, gross revenue/returns/commission/service components separately, known historical cost, unknown units, quality/errors and source dates. Unknown totals do not render0.
- [ ] Accessible receipt form and sale/return/lot details. Existing current-rate mapping clearly remains estimate, not retroactive exact FIFO.
- [ ] Tests read-only/error/retry/scope/formulas and UI build.

## Task4 — release acceptance

- [ ] Independently inspect source with close_procurement; run tenant/API/React full gates, graph update, diffgate; commit explicit files.
- [ ] Root alone performs encrypted-backup release, deployment and authorized live UI. Do not conflate code fixtures with tested production Ozon financial coverage.

## Open acceptance boundary

Nonempty primary contract samples are required to finish exact live normalization. Empty source proves only read access and cannot validate nonempty fee/sale arithmetic. Shared stock with WB, official cost accounting for all warehouses, bank payouts and tax reporting are separate modules; do not claim these from a financial Ozon-only cost ledger.

## Accepted fallback contract (root approved)

Until nonempty primary API financial semantics are verified, implement an explicitly manual Ozon document CSV contract (not pretend automatic API normalization): stable external event ID, financial accrual timestamp and separate movement timestamp with timezone, kind sale/return/fee, Ozon SKU and posting reference, quantity, signed revenue/commission/logistics/other, authoritative signed total, RUB and document reference. Every signed component must be present; their Decimal sum must equal the provided total. Returns refer to an original external sale ID in the same source namespace. Explicit dated receipts/opening lots provide cost and document/source reason. Cost projection versions preserve allocations without changing physical Ozon/WB stock. API raw source snapshots remain immutable versions and manual results clearly distinguish source from API coverage. Root specifically forbids further mass real-date querying and guessed complete normalization.


## Implemented contract and remaining external boundary

The implemented financial projection uses an explicit, operator-entered document CSV with all monetary components, source total, accrual timezone, actual movement timestamp, Ozon SKU, seller/register namespace, posting, original sale reference and document reference. It does not normalize unknown API payload money or claim full marketplace coverage. Empty authorized production responses cannot verify nonempty money schemas. Automatic marketplace P&L remains pending actual verified nonempty financial source contracts.

Manual lots have two identities: immutable namespace/document-line ID for natural deduplication and immutable request UUID receipt for safe exact replay. Historical layers never touch WB or physical stock. The result is before taxes and costs outside entered documents.

Returns use proportional original-sale allocated cost without pretending to know returned serial units. Any unknown original-sale cost makes the entire return unknown. Fully known cumulative returns restore the exact final cost residual. Returned pools keep remaining total cost and quantity to conserve a full resale. Returned FIFO age is the earliest original layer age, with availability only from the actual return timestamp. Receipts of equal timestamps use insertion order. Reports read source rows and immutable projection revision from one explicit SQLite transaction.

SQL rejects update/delete/replacement of event, lot, request, source-version and projection journals. Invalid CSV, changed operation IDs, stale preview, request replay mismatch and conflicting receipt IDs roll back atomically. Signed component totals must reconcile exactly before a result is presented.

API endpoints reuse the existing tenant middleware for role, origin/CSRF and demo restrictions; explicit writes additionally require owner/manager. UI uses the same protected headers and displays the manual-source boundary, incomplete cost and per-operation warnings.


## Verification evidence, 2026-09-30

- Builder full tenant suite from tenant-app directory: 415 passed and 8 subtests passed. An initial root-directory run failed only because a subprocess test could not import sync_lock; rerun from the intended directory passed.
- Builder full tenant API suite: 374 passed. Focused final Ozon DB/API: 34 passed after malformed Unicode validation hardening.
- Builder full React suite: 130 passed; focused Ozon React: 7 passed; TypeScript/Vite build succeeded. Existing bundle-size advisory remains.
- Independent reviewer reran 33 DB/accounting, 12 API and 7 React tests and accepted source; private SQLite WAL probe confirmed a concurrent backfill cannot mix old source rows with a new projection revision in one report.
- Graphify incremental source update: 5840 nodes, 15881 edges. No production deployment or visual authenticated browser acceptance performed by this builder.
