# Claude audit-fixes integration and production acceptance

Merged `claude/audit-fixes` a8d07ea into current `codex/cabinet-ux-20260930`, producing committed runtime revision `0a89f63132ba6d4b76c46477370f9f6a8052af4b`. One conflict in authorization regressions was resolved by retaining both sets of tests; current scoped-purge, financial lifecycle, PWA and control-network fixes remain present. The foreign untracked TrueStats review was left untouched.

CI remains Node24 because all current frontend Dockerfiles use node:24-alpine. Vitest runs once through npm test, avoiding the duplicate new job step. Script tests and Docker ignore names are integrated; contour removed.

## Verification

- Frontend582passed; TypeScript and production build passed.
- Tenant API658passed.
- Tenant-app1047passed,1skipped,10subtests passed.
- Provisioner83passed, including a repeat from the exported committed tree.
- Scripts/standalone46passed.
- Gateway runtime source unchanged; no gateway image rebuild or billing execution in this release. Existing financial gateway4926f48 preserved.

## Deployed runtime

demo, mvvorokov and marketing: `mh-tenant:audit-0a89f63`, Engine image ID `sha256:91988913374bdca54e0ba664ecbeb4f23ea8a7e14c3024652ab7fc4984a1de40`.

Provisioner: `mh-provisioner:audit-0a89f63`, Engine image ID `sha256:89ea889eeabbc797c873fb63735be128d8a8d4880c20dcdcc75293cbb181272f`.

Tenant builds package API+frontend together. Exact five changed runtime source hashes and build-id match the committed archive on all three live tenants; public health endpoints return ok. Encrypted backup, independent SQLite restore, candidate and rollback rehearsals preceded each tenant swap. Mounts, aliases,1536MiB limits and retained encryption keys preserved. Only tenant build-id environment changes. Provisioner preserves control isolation/alias256MiB/auth credential and pins future tenants to the same new image; stopped rollback detached from active control networks.

Backup host cron now references committed0a89f63 scripts, retaining schedule, recipient and14day policy. All other scheduler crypto/offsite/alert dependencies hash-identical to the prior frozen release. Client timeout900s is active; freshness check fresh=true/alert=not_needed. Provider tar-helper timeout remains a separate240s bound;900s is the HTTP client read timeout, not an unbounded background wait.

Live role checks: demo session user.role viewer; signed manager taxPUT/WBconnect/Telegramlink/backupGET403. Manager product/price/production allowance remains verified in isolated tests, not with real marketplace writes. Tax inputs disabled for other roles; price changes>70% require explicit opt-in. Stale price operation changes to unknown_outcome after600s, not a successful result or automatic retry.

Browser390px: production compact grid one353.875px column; supply dialog left16/right358.67 fits390px viewport. No dialog submission. UI screenshots/proofs outside Git at `deliveries/audit-p0-20261007/merge-0a89f63/`; matching server release `/opt/wb-saas/releases/merge-0a89f63/`.

This integration closes this branch's feasible release work, not the entire audit. Remaining external/mobile/legal/capacity items remain in `AUDIT_DEPLOYMENT_STATUS_2026-10-07.md`; historically exposed bot-token rotation still requires owner confirmation.
