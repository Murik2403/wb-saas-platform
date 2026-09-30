# Release acceptance — ef616ed (30 September 2026)

This tenant tranche is deployed. The complete product/design audit remains open.

## Exact release and recovery

- Source: `ef616ed1712df28fd001f04a76b343eac386844b`.
- Tenant image: `sha256:8ae1f0d9bd63a129ad6aee8bbf5d823742643d269876dc766409520180239115`, tag `mh-tenant:ef616ed`.
- Deployment backup: `marketshelper_manual_20260930_070652_694574.zip`; retained rollback: `wb-tenant-mvvorokov-rollback-20260930-070652`.
- Provisioner remains source `0eb4746d06dcc9356ca95b26a7096efb014495c6`, exact image `sha256:99fd4efe198af2b757ff3c2aa407e898bb349ea842ea3a1450cfb77aac4bab72`. Its default tenant image is pinned to the exact ef616ed image; demo configuration was preserved.
- Provisioner backup: `gateway-provisioner-0eb4746d06dc-1790752112886976176.sqlite3.enc`; retained rollback: `wb-saas-provisioner-1-rollback-1790752113891640586`.

## Changes and verification

Deferred Streamlit/Plotly and PDF/chart imports remove display-runtime loading from JSON startup. The metric catalog preserves all 21 labels; actual PDF generation still uses the original builder. Warehouse document/status captions are translated, and disabled schedules explicitly have no next attempt assigned.

- Tenant/reports: 673 passed, 8 subtests passed, 1 platform-specific skip. API: 403 passed. Frontend: 205 passed across 35 files; build passed (existing large-chunk warning remains).
- Independent import/PDF review: 32 tests passed. The fresh-process regression also runs actual schema startup while rejecting Streamlit, Matplotlib and Plotly imports. Source packaging gate: 25 imports; clean gate 18/18, zero tracked sensitive files; packaging regression 8 passed.
- Exact Linux image: actual scheduler crash/retained child lock, two actual PDFs, restored encrypted backup, due-run idempotency and revoked-owner checks passed. All ten recorded scheduler/API source hashes match committed Git blobs (local CRLF working copies are not the canonical archive).
- Exact-image composed business acceptance passed: material units, immutable BOM cost, sale/return COGS, CSV expense deduplication, P&L and single cash payment. Shared warehouse concurrency, cents conservation, quarantine, document scopes and financial replay passed. These are explicitly synthetic, not evidence of actual WB verification.
- Encrypted restore rehearsal: `marketshelper_manual_20260930_070235_513378.zip`; candidate and previous-image API passed. Peak observed 455,569,408 bytes under a 1,610,612,736-byte limit, no OOM/restarts; synthetic cash/Ozon writes were restored in the isolated copy before rollback testing.
- Deployment returned `DEPLOY_VERIFIED`; independent live verifier returned `LIVE_API_17_HTML_ASSETS_OK` and exact revision/health success.
- Actual owner browser loaded `/assets/index-tys-lgyo.js` and retained the preliminary WB result of 986,738 RUB. No owner cost attestations or synthetic ledger facts were written to production.

## Browser evidence and limits

Fresh detached ef616ed source plus exact-image assets were served at loopback port 5193 with fresh synthetic data and external-network denial. Finance loaded automatically, displaying a preliminary 180 RUB for this deliberately unmapped fixture. Its read diagnostic measured finance 8.250 seconds and overview 11.813 seconds, compared with a previous synthetic browser run's finance 36.329 seconds. These are individual local observations, not a production latency guarantee; sequential scoped request isolation remains in place. The rendered settings showed both schedules disabled, no next attempt and no previous jobs.

The frozen receipt and timings are retained outside Git in `audit-private-deliverables-20260930/two-shop-browser-current.json` and its declared diagnostics path. Native scheduler receipt is `release-rehearsal-tools/PROOF_schedule_native_ef616ed_20260930.json`.

Server capacity remains limited. An inspected unused intermediate 9e407d5 image and unused build cache were removed; production volumes, backups and retained rollback images were preserved.

## Still open

Finished-lot/historical CSV implementation and its exact-image/browser release gates; public Gateway contrast/focus regression and authentication error semantics; tenant dialog keyboard behavior; automatic Ozon monetary normalization on a supported nonempty source; genuine case/video/interviews and conditional category workflows. This receipt closes only the explicitly verified tenant tranche.
