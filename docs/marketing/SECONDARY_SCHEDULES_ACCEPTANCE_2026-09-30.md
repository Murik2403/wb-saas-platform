# Secondary sync / local PDF schedules: source acceptance

Base: `1d1169afcd2540a398fc9792fcbc443859f92677`.

Implemented owner opt-in schedules, disabled when absent; explicit ACL is required
to enable and for every delegated run. A current owner can disable after ACL
revocation without granting any access. Runtime account is the exact canonical
Gateway account ID. Fresh current owner/manager membership is checked before and
after private preparation; the transaction rechecks freshness, lease, generation,
seller, encrypted credential digest, existing manager ACL and snapshot revision.

Sync commits only a captured successful complete ingestion. Failed/partial jobs
retain the previous snapshot. PDF generation uses the persisted due UTC date and
period and archives bounded hashed bytes in the selected shop snapshot. No external
delivery occurs; private report definitions are disabled and delivery flags false.

One due job per tick. A 660-second lease and deterministic UUID derived from
schedule/generation/due slot survive restart. Successful snapshot, bridge receipt
and attempt event commit atomically. Failures retry after 5/10 minutes, at most
three completed attempts before advancing the due slot. Lost lease/reconfiguration
cannot overwrite current state. Attempt/configuration receipts have SQL update,
delete and replace protection.

GET `/api/v1/shop-schedules/{shop_id}` returns disabled defaults, current owner
`can_configure`, explicit-ACL `can_enable`, schedule status/history and PDF run/hash.
PUT `/{shop_id}/{kind}` requires current owner, UUID and optimistic generation.
URL shop must match the explicitly selected request context. No actor/role/email
delivery fields are accepted. Sync interval 15–1440 minutes, PDF 60–10080 minutes,
period 1–90 days and up to eight supported report metrics.

Verification:

- Full tenant suite: **569 passed, 8 subtests passed, 1 skipped**, 137.65 seconds,
  external basetemp `schedules-full-tenant-0daba`.
- Full API suite: **402 passed**, 105.06 seconds, external basetemp
  `schedules-full-api-1e593`.
- Independent review: **45 tenant passed + 1 Linux-only skip**, **5 API passed**.
- Real private PDF generation, fixed period, SHA256 archive, two-shop isolation,
  native encrypted backup/restore and no duplicate due tick tested.
- Graphify AST extraction: 6467 nodes / 18355 edges; no LLM/network extraction.
- First full-suite failure was a test-only redirected backup-directory setup;
  explicit directory creation fixed it before the fresh full repeat.

Release gates still required: actual candidate Linux crash test, exact-image
scheduled-run/native restore acceptance, integrated frontend/browser acceptance
and root-owned production deployment. Windows skips the Linux parent-kill test;
no Windows crash-lifetime guarantee is claimed. Production Linux inherits the
same flock descriptor in its child, so killing a parent does not unlock a living
child's slot. Normal processes share the OS lock in addition to local semaphore.

The Gateway membership endpoint is owned and released separately by root. The
tenant trusts only the existing authenticated Gateway-forwarded identity boundary.
No permanent membership-success cache is introduced. Rollback after new writes
requires preserving current data and restoring a verified compatible database,
not simply changing the image. This source stage does not close the whole audit.
