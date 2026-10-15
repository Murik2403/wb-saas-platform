# Audit follow-up implementation plan

**Goal:** Close demonstrated alert, scheduled-backup and report-memory defects; preserve unresolved recovery/capacity gates; delegate a separate queue-aware benchmark to Claude.

**Evidence cut:** After committed report/FIFO read fix `d0141b2220242f003f00edf8d88bd94b6202d98f`, 15 October 2026. Four-phase attempt 4 was NEGATIVE despite all 21 content/PNG pairs matching: ballast incurred an OOM kill. Two-module attempt 5 passed unchanged resource/content gates and all five tenants now run d0141b2. Gateway25ae99a includes owner-requested storage removal and light-theme styling; provisioner remains73c6a34. Historical d014/control73 image restoration passed on the isolated new OS; latest-gateway capture and full application/data/reboot acceptance remain open. [Detailed follow-up](../../AUDIT_FOLLOWUP_2026-10-15.md).

**Boundaries:** Parent owns production operations. No daytime density stress, production reboot, customer email, payment/provider write, new expense or unknown-archive deletion. Previous full-power permission expired at 07:00 MSK. Bounded copy acceptance does not establish 20–30 capacity or fresh-OS DR.

## 1. Observe before changing code

- [x] Retain fresh runtime/process/cgroup evidence; correlate the actual current-container OOM with report run 82 and `wb_commission_rate` at 06:01:24 UTC. API/resident and container init stayed alive.
- [x] Trace the exact backup-alert wrapper with fake transport; actual SMTP authentication failed with code 535. No SMTP DATA or real message sent.

## 2. Repair demonstrated defects

- [x] Alert/cron/offsite regression tests: 44 passed. Existing mailer invocation is intact; no speculative source rewrite.
- [x] Configuration-only SMTP rotation: same gateway application revision/image, only password changed, rollback retained. Runtime authentication and NOOP 250 verified; no messages sent.
- [x] Commit `61d9068`: project unused financial/catalog payloads out before commission chart fetch; no formula change.
- [x] Preserve real-copy attempt 3 as NEGATIVE: commission RSS improved with exact parity, but full PDF plus 1024 MiB ballast incurred an OOM. PDF bytes alone do not pass resource acceptance.
- [x] Commit `c6b1378`: same projection on eight remaining direct metric reads. Full coverage: 1,289 passed, one skipped, 62 subtests; seven populated real-SQLite title/summary/PNG parity cases, ten SQL-authorizer mutation catches. Graphify updated; diff check clean.
- [x] Preserve parent-reported four-phase attempt 4 as NEGATIVE despite equality of all 21 content/PNG pairs: ballast OOM remains, so c6b1378 is not resource-accepted.
- [x] Commit `d0141b2`: stream FIFO reconciliation reads, compact ordinary financial/event payloads to existing SRID semantics, retain full original rows for every ID in all three provenance tables within the same BEGIN snapshot. Formulas/projectors unchanged; 56 focused and 1,292 full-suite tests pass, one skipped, 62 subtests; four mutations caught.
- [x] Reconcile attempt 4 receipt and complete two-module attempt 5 with unchanged 1024 MiB ballast/1536 MiB limit, all 21 content/PNG pairs equal, zero new OOM and confirmed cleanup.
- [x] Actual committed d0141b2 deployed to five tenants with identity/content/resource checks and retained encrypted backups; later owner-requested gateway25ae99a public hotfix separately verified live.
- [ ] Verify inbox delivery separately if authorized; authentication/NOOP is not an email receipt.

## 3. Scheduled backup and image recovery

- [x] Read-only scheduled evidence: daily attempt 02:15:39 UTC, verified 02:18:20 UTC on 15 October; local freshness/offsite/key-escrow flags true, no error/alert. Encrypted groups include control plane, all five tenant groups and training media.
- [x] Hourly watchdog at 13:45:01 UTC is fresh and error-free. Both cron entries use committed 73c6a34 runner. Actual persisted scheduled results are separate from earlier manual function tests.
- [x] Historical d014/control73 manifest, owner authentication, encrypted offsite readback and exact8-root/9-role native OCI image restoration passed. No app containers started. Latest gateway25ae99a is absent from this snapshot and remains a separate capture/load gate.
- [ ] Independent new OS boot/reboot, host jobs, DNS/TLS, auth and UI remain open. DinD is not a new guest OS.

## 4. Claude delegation

- [x] Update Claude handoff to d0141b2: scripts/tests/docs only, separate queue-aware profile, real shared PDF slots=2, all N API/resident and N queued jobs, fixed deadlines, kernel peak/OOM/health/latency and unchanged admission.
- [ ] Local 2–4 smoke only. Preserve strict simultaneous 25/30 negatives; queue-aware 20/25/30 remains NOT_RUN until an actual permitted stand run.

## 5. Final status

- [x] Independent frozen four-phase operator/probe review and actual two-module acceptance both passed; original negative resource attempts remain preserved.
- [x] Parent reconciled actual deployment, latest public hotfix, native historical image-load and private VM stop receipts; full application/reboot and20–30 load gates remain open.

Independent key custody, test-shop payment/refund proof, physical mobile/signing/stores, accountant review and owner-paused non-empty Ozon acceptance remain separate. Telegram stays disabled; unknown archives and owner-excluded Defender are outside this work.
