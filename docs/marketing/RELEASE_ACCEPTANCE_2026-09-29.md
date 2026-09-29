# Production release evidence — 2026-09-29

Runtime commit: `2472bdf83d508b8875012ff09a65ae60edf103d9`.
Tenant: `wb-tenant-mvvorokov` / https://mvvorokov.app.marketshelper.ru/.
Image: `mh-tenant:2472bdf`, immutable ID `sha256:eb5d5bdff30ae8affe24ecb8c6bf4638d921a62fce5c8b7143d80d1b3257aabb`.

Released: variant-aware WIP issue/packaging with exact nm_id/chrt_id FIFO; additive legacy 0/0 migration; source-key mismatch guards; saved BOM validation under SQLite write lock before debit; API/React/Streamlit selectors; CSV unknown-amount display. The coherent feature branch also carries store P&L, recurring store expenses and CSV import. Production-only growth outbox/sign-up/favicon changes were preserved.

Acceptance: 253 tenant tests plus 8 subtests; 258 API tests; 69 frontend tests; 5 deployment helper tests passed; frontend build passed (existing large bundle advisory). Initial runs failed because a temp parent was missing and edits were still in progress; final stable runs passed. Graphify AST update completed.

Native encrypted archive `marketshelper_manual_20260929_194422_900718.zip` was independently restored into a private temporary database. Candidate passed six API checks on the copy; original image passed five supported API checks after migration; integrity_check and foreign_key_check passed. Rehearsal had no production volume or network access and removed plaintext scratch data.

Fresh control-plane SQLite snapshot was encrypted and independently decrypted/integrity-checked: `/opt/wb-saas/backups/control-plane-encrypted/gateway-release-2472bdf-20260929.sqlite3.enc`. Deployment native backup: `marketshelper_manual_20260929_194808_288601.zip`.

Verified rollout retained `wb-tenant-mvvorokov-rollback-20260929-194807`. Live immutable image/revision confirmed, container healthy. Eight live JSON routes returned 200: healthz, today, production-workspace, store-expenses, store-expense-templates, management-pnl, reports, action-plan. HTML and two referenced frontend assets returned 200. Growth outbox module retained. A check for `/tmp/redesign-build-id` failed because the live tmpfs masks it; OCI image revision is the verified release identity. Authenticated manual visual acceptance was not performed.

Gateway admin return fix separately deployed as `mh-gateway:764c6a6`: unauthenticated `/admin` returns 303 to `/login?next=%2Fadmin`; owner login-return scenario passed in automated integration test. Public root returns 200.

Operational repair: empty `wb-tenant-data-marketing` root owner corrected 0:0 to 10001:10001, mode0755 unchanged, no recursive changes. Marketing container healthy, three business APIs and healthz returned200. Provisioner/new-volume ownership prevention remains a follow-up; no provisioning source fix included here.

This completes this release tranche, not the full product audit. Nested semifinished goods, broader Ozon, multi-shop, company cash-flow and other roadmap items require separate acceptance.
