# Independent erasure recovery gate implementation plan

**Goal:** Prevent a restored control-plane database from reactivating a previously deleted identity.

**Architecture:** A separately mounted SQLite ledger records the original account identity before deletion. Gateway authentication and payment gates consult it; an offline/startup gate reconciles restored rows before HTTP or Telegram starts. Missing or invalid configured ledgers stop startup. This does not claim physical destruction of historical archives.

**Tech Stack:** Python standard library SQLite, existing gateway lifecycle, pytest, Docker bind mount.

**Spec:** Workspace delivery `rkn-notification-20261008/pdn-three-items-assessment.txt`.

## Constraints

- Preserve the explicit consent-text release hold; runtime security may be released separately from the live gateway revision.
- No real account deletion or archive cleanup during acceptance.
- Identity uses account id and original creation timestamp; reuse of id alone cannot block a different account.
- Ledger mount lies outside gateway_data and is never replaced by restoring that volume.
- Telegram remains enabled, but must start only after the recovery gate succeeds.

## Task 1: Ledger and authentication

Files: `gateway/logic/erasure_ledger.py`, `account_lifecycle.py`, `accounts.py`, focused tests.

- [ ] Verify restored identity is denied and a reused id with a different creation timestamp is permitted.
- [ ] Persist requested/finalized events before destructive operations; validate schema and file without silently creating a missing configured ledger.
- [ ] Deny authentication, sessions and billing on ledger errors.

## Task 2: Startup and offline gate

Files: `gateway/logic/erasure_recovery.py`, `gateway/app.py`, `gateway/docker-entrypoint.sh`, `docker-compose.yml`, tests.

- [ ] Test failed ledger validation prevents startup and reconciliation executes before serving.
- [ ] Add `python -m logic.erasure_recovery --check` before Telegram starts and repeat gate in ASGI lifespan.
- [ ] Provide offline reconciliation command for a stopped restored database; keep operations local and emit counts only.
- [ ] Mount dedicated host directory at `/app/erasure-ledger`; initialize explicitly before service replacement.

## Task 3: Verification and release

- [ ] Run full gateway and scripts tests, inspect diff, commit only owned changes, update graph.
- [ ] Build a committed runtime-only release preserving live legal templates; verify synthetic restore and missing-ledger refusal.
- [ ] Replace gateway preserving secrets, mounts, aliases, limits; verify public health and tenant forward-auth behavior.
- [ ] Record remaining archive cleanup, recipient-information and owner document approvals explicitly.
