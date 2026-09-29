# Company cash-flow implementation plan

**Goal:** Separate actual money movements and dated planned payments from accrual P&L.
**Architecture:** Dedicated SQLite ledger with integer kopecks, immutable input snapshots and status events. API uses existing tenant write-role and origin middleware; Finance embeds an independent accessible panel. No bank integration or implicit conversion of sales/P&L into cash.
**Scope:** Manual opening balance, inflows and outflows, exact currency conversion, actual/planned reconciliation and cancellation history. Procurement payments are explicitly linked by the user via references; no automatic duplication of existing payments or expenses. Opening balance means balance immediately before its date; one active opening anchor is allowed. Forecast includes overdue plans at the period start and dated upcoming plans.

- [x] Implement ledger schema, transactional idempotent writes, status events and dated roll-forward.
- [x] Add validated API and read/write-role coverage.
- [x] Add Finance panel with dates, currency, source references, opening anchor, actual confirmation and cancellation.
- [x] Verify ledger arithmetic, duplicate writes, timing, cancellation/history, rollback, API permissions and UI; build frontend.
- [ ] Commit exact source; integration and production release are root-agent responsibilities.

## Acceptance evidence

- Complete tenant suite: 286 passed, 8 subtests passed (six existing dependency/lifespan warnings).
- Complete tenant API suite: 284 passed (two dependency warnings); final modified cashflow routes rechecked: 14 passed.
- Complete React suite: 82 passed; TypeScript and Vite production build passed. Existing 500 KB bundle advisory remains.
- Graphify updated: 6074 nodes, 14697 edges; generated outputs ignored.
- New ledger regressions include CNY × 13 conversion, exact cents, period timing, opening anchor, overdue obligation retained across opening date, concurrent retry → one entry/event, immutable event log, transaction rollback on event failure, confirmed-date conflict, canceled entry retained, accrual sales never counted as cash.

## Contract and limits

`GET /api/v1/cashflow?start=YYYY-MM-DD&end=YYYY-MM-DD`, `POST /api/v1/cashflow/entries`, `PATCH /api/v1/cashflow/entries/{id}`. Existing middleware permits writes only to owner/manager and checks write origin. Inputs reject unknown fields, nonfinite amounts, unsupported currencies, nonpositive FX and sub-kopeck movements. RUB requires rate 1. Positive foreign amount × entered exchange rate is rounded once to integer kopecks with decimal half-up.

Plans become actual with an explicit settlement date and retain their original date/snapshot. Confirmed amounts cannot silently change: cancel the old record and create its replacement. Canceling retains the row and an immutable event. One active opening anchor is permitted; negative balance is entered as outflow. Earlier actual movements are already included in the anchor and excluded from a second addition. Earlier unpaid plans remain due. A window before the anchor is marked invalid and the UI hides balances.

The ledger covers entered operations only: no bank feed, no automatic WB payout or procurement import, no P&L expense mutation. Procurement references are user-entered provenance, not verified foreign keys. Cash amounts use the rate supplied when entering the record; a changed settlement amount/rate requires cancellation and replacement. No production deployment or authenticated visual review was performed by this subagent.
