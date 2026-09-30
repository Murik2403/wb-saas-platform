# Remaining audit boundaries and imported → shared monetary basis

Independent read-only reviewer proposal, 30 September 2026. Source inspected: root316daad and accepted CSV0b28a120. No runtime edits or production actions.

## What remains

1. Accepted CSV software now supports primary local FIFO openings and complete manual historical files. Integrated exact-image/native/browser/deploy gates are still required. Lack of owner documents means no actual historic backfill, not a software failure.
2. Ozon automatic nonempty monetary normalization is a software gap whose trustworthy adapter requires authoritative nonempty source/sign/identity/reconciliation evidence. Current manual ledger and bounded raw collector do not close it. Owner says such data is unavailable. Keep completeness false; do not invent API fixtures or sums.
3. Imported local lots cannot currently enter the shared warehouse. This is a genuine composability gap for a customer using both migration and multi-shop allocation. Original audit §5 requires switching/consolidation/shop accounting/rights; it does not explicitly require automatic adoption of every pre-existing local layer. Thus bounded CSV and multi-shop releases remain accepted, with this limitation visible. A follow-up transfer is appropriate software work, not merely owner-data approval.
4. One-document/one-financial-row mapping, lack of automatic batch reconciliation or destructive rebinding are explicit limits. They do not independently invalidate the original multi-shop acceptance. Any correction workflow must be separately specified before widening claims.
5. Secondary read latency is evidenced Windows cold imports + serialized fanout; it is a performance follow-up, not missing data normalization. Do not relax the one-worker memory/security guard to hide it.
6. Current public/drawer focus regressions are concrete software fixes under separate review. Native runtime/browser/live acceptance is needed after integration.
7. Remaining actual real case permission/documents, real interviews, customer cohort, category-specific scanner workflow, real equipment data and historical expense attestations are owner/research gates. Prepared drafts/tests do not create those facts. Final video render is a deliverable/approval gate, not an accounting change. Ads belong to Claude.

## Why deleting central_setup_guard is unsafe

`finished_history_import.physical_amount` values each location from immutable opening cents plus active local allocations. `shared_warehouse._preview` and shared manufacturing use immutable `warehouse_ready_basis`, reservations/ownership and manufacturing events. A local remainder cannot safely be inferred as remaining quantity × average layer rate, nor by resetting original units/amount. Dual writers would allow independent consumption/reversal and duplicate ownership. Existing imported layer/archive/financial fingerprints must stay unchanged.

## Bounded first transfer: ready-only SKU, explicit preview/confirm

This is an explicit change of authority, not another stock receipt, sale, cash payment or historical COGS document.

- Primary owner only, current canonical account and seller identity checked before preview/confirm/replay; current role/origin/demo protections. No automatic grants or inferred seller.
- Adopt an entire canonical SKU and its exact variants together because current managed guard applies to the whole card. First tranche permits only imported layers with ready stock; any inbound/WB stock, active incident hold, dispatch, shared mapping/reservation/ownership, or unresolved legacy sale-return/reversal dependency fails with an actionable reason. Future non-imported layers of that managed SKU continue through the normal central allocator.
- Read-only preview builds per-layer ready units and integer cents using the accepted imported residual function, catalog proof, seller, cutover, current local allocation revision/hash and immutable archive IDs. Quantities/cents and no active dependency checks share one read snapshot. Unknown cost/provenance never falls back to the current cost table.
- Confirmation under one BEGIN IMMEDIATE repeats the full fingerprint/ACL/identity checks. An immutable transfer receipt stores UUID, actor, reason/source proof, SKU/variants, source layer/location quantities and cents, old authority and transfer timestamp, before/after hashes. No mutation of existing imported original units/rates/archive/source fingerprints; no physical quantity or money movement.
- Persist the actual ready residual as the initial central ready basis per layer, never `portion(original_amount, ready_units, original_units)`. Register the central item and this basis/authority marker in the same transaction. Layer/receipt/basis collision and DELETE/UPDATE/REPLACE guards are required.
- After transfer, only the central allocator may change physical quantities. Local movement/reversal/automatic FIFO writers must explicitly refuse authority-transferred layers. Existing historical archived costs remain readable/manual-preliminary, and current financial rows never acquire verified seller lineage merely from transfer.
- Refactor valuation to a single authority-aware per-location resolver: pre-transfer local residual, post-transfer central residual. Do not add central costs on top of existing local allocations. The resolver must preserve cents across reservation/release, shared manufacturing, sale/matched return/quarantine and reports. Local historical allocations remain immutable provenance but cannot become a second active pool.
- A rollback is not deletion. Before any dependent central event an explicit reversal document may atomically remove management authority only if quantities/cents still equal the transfer snapshot and no holds/reservations exist; otherwise fail and require resolving dependencies. Database rollback after later business events requires verified backup restore preserving those events.

## Later tranche: inbound/WB and historic reversals

Do not include it by stealth in ready-only adoption. Need a separate protocol proving each original dispatch/receipt and exact primary seller ownership, native per-location cent pools, matched historical return links and the fate of outstanding reversals. Existing imported layer at WB cannot be attributed to a secondary seller merely because nm_id is equal. No synthetic shipment/receipt sequence to manufacture proof.

## Required acceptance

- Opening3 @0.335 =1.01; local consume2 gives .68 and ready remainder .33; transfer exactly .33 without new stock/expense/revenue. Central subsequent reserve/sale/return preserves the same cents and original source IDs. If active reversible local consume disqualifies this first tranche, test explicit denial and a resolved/non-reversible supported path separately; do not claim it supported.
- Two variants/shops, exact mapping, stale preview, seller/role/ACL revocation, duplicate UUID, changed payload, alternate-key REPLACE, transaction failure and two simultaneous transfers.
- Transfer neither changes existing financial COGS nor creates financial/cash/sales events. Supply/quarantine/production availability use one pool only.
- Ordinary non-imported local/central paths retain behavior. All read snapshots remain coherent under concurrent central writes.
- Actual immutable candidate native rehearsal plus live UI/API and backup/rollback acceptance before release; source-only tests do not close the follow-up.

The initial architecture above is the accepted proposal; the concrete implemented first tranche below is narrower on reversal. Whole-audit completion is not claimed.

## Concrete first implementation

- New `tenant-app/imported_shared_basis.py` owns owner/seller preconditions, preview, atomic confirm; `db/imported_shared_basis.py` owns append-only per-SKU/per-layer authority facts and central per-location cent resolver. Reuse setup receipts UUID namespace. No receipt/sale/cash/source event created.
- Register all current exact catalog variants under one BEGIN, and freeze all imported layers (including exhausted archive slots). Reject any active legacy allocation, any non-imported layer, any inbound/WB/hold/mapping/reservation/ownership or unavailable original source; return actionable reason. Active movement reversal dependencies block this tranche. Zero remaining variants preserved; SKU must have positive ready stock.
- Confirmation inserts authority marker before register_item guard, but only after full fingerprint check and within uncommitted TX; inserts exact ready basis and immutable receipt. Marker collision rejects a second authority transition.
- Authority switch is one-way in this tranche; no destructive rollback/deletion. An optional explicit reverse authority document is a separate contract, not removal of immutable managed items. Backup restoration required after later events.
- Reader uses local residual before marker; after marker uses central ready basis minus allocated reservations and net manufacturing, inbound/WB ownership exact amount. Held amounts remain subsets; reserved-ready cost included exactly once. Frozen opening fields unchanged.
- Existing typed shared manufacture reversal remains supported; legacy local move/dispatch/return/reversal explicitly deny transferred layers. No generic bypass flag.
- API POST `/api/v1/shared-warehouse/imported-basis/preview` `{nm_id,reason}` and `/confirm` `{nm_id,reason,fingerprint}` +X-MH-Request-Key UUID. Owner account canonical positive <=int64, selectedprimary0 only, freshseller before every replay. Registry and frontend actualroutes fixture updated together.
- UI dedicated component in existing shared region: primaryowner product selector, reason, explicit readonly preview units/cents/variants/source and confirmation; networkretry sameUUID, inputchanges newUUID; no automatic grants or WB verification.
- Tests transfer lifecycle2shops, .34/.34/.33, manufacture/reverse source, immutable/altkey guards, sameUUIDdifferentinput/stale/catalog/source/role/seller/concurrency/rollback, localwriter denial and coherent reader snapshot; fulltenant/API/UI/build +independentreview +graphupdate. Native/live root gates separate.

## Source acceptance 2026-09-30

- Fresh full gates: 611 tenant tests +8 subtests passed, 1 existing Linux-only test skipped on Windows; 410 API tests; 216 frontend tests and production build passed. Source packaging/route gate and diff whitespace checks passed. Graphify rebuilt code graph (7905 nodes /20512 edges).
- Independent review: 19 new core tests, 2 actual API tests, 3 new UI tests accepted. Reviews cover exact money, full variants, two-shop allocation, current owner/seller replay, immutable history, rollback and explicit unsupported dependencies.
- `scripts/imported_shared_basis_native_acceptance.py` local-source smoke passed against actual ASGI APIs: CSV preview/confirm → read-only authority preview → UUID confirmation/replay → mapping → .34/.34/.33 reservations → dispatch/arrival/sale/matched return; initial units/202 cents preserved, no financial rows created. Synthetic seller binding is a fixture, not WB verification; no external requests. Current WB snapshot is deliberately absent, so physical per-location layer value is tested separately from marketplace snapshot valuation.
- Root must run this helper in the exact immutable Linux candidate with `--network none`, `--memory 1536m`, fixed `--expected-build` and mounted private output, then finish browser/backup/release acceptance. No live data or deployment was performed by this stage.
- Active local movement/reversal dependencies, inbound/WB imported authority, archive-only post-cutover returns and authority reversal remain explicitly unsupported. The UI explains ready-only one-way transfer; existing archived financial facts remain manual-preliminary.
