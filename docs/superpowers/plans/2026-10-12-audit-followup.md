# Audit follow-up implementation plan

> **For agentic workers:** Execute these disjoint tasks with the available collaboration tools; parent integrates and deploys only committed source.

**Goal:** Fix reproducible remaining runtime failures and replace stale audit assertions with current evidence.

**Architecture:** Retain explicit PWA update consent and existing financial lifecycle rules. Verify offsite recovery independently and inspect capacity without production stress or customer writes.

**Tech Stack:** TypeScript/Vitest, Python/pytest, Docker, encrypted backups.

**Spec:** Owner's audit continuation request; existing audit correctness and production reports, current HEAD `91dcab0`.

## Global constraints

- No new charges/refunds, marketplace sends, customer deletion, secret rotation or Telegram activation.
- Preserve unknown archives, foreign working files, 16 narrated lessons, container data/UID/memory/network aliases.
- No automatic reload of a user's unsaved form.
- Native signing/device/store acceptance delegated to Claude; real Ozon and accountant acceptance remain factual external gates.

### Task 1: PWA update ordering

**Files:** `mh-redesign-lab/src/pwa-updates.ts`, `pwa-updates.test.ts`.

- [x] Reproduce build-ID offer arriving before waiting service worker and vanished waiting worker at explicit click.
- [x] Make explicit update activate the currently waiting worker and reload once; retain Later/cleanup.
- [x] Run frontend tests, TypeScript, build and service worker regression.

### Task 2: Refund verification HTTP client

**Files:** `gateway/yookassa_client.py`, `gateway/tests/test_yookassa_client.py`.

- [x] Reproduce missing requests import using mocked HTTP transport rather than mocked get_refund.
- [x] Add the missing local import; preserve authoritative authenticated provider lookup.
- [x] Verify success/error/auth/timeout and billing/webhook regressions without external payment calls.

### Task 3: Capacity and recovery evidence

**Files:** `docs/AUDIT_CAPACITY_VERIFICATION_2026-10-12.md`, `docs/AUDIT_OFFSITE_RECOVERY_VERIFICATION_2026-10-12.md`.

- [x] Inspect runtime/process/load controls and bounded isolated concurrency tests.
- [x] Verify owner-side offsite recovery with existing keys without disclosing or uploading private material.
- [x] Reconcile evidence in `docs/AUDIT_CURRENT_STATUS_2026-10-12.md`, distinguishing implemented, verified and external gates.

### Task 4: Release

- [ ] Review scoped diff, clean test gates, commit approved files only.
- [ ] Build from git archive HEAD; encrypted independent restore and candidate/rollback before switching changed services.
- [ ] Check live hashes/health, explicit update ordering, preserved media and controls; save sanitized operator evidence.
