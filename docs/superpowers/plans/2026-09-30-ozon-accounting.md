# Ozon accounting implementation — 2026-09-30

Build an isolated Ozon raw ledger, source snapshots and explicit SKU cost mapping in the current tenant, preserving WB tables and inventory. Use only existing Seller API credentials for reads. Confirmed primary endpoints: /v3/product/list (result.items,last_id), /v4/product/info/stocks (items,cursor), /v4/posting/fbs/list and /v3/posting/fbo/list (postings,cursor,has_next), /v1/returns/list (returns,has_next; request last_id), /v1/finance/accrual/types, /v1/finance/accrual/by-day (accruals,last_id; one date per request).

- Bound date range, pages, rows and request timeout; fail closed on repeated/missing cursors or unknown response shapes.
- Download all collections before writing. Upsert a full source snapshot and finance events in one transaction, using stable source keys; no partial finance success flag.
- Preserve accrual_id, category, authoritative total_amount and typed raw payload. Decimal parsing; currency checks; all fee IDs must be in the downloaded primary dictionary. Unknown shapes/types/currencies block complete accounting.
- Do not sum component fees twice or manufacture gross sales from total_amount. Nonempty actual POSTING examples do not reconcile with naïve sum of sale_amount+sale_commission+delivery. Show authoritative net accrual separately; component interpretation remains explicitly preliminary.
- Cost mappings are explicit by Ozon SKU: manual unit cost or mapped WB product current estimate. Never silently reuse WB FIFO sale allocations for Ozon. Exact historical FIFO requires an actual Ozon-specific movement allocation; absent it show estimate/unknown coverage.
- Owner/manager can synchronize or save cost mappings; all authenticated readers can view. No marketplace writes.
- Provide date controls, sync status, source coverage, products/stocks/returns and editable SKU cost mapping in Finance. Avoid claiming complete P&L while component reconciliation is unverified.
- Verify isolated DB/API/UI tests plus full suites/build and graph update; commit explicitly. Root deploys integrated revision after backup rehearsal.

## Identity correction after independent review
Primary read-only API checks on three real dates returned 2/3/5 rows, without repeated accrual_id; these IDs were not all in the fee dictionary. This bounded sample does not establish global event uniqueness. Completed day windows retain every source row, including identical occurrences, using day + canonical payload SHA256 + occurrence ordinal. Reimport replaces the date range transactionally. Identical occurrences block complete-accounting claims rather than being silently deleted. Invalid rows leave the previous snapshot untouched. Cross-period snapshots retain their explicit synchronization timestamp.
