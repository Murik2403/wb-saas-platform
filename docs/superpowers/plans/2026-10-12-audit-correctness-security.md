# Audit correctness and security implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking. This session uses the available collaboration tools with explicit, disjoint file ownership.

**Goal:** Close independently reproducible data-integrity and security gaps and release verified committed source without changing customer marketplace data.

**Architecture:** Keep SQLite updates transactional at their public workflow boundaries. Gateway creates an unpredictable per-response CSP nonce before Jinja renders; only nonce-authorized scripts execute. Host backups add authenticated key identification while retaining explicit legacy decryption and unchanged existing keys.

**Tech Stack:** Python, SQLite, FastAPI/Starlette, Jinja, AES-GCM, pytest, Docker.

**Spec:** Owner's 12 October request; `C:/Users/mvvor/Downloads/wbsaasplatform/wb-saas-platform-clean/docs/PROJECT_AUDIT_2026-10-08.md`, reconciled against current `d24562f` rather than its obsolete base.

## Global constraints

- Existing branch `codex/cabinet-ux-20260930`; preserve untracked `.build-temp/` and TrueStats document.
- Deploy only committed HEAD with encrypted restore/candidate/rollback gates.
- Preserve UID/GID 10001, tenant memory 1536 MiB, volumes, secrets, aliases and provisioner control isolation.
- Telegram stays disabled; 16 lessons and subtitles remain byte-identical.
- No actual WB/Ozon price changes, charges, customer deletion or historical-stock corrections.
- Historical archives retained by owner decision are outside this task.
- Claude owns frontend bundle splitting/mobile preparation; parent owns gateway CSP and release integration.

### Task 1: Atomic ingestion (agent audit_ingestion_20261012)

**Files:** `tenant-app/db/ingest.py`, `tenant-app/db/__init__.py`, `tenant-app/sync.py`, relevant ingestion/sync tests.
**Interfaces:** one transactional replacement for explicitly fetched campaign/date scopes; explicit snapshot timestamp retained.

- [x] Reproduce failure between delete and upsert and omission of historical campaigns.
- [x] In one connection/transaction replace only complete fetched scopes; rollback preserves all prior rows and outside-period rows.
- [x] Verify partial upstream responses do not erase rows; explicit empty complete scopes can clear their own rows.
- [x] Verify timestamp fallback against Moscow snapshot contract rather than assigning marketplace dates to fetched snapshots.
- [x] Run ingestion, partial sync, progress and finance consumers' tests.

### Task 2: FIFO workflow concurrency (agent audit_fifo_20261012)

**Files:** `tenant-app/db/fifo_finished_goods.py`, actual dispatch/receipt workflow module and associated tests.

- [x] Reproduce concurrent calls at public dispatch/receipt boundaries before adding locks.
- [x] Acquire write lock before deduplication reads when caller owns transaction; preserve rollback/nested transaction behavior.
- [x] Validate idempotent movement/allocations, variant identity and monetary totals.
- [x] Record false-positive helper races; never reinterpret stock shortfall as a proven sale.

### Task 3: Backup key identification (agent audit_backup_format_20261012)

**Files:** `provisioner/backup_crypto.py`, `app.py`, `decrypt_backup.py`, crypto tests; narrow format checks in `scripts/backup_offsite.py`, `backup_offsite_envelope.py`, `pdn_retention.py`, `pdn_retention_job.py` and their tests.

- [x] Add v2 authenticated nonsecret key-ID header with bounded explicit keyring selection.
- [x] Preserve v1/current-key decryption and operator CLI compatibility; unknown IDs reject without trying unrelated keys.
- [x] Reproduce header tamper failure, no plaintext residue, streaming and deadlines.
- [x] Verify new backup traverses offsite envelope and independently restores; never rotate a production key.

### Task 4: Nonce CSP (parent)

**Files:** `gateway/security_headers.py`, `template_setup.py`, script-bearing Jinja templates, `static/public-analytics.js`, security/template tests.
**Interfaces:** middleware populates `scope['state']['csp_nonce']`; shared template context exposes `csp_nonce`; response policy carries same nonce.

- [x] Failing tests extract nonce from header and HTML, compare script tags, require fresh nonce per response, reject unsafe-inline/eval and event handlers.
- [x] Implement nonce generation before template rendering and apply script policy without buffering streamed media.
- [x] Annotate trusted static/inline/JSON-LD script tags; propagate nonce to consent-gated Metrika loader.
- [x] Preserve independent stricter route policies, CSRF, theme, calculator, iframe, consent and media Range behavior.
- [x] Check real browser UI and CSP violations locally and after gateway release.

### Task 5: Integrated release (parent)

- [ ] Review every agent diff and run relevant complete suites from committed archive.
- [ ] Commit only owned source/tests/plan; update Graphify without staging generated graph files.
- [ ] Build gateway, tenant and provisioner images from the exact commit, keeping dependency/runtime parity.
- [ ] Rehearse encrypted restore and rollback; deploy gateway, provisioner and tenants with current image IDs pinned.
- [ ] Verify all five tenants' health, signed/unsigned role boundary, cold finance timing, CSP UI and backup v1/v2 recovery.
- [ ] Record deployed revision and unresolved external gates: nonempty Ozon evidence, store signing/device acceptance, accountant/receipt evidence.
