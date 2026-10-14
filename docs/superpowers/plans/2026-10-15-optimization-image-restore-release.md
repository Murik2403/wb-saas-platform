# Optimization deployment and image restore Implementation Plan

> For agentic workers: execute the scoped steps below with independent test and review gates; parent alone controls SSH and deployment.

**Goal:** Deploy the committed89ad911 optimization from currentHEAD and verify load of the authenticated eight-image archive without changing lessons.
**Architecture:** Preserve each actual tenant base image and frontend. Copy only three committed Python modules. Validate encrypted backups, candidate and rollback on isolated real-data copies, then replace tenants sequentially with retained rollback containers. Independently load images into an owned network-none Docker and verify exact IDs and source layer count/SHA before cleanup.
**Tech Stack:** Python3.12, pytest, SQLite online backup, DockerAPI, SSH, authenticated existing encrypted escrow.
**Spec:** Owner request15October2026; docs/AUDIT_SHARED_HOST_NIGHT_2026-10-14.md and89ad911.

## Global Constraints

No lesson edits; Claude owns16MP4/VTT recovery. No production host/Docker reboot, WB/Ozon/provider writes, real refunds, blanket image pruning, old archive deletion or Telegram re-enable. Preserve frontend hashes, credentials/keyrings, volumes, aliases, resource limits and rollback. All source payloads come from git archive of exact committedHEAD. Real performance/capacity and fullOS/TLS restoration retain separate status.

### Task1: Fresh source and runtime baseline
- [x] Read AGENTS, graph query, `git status --short`, `git rev-parse HEAD`, `git show89ad911`; freeze source SHA and runtime module hashes.
- [x] Run readonly `python deliveries/optimization-release-20261015/preflight.py`; verify current five tenants/control-plane IDs, images and headroom. Check exact protected source IDs before image test.

### Task2: Fresh independent release tests
- [x] Run full tenant-app and tenant-api pytest with unique basetemp; record complete exit/counts and optimization parity regression output.
- [x] Review exact calendar operation totals/fractional-ID fallback, return classification and quality result identity; any result drift blocks release.

### Task3: Build committed minimal candidates
- [x] Use existing prepare.py/build.py architecture with base09e414a and exact three changed runtime files: tenant-app/calculations.py, tenant-app/ui_helpers.py, mh-redesign-lab/tenant-api/api_server.py.
- [x] `git archive` exactHEAD; assert dependency/frontend inputs unchanged. Build one derived image per actual unique base; verify inherited layers/user and committed module hashes. Do not compile or replace frontend assets.

### Task4: Isolated image acceptance
- [x] Freeze fresh date-bound public helper package, finite watchdog/timers and existing host/source fingerprints. Cap4GiB/2CPU, no swap, physical floor1GiB/20GiB.
- [x] Parent authenticates owner ACL and GCM before sending archive. Independently verify fresh stored handshake, exact own CID/engine and absence of application containers before approving stdin.
- [x] Verify complete inventory with--all, exact eight pins/nine roles and source layer count/canonicalSHA. Record missing/extra diagnostics on refusal. No application/provider starts.
- [x] Require cleanup/protected lifecycle/mount checks; retain previous negative proofs. Acceptance is only image load, not new-host fullDR.

### Task5: Backup, parity and sequential tenant rollout
- [x] Create fresh retained encrypted backup for every existing tenant. Restore candidate and rollback into network-none copies. Compare financial payloads and protected table hashes on identical copied data.
- [x] Verify candidate exact modules and all inherited frontend assets before replacement.
- [x] Deploy five tenants sequentially with immediate failed-gate rollback; compare mounts/aliases/env/memory/restart and public/signedbusiness endpoints after each.
- [x] Update future tenant image only after all five pass, preserving gateway/provisioner code/images and aliasprovisioner. Verify effective Compose and exact live pins, retain configuration backups.

### Task6: Final acceptance and audit
- [x] Check live immutable images/labels/modules/frontend hashes, measured live finance/management endpoints (cache condition not independently established) and data-quality concurrency. No customer data/value logging.
- [x] Verify16 existing lessons preserved without replacement. Confirm no own isolated containers/volumes/guard units remain.
- [x] Record RKN registry inclusion from owner screenshot as owner-provided evidence. Reconcile remaining audit gates and Claude handoff. Commit only scoped docs/code; preserve foreign untracked work.

## Execution receipt

Completed 15 October 2026 MSK. Deployed source HEAD: 3dc30c3c2594535178918822f29f8235ce126010, containing runtime optimization 89ad911. Fresh suites: tenant-app 1233 passed / 1 skipped / 62 subtests, tenant-api 678 passed. Real copied-data financial equality: 16 responses; synthetic equality: 576. Actual original image archive: 8 immutable IDs / 9 roles, run 30208ac466a5, cleanup verified. See docs/AUDIT_CURRENT_STATUS_2026-10-15.md for the final live measurements, retained negative acts and remaining acceptance boundaries. The first pre-live manifest rejection changed no live container; only the expected build marker was corrected. Control-plane images/code unchanged, containers recreated to commit the future tenant pin. This receipt does not claim cold-cache finance latency, full new-host DR or strict 25/30 capacity.
