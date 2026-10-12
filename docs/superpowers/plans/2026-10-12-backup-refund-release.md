# Backup and refund release implementation plan

> For agentic workers: execute the existing branch with independent review, fresh verification and production acceptance before claiming completion.

**Goal:** Merge 75d5f1e and release consistent SQLite backups plus durable outgoing refund intents without changing the deployed finance runtime.

**Architecture:** Gateway records additive refund intents before provider calls. Provisioner uses an isolated tenant-image helper to create online SQLite snapshots, then encrypts and retains archives through the existing pipeline.

**Tech Stack:** Python, FastAPI, SQLite/PostgreSQL, Docker Compose, existing backup crypto, fake YooKassa provider in tests.

**Spec:** User handoff; docs/BACKUP_CONSISTENCY_ROLLOUT_2026-10-15.md and docs/REFUND_IDEMPOTENCE_2026-10-16.md in claude/backup-refund-bench-20261016.

## Global constraints

- Deploy only committed HEAD; keep tenant runtime e7b1582 and finance fixes.
- Preserve provisioner alias, existing secrets, mounts, network isolation, memory limits and future tenant image pin.
- No real payment or refund, no deletion of unknown archives, no host restart or Traefik replacement.
- Record private data only in restricted operator copies; publish sanitized timings, counts, hashes and booleans.
- Benchmark extrapolations for 25/30 tenants are not production capacity measurements.

## Tasks

- [x] Review snapshot and refund code independently; verify provider contracts against official documentation when uncertain.
- [x] Fast-forward the requested branch, resolve only evidence-backed issues and run full gateway, provisioner and scripts tests plus the finance regression subset.
- [x] Build gateway/provisioner images from committed HEAD; verify exact file hashes, snapshot helper inclusion and unchanged tenant image.
- [x] Exercise the snapshot helper on real Docker with synthetic WAL writes and verify archive consistency, UID/mode, encryption and isolated restore.
- [x] Verify encrypted control-plane backups and isolated candidate/rollback startup; inspect additive migration without contacting YooKassa.
- [x] Deploy gateway, then provisioner through Compose while preserving alias and all runtime settings. Retain rollback containers and images.
- [x] Manually snapshot demo through the deployed API, independently decrypt/restore, run integrity_check and check no fresh stage/partial files remain.
- [x] Check all tenant health/build IDs, protected refund endpoints, schema/index migration, admission budget and lesson assets. Document actual deployment and remaining external payment-provider acceptance.

Acceptance: docs/BACKUP_REFUND_RELEASE_2026-10-12.md. Runtime b13fa97; tenants e7b1582. External test-shop refunds and production density beyond the measured local benchmark are explicitly outside completed acceptance.
