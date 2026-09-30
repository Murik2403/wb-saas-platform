# Daily demo refresh implementation plan

**Goal:** Refresh only the fixed fictional demo daily, with verified encrypted rollback and five real business controls.

**Architecture:** Opt-in provisioner lifecycle worker, persistent daily success/attempt state under the existing backup mount, cross-process file lock. Reuse constrained reseed and verified rollback. A shared tenant validator proves procurement, production, dispatch/receipt, sale/return FIFO, and manual expense; record Moscow fixture date. No customer writes, marketplace calls, raw cron or secrets in status.

## Evidence before changes
- Production read-only inspection 2026-09-30: no demo timer or cron job; demo markers absent and newest sale 2026-09-13.
- Source validator uses unqualified manufacturing source key, whereas release persists variant-qualified key.

## Steps
- [x] Add real fixture validator and current Moscow marker; regress deterministic reseed, each failed control and date freshness.
- [x] Add persisted single-worker daily scheduler with bounded retry, failure sanitization, cross-process exclusion and read-only status.
- [x] Wire opt-in lifecycle and Compose flag; verify reseed health before successful daily marker.
- [x] Run tenant seed/validator and complete provisioner tests, compile and graph update; commit.
- [ ] Parent integrates/deploys provisioner and tenant images, enables schedule, then verifies actual first success plus five controls in production demo. This agent performs no production writes.

## Verification
- Full tenant suite: 380 passed plus 8 subtests. Initial execution from repository root had one subprocess import failure; rerun from required tenant-app working directory passed all.
- Provisioner suite: 32 passed, including persisted restart idempotence, Windows OS lock exclusion, hourly failure retry, corrupt state/disk failure fail-closed, Moscow midnight and health prerequisite.
- Real fictional seed: five controls plus fresh true; each separately corrupted control rejects acceptance; same-day reseed deterministic.
- Python compile and git diff whitespace gate passed. Graphify rebuilt.
- Source remains opt-in until parent enables configuration. Production live schedule and latest refresh are not yet accepted.
