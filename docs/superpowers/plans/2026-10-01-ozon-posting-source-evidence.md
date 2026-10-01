# Ozon financial posting rows: bounded source evidence (2026-10-01)

This addition collects `/v1/finance/accrual/postings` during the existing explicit Ozon sync and exposes a read-only table in the Ozon workspace. It does not normalize POSTING revenue, commission, delivery, or returns and does not write documentary movements.

## Primary contract read

[Official Seller API postings schema](https://docs.ozon.ru/api/seller/#operation/GetFinanceAccrualPostings), freshly expanded in the root agent's browser on 2026-10-01, documents `posting_numbers` as 1..200 strings; response `posting_accruals[].posting_number` and `accruals[]` with `accrual_date` (YYYY-MM-DD), `accrued` (money object), `quantity` (nullable int32), `seller_price` (nullable money object), `sku` (nullable int64), and `type_id` (int32, linked to accrual types). Money examples contain `amount` and `currency`. The rendered example contains placeholders, not a reconciliation fixture. Exact rendered proof is retained privately at `audit-private-deliverables-20260930/ozon-official-postings-contract-20261001.txt` outside this checkout.

[Official migration announcement](https://dev.ozon.ru/news/783-Izmeneniia-v-metodakh-dlia-raboty-s-finansovymi-otchetami-v-Seller-API/), published 2026-08-24 and freshly read in that browser, states retirement of the legacy v3 financial transaction methods on 2026-09-08, recommends the v1 accrual methods and sequential requests, and warns that results can differ from the cabinet. We do not restore the retired methods.

## Implemented boundary

Only explicit `posting_number` values from the loaded FBO/FBS order lists are requested, in sequential batches of at most 200. The existing request count, deadline, streamed response byte, page, and row budgets apply. Unrequested posting groups and malformed group/row containers abort the sync without replacing a snapshot. Repeated request references are collapsed, but repeated response occurrences remain in the immutable raw snapshot. The table shows up to 100 observations with exact RUB decimal strings; null or invalid quantity remains unknown rather than zero. Type IDs are observations and never imply a sale or return.

The financial endpoint has no date filter: the selected dates scope the order lists, not the accrual dates returned for those references. The workspace shows requested and missing group counts and explicitly disclaims full financial coverage. A new snapshot containing changed rows has a new source hash and occurrence keys. This preserves evidence versions; it does **not** prove business-operation identity, idempotency of financial events, or how to apply corrections to a ledger. Old snapshots without this source remain visibly uncollected.

## Remaining normalizer gate

The freshly read contract does not specify the signed POSTING component conservation formula, stable per-accrual operation identity, correction/supersession relationship, or linkage of nullable financial quantities to physical returns/FIFO. There is no authoritative evidence that `unit_number` from by-day is a `posting_number`; the implementation does not join them. No real nonempty client financial source is available for business parity. Consequently financial normalization, movement verification, return linkage, source completeness and ledger confirmation are not promoted by this change. Existing manual documentary Ozon FIFO is independent.

## Validation

Backend test was red before the evidence module existed; the frontend display test was red before the source table existed. Final targeted backend suite: 84 passed. Frontend Ozon workspace suite: 12 passed. Tests cover sequential 201-reference batching, malformed/unrequested responses, repeated occurrences, missing and nullable quantities, empty response/empty request, row limits, exact signed money, changed source versions, immutable persisted raw versions and unchanged documentary/WB ledger state. No external API requests or production writes were used in these tests.
