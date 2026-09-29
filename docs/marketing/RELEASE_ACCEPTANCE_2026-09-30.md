# Audit release acceptance — 2026-09-30

## Deployed product change

Runtime source revision: `e7c39ff049fed38d865d7f82af07c999a4706ba6`.
Tenant: `wb-tenant-mvvorokov`, https://mvvorokov.app.marketshelper.ru/?view=settings.
Image: `mh-tenant:e7c39ff`, immutable ID `sha256:040acf64540e8ce41d477c0cceea08d9fcd78e7c16455fa802a2ac9b51d3b4b4`.
Default image tag for newly provisioned tenants `wb-dashboard-tenant:redesign` now points to this verified image. Existing demo/marketing containers were not replaced by this product release.

Settings now contains “История норм и себестоимости”: before/after values, date, WB article, event source and actor when available, filters and pagination. History begins at rollout; no old events are fabricated. Manual cost components, forecast application, legacy norms and actual tracked changes during forecast refresh use one transaction for state and history. Pure forecast/timestamp updates do not create events. Journal UPDATE/DELETE/REPLACE is rejected. Confirmed zero packaging cost remains zero during forecast refresh; unknown zero retains the existing default logic.

## Verification

- Independent read-only review accepted final source; no remaining blocking issue.
- Fresh root verification: tenant 270 passed + 8 subtests; API 270 passed; frontend 76 passed; production build passed. Total 616 tests. Existing dependency deprecations and bundle-size warning remain.
- One initial root API run could not create a nested temporary test directory (116 passed, 154 setup errors); after creating its parent directory the full rerun passed 270/270. No application fix was needed for that environment failure.
- Code graph updated: 6007 nodes / 14522 edges; source diff check passed.
- Native encrypted backup `marketshelper_manual_20260929_225933_577615.zip` independently restored to an isolated copy. New version passed seven candidate API checks, including history; previous live image passed five API checks after the new schema migration on the same copied database. No live restore or synthetic customer writes.
- Fresh deployment backup `marketshelper_manual_20260929_230106_895477.zip` passed encrypted inspection before switch.
- Encrypted control-plane backup `/opt/wb-saas/backups/control-plane-encrypted/gateway-tenant-e7c39ff-20260930.sqlite3.enc` independently decrypted and passed SQLite integrity check.
- Live rollout passed its four mandatory probes. Extended checks then passed nine JSON APIs, HTML and referenced JS/CSS assets. Container healthy; Docker image identity and full OCI revision confirmed.
- Rollback retained: `wb-tenant-mvvorokov-rollback-20260929-230106`.
- Authenticated manual visual acceptance has not been performed; automated UI tests and live asset/API checks are distinct evidence.

## Provisioner correction deployed

Revision `0f495b8a9f1018fe8bf6fae8f8a5e5fefeae65d9`; image `mh-provisioner:0f495b8`, ID `sha256:4ad98aac2b5e95528b64a65462cdb41cfa4dc387f141d1e49b0955f40a634069`.
Empty newly created tenant volume roots receive runtime UID/GID 10001 before start. Populated roots and files are preserved; inaccessible populated volumes fail before tenant launch. Disposable Docker-volume rehearsal passed initialization, reuse and wrong-owner preservation. Forty-seven provisioner tests passed. Live health, tenant health and Docker proxy access passed, with original environment, mounts and networks preserved.
Rollback: `wb-saas-provisioner-1-rollback-20260929-224901`. Encrypted control-plane snapshot independently restored before this change. Existing marketing tenant empty-volume ownership was repaired separately; marketing and demo are healthy.

## Status corrections and remaining audit work

Live `www` canonical redirect already works (HTTPS www → apex 301 preserving path/query); earlier roadmap entries describing it as missing are stale. Gateway admin return fix remains deployed in `mh-gateway:764c6a6`.

This release closes the detailed manual cost/norm history gap, not the entire product roadmap. Nested semi-finished products, full Ozon accounting, multiple shops and company cash-flow require separate implementation and acceptance. Historical P&L continues to mark estimated/incomplete cost coverage; the new settings journal does not invent historical FIFO data.
