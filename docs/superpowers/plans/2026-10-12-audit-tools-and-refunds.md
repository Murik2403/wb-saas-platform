# Audit tools and proportional refunds

> **For agentic workers:** Use the authorized disjoint collaboration tasks and verify artifacts rather than installation status alone.

**Goal:** Install missing Android tooling, build and exercise a test package, restore all current offsite snapshots, implement the owner's proportional refund policy.

**Architecture:** APK builds from archived committed source with isolated caches. DR keeps owner keys local and all restoration offline. Financial changes use the existing account write lock, immutable granted intervals and cumulative successful refund amounts for exact idempotent time deductions.

**Tech Stack:** JDK21/AndroidSDK36/Gradle, pytest/Python/SQLite, encrypted backups.

**Spec:** Owner's audit/software installation request; owner selected proportional paid-time reduction on partial refunds.

## Constraints

- No production Android/iOS signing keys, store publication, actual charges/refunds, marketplace writes, customer deletion, Telegram activation, host reboot or endpoint-security changes.
- Preserve unknown archives and existing keys. Real software from vendor sources; verify archive hashes.
- Claude owns global tenant admission/resource budget on a separate branch.
- Unverified historical payment intervals are not guessed; retain a reconciliation marker. For an older prepaid interval, deduct its unconsumed paid time without removing a later renewal's separately purchased duration.

- [x] Android tools/APK: download, hash-check, build, inspect package.
- [x] Android runtime: instrumented isolated AVD acceptance in both themes; explicitly bound credential/device/push limits. Both native origin bugs reproduced and fixed. Final committed-source 35f27c4 APK rebuilt and passed two tests in each theme; emulator stopped.
- [x] Full current offsite set: outer/inner authentication, all DB integrity and settings, current per-tenant key recovery.
- [x] Refund policy: grant interval persisted; cumulative proportional deduction, duplicates/pending/terminal binding/over-refund/history regressions.
- [x] Fresh gates, commit scoped server code, gateway candidate/rollback restore and production rollout; update audit evidence. Server release 0e9b1ea; final mobile archive/build tracked separately.
