# Cash accounts, statements and transfers implementation plan

**Goal:** Complete FULL audit P2 cashflow beyond a single manual cash journal: explicit bank accounts/wallets, dated native-currency balances, reviewed statement import, plan settlement and internal transfers.

**Architecture:** Extend existing cash_entries with additive mapping tables rather than a competing monetary ledger. All changes are tenant-local SQLite, protected by existing verified tenant role/account headers. No WB accrual, procurement purchase or production release implicitly becomes cash. Historical rows migrate to an explicitly named legacy RUB-valuation wallet; their original amount/currency/rate and ids remain unchanged.

## Files and schema

- tenant-app/db/cash_accounts.py: schema, accounts, native/valuation money validation, per-account dated balances, linked transfers, statement preview/confirmation.
- tenant-app/db/cash_statement.py: strict bounded CSV parsing/canonical source identities, no database mutation.
- tenant-app/db/cashflow.py: narrow transactional helpers reused by manual entry/import/transfer, optional account assignment, per-account opening checks, grouped transfer status changes; existing API remains compatible.
- mh-redesign-lab/tenant-api/cash_accounts_routes.py: validated account/read/import-preview/import-confirm/transfer endpoints, owner/manager mutation; API/main router and production_permissions integration only.
- mh-redesign-lab/src/CashAccountsPanel.tsx and cash-accounts-source.ts: fixed currency account selector, balances/coverage, entry/opening/transfer forms, CSV template/upload/mapping/preview, explicit confirmation and retry handling. Mount inside existing cashflow panel; do not modify expense coverage work.
- New DB/API/UI regressions; existing cashflow tests and full suites remain gates.

Tables:
1. cash_accounts(id, name, currency, active, revision, legacy, created_at, actor), one fixed RUB/CNY/USD/EUR currency. Archive affects visibility, never historical balances. No currency rewrite after creation.
2. cash_account_entries(entry_id PRIMARY KEY REFERENCES cash_entries, account_id REFERENCES cash_accounts, native_minor, source_kind, external_key), UNIQUE(account_id,external_key). Legacy native_minor equals stored RUB valuation; explicitly disclose original currency is only the historic source for those migrated rows.
3. cash_transfers(id, request_key UNIQUE, fingerprint, from_entry_id UNIQUE, to_entry_id UNIQUE, created_at, actor). One transaction creates two linked legs, same plan/actual status/date; source/destination differ. Same-currency transfer requires equal native amounts. Cross-currency amounts and historical rates are explicit inputs; RUB valuation difference is reported separately, never interpreted as external revenue/expense or P&L.
4. cash_statement_imports(id, request_key UNIQUE, fingerprint, account_id, result_json, created_at, actor), immutable confirmation evidence. Statement external identities persist on cash_account_entries; cancelled imports cannot be silently recreated under the same bank identity.

## Invariants / self-review

- All entry/import/transfer writes use one BEGIN IMMEDIATE connection, including journal events, mappings and import receipt; any error rolls everything back.
- Amount validation Decimal, finite, bounded, native two decimals, exact currency matching for explicit accounts, RUB rate exactly1. Frozen historic rate per entry; no market-rate guess.
- One active opening anchor per account. Balance uses operations on/after that account's anchor, opening is balance immediately before its day. Missing anchors/earlier periods visibly preliminary. Plans before the anchor remain unpaid obligations.
- External inflow/outflow excludes transfer legs; account balances include them once. Both legs settle/cancel together; cannot settle one side or change immutable amounts.
- Preview produces normalized rows/errors/duplicates, never cash entries. Confirm requires exact preview fingerprint + explicit confirmed=true + bounded request UUID. Fingerprint covers account revision, source rows and selected plans' status/amount/date, so stale settlement fails closed.
- CSV canonical template: external_id;date;direction;amount;currency;exchange_rate;category;reference;plan_id. UTF-8 BOM allowed, explicit separator, <=1MiB and1000 rows, strict dates/number bounds, no formula evaluation. Every row requires external_id; distinct legitimate identical payments stay distinct, repeated identity is deduplicated. Changed content under one identity is conflict, not overwrite.
- Plan matching is an explicit selected plan_id; only active planned entry in selected account with matching direction/native amount/currency/rate may settle. Import records external identity on that same entry and changes status/date atomically; no second cash movement. Unmatched actual entry is inserted once. Different planned estimate must be explicitly resolved by the user, never silently rewritten.
- Retry uses same key for identical body; changed body rotates key. Actual server receipt returned on replay. Owner isolation is fixed tenant DB; no caller-provided DB/tenant paths. Viewer can read preview only; integration_reader denied.
- Immutable import/transfer evidence guarded against UPDATE, DELETE and INSERT OR REPLACE. Account archival does not remove ledger data. Native backup automatically contains all new tables and journal evidence.

## Steps and acceptance

- [ ] Schema migration twice preserves old ids/events/amounts; per-account currency and anchors validated; immutable journal SQL guards tested.
- [ ] Account + native balance backend and API; two accounts with separate dated anchors, foreign currency, missing anchor and period-before-anchor cases.
- [ ] Atomic transfer create/settle/cancel with explicit source and destination, replay/conflicting retry/concurrent race; total external turnover unaffected; no P&L writes.
- [ ] CSV parser/preview rows with invalid dates/NaN/formulas/oversize/duplicate/conflict; preview leaves row counts unchanged.
- [ ] Explicit confirmation links selected plan or inserts one actual row; repeat upload/request produces no duplicate, stale plan/account revision fails before any mutation.
- [ ] React account/opening/transfer/import workflow, visible account/date/currency/coverage, pending-body retry freeze, role enforcement and private data validation.
- [ ] Full tenant/API/frontend suites, build, graph refresh, diff/source gate; isolated native backup restoration of balances/import identities. Root owns exact-image deployment and live read acceptance.

No automatic bank connection, tax calculation or accounting policy. Generic CSV template is implemented; unsupported bank-specific formats require an explicit mapping/export, not inferred headers or guessed signs.
