# Ozon automatic money normalization: evidence gate, 2026-10-01

Status: **open, external contract evidence missing**. This document does not close automatic Ozon monetary normalization. No runtime changes or deployment are implied.

Base examined: `93714b5` (committed cabinet release). Scope: `tenant-app/ozon_accounting_client.py`, `tenant-app/db/ozon_accounting.py`, `tenant-app/db/ozon_pnl.py`, their tests and API boundary. The owner has confirmed that no nonempty real Ozon financial export is available. No additional production API queries or credentials were used for this investigation.

## Fresh primary-source check

- [Official Seller API documentation](https://docs.ozon.ru/api/seller/#operation/GetFinanceAccrualByDay): retrieval failed with a redirect loop on 2026-10-01. A full current financial response schema was therefore not inspected.
- [Official Ozon Seller API notifications](https://t.me/s/OzonSellerAPI?before=690): accessible. The 30 July 2026 notice identifies changes to request date/cursor semantics and response categories/container fees. A notification is not a complete monetary schema.
- The same official channel announces retirement of `/v3/finance/transaction/list` and `/v3/finance/transaction/totals` on 8 September 2026. Those older endpoints are not a suitable fallback contract for a new implementation.
- [Official migration article](https://dev.ozon.ru/news/783-Izmeneniia-v-metodakh-dlia-raboty-s-finansovymi-otchetami-v-Seller-API/): retrieval also failed with a redirect loop. Search results for third-party schema mirrors were discovery leads only and were not used to authorize monetary interpretation.

These sources support endpoint freshness checks; they do **not** establish signed-component reconciliation, unique operation identity, movement date, allocation to individual SKU, or return-to-original-sale linkage.

## What the committed implementation can safely do now

| Path | Current supported result | Evidence boundary |
| --- | --- | --- |
| Bounded API collection | Immutable raw source versions and collection receipts; terminal-page/day checks | `source_collection_complete` means collection checks, not correct monetary interpretation |
| Raw workspace | Observed signed RUB totals and explicitly preliminary component/cost display | `source_complete=false`, `financial_normalization_verified=false`, `accounting_complete=false`, `profit_rub=null` remain mandatory |
| Manual document CSV | Preview with fingerprint, explicit confirmation, stable namespace/event identity, duplicate/conflict checks, Decimal component-to-total reconciliation | This is the operator-entered document contract, not an automatically verified Ozon API interpretation |
| Manual historical cost lots | Versioned FIFO allocations, documented movement timestamps, original-sale return reference, incomplete-cost marking | No physical stock mutation; no implicit shared WB/Ozon identity; no claim that the operator supplied all marketplace documents |

An empty response is not proof of zero profit or complete sales coverage. Identical raw rows are retained because operation identity uniqueness is unproven; a raw `accrual_id` alone must not be used to silently discard occurrences.

## Why an automatic draft cannot yet produce monetary postings

Changing a badge to “draft” does not resolve uncertain arithmetic. A draft adapter that guesses field signs, adds nested fees twice, allocates an order total to every SKU, or infers stock movement from accrual date would still provide misleading sums and could contaminate the document ledger after confirmation.

The safe existing draft is the raw inspection workspace. The safe existing confirmed money path is the explicitly manual document CSV. Do not connect raw payloads to that ledger until the mapping below is supported. Preserve these independent namespaces and existing manual FIFO projections.

## Exact evidence required before implementation

1. Current official by-day/types schema or an official support-provided contract, including field units, currency, sign, optionality, cursor/day semantics and category rules. Schema access alone must not be described as successful validation on live account data.
2. Sanitized nonempty examples with authoritative totals: single-SKU and multi-SKU sale, standalone fee, return/refund and compensation, plus container-category examples if returned. Keep structure, signed numbers and cross-references; remove credentials, contact details and personal data.
3. Proven identity scope and correction/version semantics: seller + operation + line identity, repeated page boundaries, identical legitimate rows and changed revisions. Do not equate repeated IDs with duplicate events without this proof.
4. Proven per-SKU quantity and value allocation and original-sale linkage for returns; separate financial accrual time from physical movement time. Missing allocation/linkage stays unknown rather than being manufactured from posting number.
5. Reconciliation fixtures for every supported category and unknown-category rejection; a component must contribute once, and its Decimal sum must match the authoritative signed source total. Reject unsupported currencies; do not silently convert them or sum mixed-currency data as RUB.
6. Only then implement isolated read-only preview, explicit immutable confirmation, idempotency/conflict rejection and incomplete coverage flags; run regression tests proving manual documents/lots and WB stock remain unchanged.

## Verification on this base

`tenant-app/tests/test_ozon_accounting.py` and `tenant-app/tests/test_ozon_pnl.py`: **51 passed** on 2026-10-01 in an isolated test database.

Those tests cover bounded pagination/raw provenance, repeated rows and IDs, malformed or unknown money, incomplete flags, manual preview/confirmation/dedup, historical lots and returns. They use synthetic fixtures and therefore **do not verify the current nonempty Ozon API money contract**.

Acceptance decision: retain the existing safe collection/manual paths, leave automatic normalized Ozon money explicitly open, and do not deploy an invented mapping. This evidence-only increment requires no runtime release.
