# Automatic Ozon source: evidence-first bounded increment

Base21dfec4; scope only tenant-app Ozon client/db/tests. Existing manual CSV historical ledger, namespace, FIFO and immutable journals are preserved. No global frontend/shop dispatcher edits or deployment by this agent.

Primary verification: official documentation https://docs.ozon.ru/api/seller/#operation/GetFinanceAccrualByDay currently fails to load; official https://t.me/s/OzonSellerAPI?before=690 confirms June accrual_id rename, July container_fees/category changes and retired v3finance methods. Thirdparty scraped schemas are discovery leads only, not evidence to authorize monetary normalization. Earlier live by-day probes were200empty; no nonempty raw shape/unique operation key/commission sign/return linkage was proven.

Task1: strict bounded transport (no redirects, streamed byte ceiling, request/page/deadline limits, stable safe cursor types, no silent missingterminalpage); tests malformed JSON/oversize/cursor/type/timeout. Preserve monetary numeric precision while storing canonical raw snapshots.

Task2: capture per-requested-day collection provenance and explicit no-normalization quality. Do not label legacy raw snapshots source-complete; distinguish collected sources from verified financial interpretation. Empty API rows do not prove profit0 or completehistoricalCOGS. Failed orpartial collections never replace prior window. Tests period coverage/repeated sync/atomic rollback/empty andunknownschema.

Task3 conditional: ONLY if accessible official primary schema and actual sanitized nonempty shape corroborate field semantics, operation identity, exact component reconciliation, timestamp/SKU and sale/return linkage, implement isolated automatic namespace financial normalization with immutable dedup and historicalcost projection. Otherwise leave the blocker explicitly open; do not manufacture mapping from names or thirdparty guesses.

Acceptance: focused/full tenant tests; independent source review; exact commit+honest open automatic normalization gate. Root deployment after integration.


Evidence boundary (2026-09-30): authorized production read-only POST to by-day for2026-09-01 and2026-09-29 returned200 {accruals:[],last_id:""} both times. Structural probe retained no credentials/IDs/contact values; only two dates were queried. Official docs failed to load; official changelog establishes endpoint changes but not a complete monetary operation schema. Task3 remains blocked by missing primary field/sign/identity/linkage proof. This increment fixes bounded raw collection quality, not automated reconciled Ozon P&L.

Collection receipts contain only endpoint, SHA256 request/response fingerprints and response byte size. They establish a checked collector run, not marketplace authenticity or financial normalization. Existing snapshot occurrence retention and manual CSV historical accounting namespace are unchanged. Empty API lists render unknown net amount, not confirmed0; raw observed nonempty totals stay explicitly preliminary and profit staysnull.
