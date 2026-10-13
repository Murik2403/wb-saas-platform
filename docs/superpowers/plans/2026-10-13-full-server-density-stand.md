# Full server recovery and density acceptance implementation plan

> **For agentic workers:** Execute disjoint tasks with available collaboration tools. Parent verifies source and measured results before committing. Production must not be contacted or changed.

**Goal:** Rebuild the complete server stack on an independent stand and measure 20/25/30 tenant acceptance with explicit resource, dataset, admission and recovery evidence.

**Architecture:** Dedicated Docker engine/VM with unique names, networks, synthetic stand identity and no marketplace/payment/SMTP/Telegram egress. Verified encrypted recovery inputs must be available independently of production. Synthetic bootstrap and actual disaster recovery have separate verdicts.

**Tech Stack:** Docker/Compose, Python/pytest, SQLite online backup, TLS/Traefik, Linux systemd/cron; optional PostgreSQL.

**Spec:** Owner request 13 October 2026: finish full-server and 20–30 tenant checks on a separate stand; do not touch production. Latest accepted runtime `09e414a`, local source `fdcc301`.

## Global constraints

- No production SSH/API/load/restart/deployment or real customer/marketplace/provider operation.
- Do not stop unrelated local Docker containers or change global `.wslconfig`/Docker Desktop resources.
- Rental requires an owner-provided stand or explicit budget; administrator-only VM creation requires legitimate owner action.
- Existing unknown archives and owner recovery keys must remain intact; missing secrets cannot be invented for an actual DR verdict.
- Private restored fixture stays isolated and out of Git, logs and public artifacts. Source archive and test fixture checksums recorded.
- Capacity refusal, empty results, worker exceptions, skipped background phases and memory-floor breach are not success.
- A 16 GiB host's default admission may refuse tenant 30. Do not weaken the admission budget to obtain a green verdict.
- Hyper-V/new-host reboot, Docker daemon restart and application PID restart are distinct checks. Container-only bootstrap does not prove a new-host recovery.
- Claude owns the 16-lesson media restoration; do not modify his files or rebuild videos.

### Task 1: Stand and recovery-input preflight (parent / DR agent)

**Files:** `deliveries/full-recovery-density-20261013/stand-preflight.json`, recovery requirements / operator documentation.

- [x] Inspect CPU/RAM, local Docker endpoint and existing local containers read-only.
- [x] Verify Hyper-V management access without attempting a privilege bypass.
- [ ] Obtain a sufficient independent stand without interrupting existing services.
- [ ] Authenticate current backup/ledger/configuration/TLS/key inputs from independent owner-side copies.
- [x] Freeze source and build gateway, provisioner and tenant images for the stand only.

### Task 2: Full recovery (DR agent / parent)

**Files:** isolated full-stack operator, safety tests and `docs/AUDIT_FULL_SERVER_RECOVERY_2026-10-13.md`.

- [x] Review explicit local/dedicated endpoint guard, ownership and no-egress boundary before launch.
- [ ] Restore five tenant datasets, control plane and current deletion ledger; compare all original table content before/after startup.
- [ ] Restore actual deployment/key/TLS/systemd/cron state where available; label synthetic replacements explicitly.
- [ ] Verify HTTPS routing, auth/role/shop isolation, provisioner alias, capacity, backups, schedules and restart readiness after a whole stand reboot.
- [x] Verify cleanup only exact run-owned objects; preserve all unrelated objects and source hashes (synthetic DinD attempt5 only).

Synthetic mechanical gate completed separately: 27 boolean gates, real email-confirmation/provision/login/HTTPS/owner/anonymous denial/egress/capacity before and after own daemon restart. This is not an actual archive restore or new-host reboot.

### Task 3: Density harness repair and execution (density agent / parent)

**Files:** `scripts/bench_tenant_density.py`, `scripts/tests/test_bench_density_acceptance.py`, measured proof JSON/doc.

- [x] Reproduce false-success risks on worker exceptions/empty responses and unsafe fixture seeding.
- [x] Support immutable restored fixtures and strict phase/watchdog refusal semantics.
- [x] Cover latest expensive local analytics and actual selected admission budget, memory/CPU/health/queue measurements.
- [ ] Execute serial, concurrent, background and combined phases at 20/25/30 on the independent stand; preserve genuine refusals.
- [ ] Repeat recovery/health checks after load and report measured limits without extrapolating synthetic data to real sync.

### Task 4: Independent review and final evidence (parent / reviewer)

- [x] Verify hashes, labels, resource limits, endpoints and no production/unrelated-object changes (synthetic mechanical run).
- [x] Run focused regressions and relevant full checks; update Graphify after source changes.
- [x] Commit a truthful closed/open matrix. No production deployment is requested for this task.

Additional actual gates: synthetic frozen copy helper passes root/drop-caps → UID10001 read/write and original hashes; one deployed cabinet from committed6204510 completes all four phases, actual resident PDF and10.574672s measured calculation/PDF overlap. Added all-cohort interval proof and verified it against the saved one-cabinet timestamps. Geometry preflight for20/25/30 refuses the current8GiB/12CPU before creating their resources. These do not close either pending large-stand/real-recovery task. Separate host/rental budget question remains unanswered; independent deployment/key/TLS/host-job inputs still needed.
