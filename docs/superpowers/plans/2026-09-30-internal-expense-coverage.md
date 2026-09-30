# Internal expense coverage — P0-3

Parent reviewed and accepted this scope before implementation.

Add an append-only journal of explicit user attestations for exact SKU/article/report period. Five states: confirmed, confirmed zero, not applicable, estimated, unknown. An absent or stale entry means unknown; legacy expense rows never imply confirmation. Every entry has actor, UTC time, source/document, reason and replay UUID.

The source fingerprint includes effective allocated expense amounts for this SKU/period and the financial baseline used by the report. Changing expense inclusion/amount/period or underlying financial data invalidates confirmation. Nonzero included expenses prohibit zero/not-applicable. Excluded rows never contribute automatic coverage. Status changes do not add, replace or deduct money.

Implement: DB journal/schema; validated GET/POST API protected by existing origin/auth/role middleware; integrate coverage into management-economics while preserving prior response fields and monetary formula; accessible Settings panel to choose SKU/period, review all nine articles and inspect history. Read roles see state/history but cannot write; owner/manager may attest.

Verify: legacy/excluded rows, exact period scope, status semantics, zero contradiction, allocation fingerprints, inclusion/source changes, stale baseline, immutable journal, replay conflict/idempotence, transaction rollback, API write role/origin and frontend read-only/error flows. Run tenant/API/frontend suites and build, graph update, diff gate. Root deploys and performs normal authenticated live acceptance.

Limits: attestation means a user's documented assertion, not an automatic WB verification. Expense coverage is separate from FIFO and full-report completeness. No financial value is fabricated from status.

## Acceptance evidence

- Separate append-only `expense_article_attestations` table; UPDATE, DELETE and INSERT OR REPLACE collision guards. Existing expense and profit amounts are unchanged.
- Exact SKU/date scope, explicit source/document and reason, gateway account actor and UTC time; request UUID supports exact retry and rejects conflicting reuse.
- Preview fingerprints must match at save, and underlying row/inclusion/effective allocation or financial snapshot changes mark older assertions stale. Current DB rows are rechecked inside the insert transaction.
- Known nonzero WB commission/logistics/storage/acceptance and snapshot cost/advertising values block contradictory zero/not-applicable assertions. Presence is labelled observed evidence, never automatic completeness.
- Settings panel uses actual product choices, separate statuses, read-only role display, stale state and history. Scope edits hide the previous save form; edited payload rotates retry UUID.
- Full suites before the final small strict-integer/UI provenance change: tenant 392 passed plus 8 subtests; API 368 passed; React 127 passed. Final focused repeat: 32 DB/API/report tests and 3 React tests passed; TypeScript/Vite build passed. Graph: 5719 nodes, 15519 edges. Existing deprecation/bundle-size warnings remain.
- Independent source review requested. Production deployment and ordinary authenticated browser acceptance remain the root release task; this source commit does not prove them.

Boundary: this is documented article completeness for the selected SKU report, not a claim that all company expenses are allocated to that SKU, that all original WB data is received, or that FIFO is exact. Unknown and excluded legacy rows are never automatically turned into confirmed articles. Automatic bank reconciliation and multi-marketplace historical FIFO remain outside this change.
