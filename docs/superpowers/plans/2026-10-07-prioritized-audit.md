# Prioritized production audit implementation plan

> **For agentic workers:** Use scoped parallel workers; root integrates, reviews and deploys each priority tranche from committed source. Each task ends with fresh tests and live evidence.

**Goal:** Repair P0 production defects, then P1 correctness/mobile/resources/infrastructure, then prepare P2 mobile app requirements without claiming store publication.

**Architecture:** Preserve existing scoped secrets/encryption/runtime data; authenticate forwarded identity independently in tenant API. Apply release gates against actual immutable images and preserve rollback containers, volume mounts, aliases and manually upgraded limits.

**Tech Stack:** FastAPI, Python/SQLite, React/Vite, Docker/Traefik, SMTP/Telegram, rclone.

**Spec:** docs/CODEX_HANDOFF_AUDIT_2026-10-07.md and direct owner instructions 2026-10-07. Audit baseline254c898; current2380882 includes subsequent Ozon cabinet work that must be retained.

## Global constraints
- Do not print actual Telegram/API/encryption secrets, environment dumps or raw historical Sentry events.
- Build/deploy only committed HEAD; preserve unrelated TrueStats draft.
- P0 first; live checks per defect, encrypted backup/independent restore and rollback before swaps.
- Existing gateway1024MiB, provisioner256MiB, Traefik256MiB, GlitchTip PostgreSQL512MiB pinned; host8CPU/16GiB. Do not reduce tenant memory to512MiB before demonstrating real business workload fits.
- No actual marketplace price/budget/shipment actions in acceptance; no account deletion or payment changes.
- Existing keys must not be rotated blindly; owner will rotate exposed Telegram token after redaction deployment.

## P0 tranche
- [ ] Gateway logging/Sentry redact credentials at every depth; safe Telegram transport errors and bounded polling backoff. Tests synthetic token through formatter/events/breadcrumbs/exceptions; prod sanitized counters never raw logs.
- [ ] Fix provisioning closed connection; disposable-DB running branch regression; live authenticated provisioning request must redirect successfully.
- [ ] Backups: new namespaces, helperUID10001, private plaintext staging/cleanup, encrypted atomic write, bounded retention, adequate timeout/status, failure and freshness alarms, non-destructive offsite copy with encryption-only filter. Verify a fresh real encrypted backup plus independent restoration, failure paths in isolated fixture.
- [ ] Real tenant role fail-closed; scoped signed forwarded context, TTL/method/path/account/role checks and encoded-path rejection. Anonymous demo forced viewer. Negative live unsigned/forged writes rejected before handlers.
- [ ] Owner-only Telegram linking; code scoped to caller and tenant, manager rejected without destination changes.
- [ ] Root full test/release integration, immutable images, updated cron/config, every P0 live check; record unresolved external owner actions explicitly.

## P1 tranche
- [ ] Read-only price advice for viewer/demo; frontend + scripts tests in CI; codex branch triggers, matchingNode versions, supported Dockerignore filename, optimized-interpreter release gates.
- [ ] Mobile compact-flow/dialog width/safe-area/footer focus/contrast/touch targets; browser320/360/390/desktop both themes.
- [ ] Unified/lazy background processes, cache byte bounds and economics reuse; measure real tenant memory and concurrent business requests. Capacity/lifecycle decisions must not silently stop active users or erase data.
- [ ] Compose/prod reconciliation: resource limits, logging rotation, isolated writable socketproxy +separate readonly discovery proxy, alias preservation, hardened stagingtenant, scoped keyring, provisioner authentication, request-size/headers/session safeguards.
- [ ] Site drift intendedfeedback exclusion baseline, cronenv warning, static secret-ignore hygiene; no unrelated historical code removal without evidence.
- [ ] Committed P1 release and live verification.

## P2 tranche
- [ ] Mobile wrapper/store mode, native downloads and links/push boundaries, reviewer/demo account route and icons/PWA update behavior; explicit platform/account prerequisites.
- [ ] Account deletion/payment-history retention and public instructions; policy/offer/compliance text reconcile with actual functionality, owner legal review/actions retained.
- [ ] Tests/build/reviewable artifacts; do not publish to stores or claim legal filings without credentials and owner action.
