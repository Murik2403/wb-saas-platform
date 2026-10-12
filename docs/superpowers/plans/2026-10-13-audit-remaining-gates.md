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

- [x] Reproduce false success on conflicting content, duplication without a primary key and identity-column import failure using a disposable local PostgreSQL.
- [x] Hold a read-only source snapshot; lock destination tables and verify full row multisets before committing. Reject divergent destinations atomically; permit exact repeat without duplication.
- [x] Preserve explicit identity values and advance their sequences for the first new row.
- [x] Run regressions and full scripts checks. Do not migrate production.

### Task 2: Restored application rehearsal (DR agent)

**Files:** isolated rehearsal tooling and `docs/AUDIT_DR_APPLICATION_REHEARSAL_2026-10-13.md`.

- [x] Authenticate current S3 copies using owner-local keys; start independent gateway and tenant application candidates with no external network/jobs.
- [x] Verify restored DB integrity/content and application reads; prove source identities unchanged and clean owned plaintext candidates of the successful run. Earlier debug folder cleanup was policy-rejected and is explicitly documented separately.
- [x] Record application recovery boundaries and remaining new-host/key-loss acceptance.

### Task 3: Android extended acceptance (Android agent)

**Files:** opt-in native instrumentation and `docs/AUDIT_ANDROID_EXTENDED_2026-10-13.md`.

- [x] Verify synthetic download bytes, native share cancellation and lifecycle on installed emulator in both themes.
- [x] Inspect physical-device availability read-only; retain owner signing/device/iOS boundaries.

### Task 4: Restart policy inspection (release agent)

**Files:** `docs/AUDIT_RESTART_RECOVERY_2026-10-13.md` and isolated probe tooling if needed.

- [x] Inspect live policies/systemd without restarting production.
- [x] Demonstrate isolated local crash/restart behavior and explicitly leave host reboot untested.

### Task 5: Consolidation (parent)

**Files:** current audit status and Claude handoff.

- [x] Refresh live read-only health/revisions and existing completed release status.
- [x] Review agent diffs/results independently, update Graphify after code edits, commit scoped verified changes.
- [x] Deliver exact completed/open matrix and separate Claude work order. Measured load handoff arrived during work; the next disjoint task is media recovery.

### Scope amendment after measured density handoff

Claude's `76472e3` was merged. Capacity acceptance 20–30 remains failed/unproven.
The confirmed concurrent FIFO GET defect was fixed and regression-tested.
Real restored-copy acceptance then found full data-quality timeout beyond 35 s;
its request budget and overview coalescing were added in `09e414a`.

Authorized runtime replacements supersede the inspection-only identity constraint
for the explicit releases: same data mounts/keys/roles/networks/limits/restart
policies, immutable committed source, encrypted backups and independent
candidate/rollback restores. No host reboot, live stress or customer marketplace
write is permitted. Claude's next disjoint task is 16-lesson media recovery,
as recorded in `CLAUDE_AUDIT_HANDOFF_2026-10-13.md`.
