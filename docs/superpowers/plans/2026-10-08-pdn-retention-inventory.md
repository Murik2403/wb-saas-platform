# PDN retention inventory implementation plan

**Goal:** Produce a read-only inventory of local, staging and S3 archive retention, before any deletion policy changes.

**Architecture:** A standalone Python CLI examines exactly one level of namespace directories and metadata exported by `rclone lsjson`. It never deletes, downloads, decrypts or changes retention. Age flags are review candidates, not proof of eligibility for destruction. Missing metadata, unsafe paths, symlinks and unknown S3 versioning block a completion claim.

**Tech Stack:** Python standard library; pytest; existing SSH/rclone configuration.

**Spec:** `deliveries/rkn-notification-20261008/pdn-three-items-assessment.txt` in the workspace parent.

## Global constraints

- No production deletion, real account removal, token rotation or Telegram disabling.
- Preserve the gateway legal release hold.
- Deploy/run source only after a tested commit; keep full inventory private.
- Proposed 14-day policy is diagnostic only until owner approval and implementation.

## Task 1: Inventory CLI

Files: create `scripts/pdn_backup_inventory.py`, `scripts/tests/test_pdn_backup_inventory.py`.

Interface: `inventory(root, days, now)` returns metadata rows with `status`, `age_days`, `namespace`, `path`, `size`; `remote_inventory(rows, days, now)` validates supplied rclone metadata. `main()` emits one JSON report with `mode=read_only`, `deletion_authorized=false`, counts and rows.

- [ ] Write tests for stale/live archives, unknown formats, symlinks, missing dates, future dates and unsafe remote paths.
- [ ] Run `python -m pytest scripts/tests/test_pdn_backup_inventory.py -q`; confirm missing-module failure.
- [ ] Implement path/metadata validation and a CLI requiring local/staging roots, optional remote JSON, bounded days (1..3650).
- [ ] Run focused tests and full `scripts/tests`, inspect `git diff --check`.
- [ ] Commit only the script, tests and this plan. Archive the committed script and run it read-only on production metadata.

## Task 2: Preserve evidence and Telegram preparation

- [ ] Save private server inventory with mode 0600; return only aggregate counts to the user.
- [ ] Prepare a local Telegram recipient-information request; do not send it without explicit messaging authorization.
- [ ] Record missing versioning/Object Lock facts and independent erasure-ledger integration as remaining work, not completed features.

## Next implementation gate

The erasure ledger must be mounted independently of restored control-plane data, written before final deletion, consulted before gateway/tenant traffic and applied to every restore/rollback. A source-only helper is insufficient. Integration and deployment require preserving the existing consent release hold or isolating an explicitly approved runtime-only release.
