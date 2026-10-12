# Remaining audit gates implementation plan

> **For agentic workers:** Execute the disjoint assignments with available collaboration tools. Parent reviews and verifies changes before committing; runtime deployment only when runtime source changes.

**Goal:** Close actionable migration, restored-application and Android runtime gaps and reconcile the stale audit with verified production evidence.

**Architecture:** Independent offline PostgreSQL migration tests, network-isolated restored candidates and opt-in Android instrumentation. Production is inspected read-only; no migration, customer write or reboot.

**Tech Stack:** Python/pytest/psycopg, Docker, SQLite online backup, Android SDK/JDK/adb.

**Spec:** Owner's audit continuation request; current status and backup/refund release docs; source HEAD `7a9fd88`.

## Global constraints

- Preserve existing archives, foreign worktree files, keys, 16 lessons, disabled Telegram and live container identities.
- No marketplace/provider calls, real refunds, signing-key creation, store publication or production stress/reboot.
- Keys and customer values never enter Git, stdout or evidence artifacts.
- Measured isolated checks do not establish full host loss recovery or production 30-tenant capacity.

### Task 1: Verified SQLite to PostgreSQL migration (parent)

**Files:** `scripts/migrate_sqlite_to_postgres.py`, `scripts/tests/test_migrate_sqlite_to_postgres.py`, `DEPLOY.md`.

- [ ] Reproduce false success on conflicting content, duplication without a primary key and identity-column import failure using a disposable local PostgreSQL.
- [ ] Hold a read-only source snapshot; lock destination tables and verify full row multisets before committing. Reject divergent destinations atomically; permit exact repeat without duplication.
- [ ] Preserve explicit identity values and advance their sequences for the first new row.
- [ ] Run regressions and full scripts checks. Do not migrate production.

### Task 2: Restored application rehearsal (DR agent)

**Files:** isolated rehearsal tooling and `docs/AUDIT_DR_APPLICATION_REHEARSAL_2026-10-13.md`.

- [ ] Authenticate current S3 copies using owner-local keys; start independent gateway and tenant application candidates with no external network/jobs.
- [ ] Verify restored DB integrity/content and application reads; prove source identities unchanged and clean owned plaintext candidates.
- [ ] Record application recovery boundaries and remaining new-host/key-loss acceptance.

### Task 3: Android extended acceptance (Android agent)

**Files:** opt-in native instrumentation and `docs/AUDIT_ANDROID_EXTENDED_2026-10-13.md`.

- [ ] Verify synthetic download bytes, native share cancellation and lifecycle on installed emulator in both themes.
- [ ] Inspect physical-device availability read-only; retain owner signing/device/iOS boundaries.

### Task 4: Restart policy inspection (release agent)

**Files:** `docs/AUDIT_RESTART_RECOVERY_2026-10-13.md` and isolated probe tooling if needed.

- [ ] Inspect live policies/systemd without restarting production.
- [ ] Demonstrate isolated local crash/restart behavior and explicitly leave host reboot untested.

### Task 5: Consolidation (parent)

**Files:** current audit status and Claude handoff.

- [ ] Refresh live read-only health/revisions and existing completed release status.
- [ ] Review agent diffs/results independently, update Graphify after code edits, commit scoped verified changes.
- [ ] Deliver exact completed/open matrix and separate Claude load-benchmark work order.
