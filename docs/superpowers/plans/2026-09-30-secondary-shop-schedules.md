# Secondary WB shop schedules implementation plan

**Goal:** Explicit opt-in secondary-shop synchronization and scheduled local PDFs, with fresh membership/ACL/seller checks and atomic snapshot/run receipts.

**Architecture:** Existing tenant report scheduler invokes a separate bounded secondary-shop runner. A persisted schedule is a narrow owner-authorized delegation; it never grants warehouse ACL or stores a trusted role indefinitely. Gateway answers the current membership role for the exact tenant-scoped internal credential. Private child workers never deliver email/chat or mutate central physical stock.

**Spec:** `docs/marketing/AUDIT_CLOSURE_MATRIX_2026-09-30.md`, P2 multiple shops.

## Constraints

- New schedules default disabled; no automatic activation on deployment.
- Explicit existing write ACL is required for delegated actor. No automatic grants.
- Current membership must be owner/manager and current ACL must still allow write. Unknown/unavailable role verification fails closed; old snapshot remains readable.
- Exact shop seller hash, current schedule generation, active shop and revision are checked before and after child execution.
- Schedule claim/commit uses immutable UUID derived from schedule ID/generation/due time. Replays do not sync or generate twice.
- A conflict/timeout never overwrites newer snapshot; next attempt is bounded and visible.
- Local PDF generation only. Sending to email/Telegram is outside this release and requires explicit channel configuration and authorization.
- Keep raw token, role-verification secret and database content out of API errors/logs.

## Deliverables

1. Gateway: `POST /internal/tenant-member-role` accepts `{slug, account}` through existing tenant-scoped secret validation; replies only `{role: owner|manager|...|null}` for active tenant and exact membership. Unknown account is null. No lists, email addresses, secrets or cross-tenant status leaks. Add wrong-secret, wrong-slug, revoked-role, disabled-tenant tests.
2. Tenant service: fixed configured HTTPS gateway URL, no redirects, short timeout, bounded response and strict role parsing. Caller checks exact requested tenant/account; no cached success after revocation.
3. Schedule store: explicit owner-only configure/disable, persisted actor/seller/generation, strict cadence bounds, due claim, immutable attempt history and bounded retries. All objects included in existing encrypted native backup.
4. Runner: run at most one due secondary job per scheduler tick, reuse existing private worker preparation and atomic bridge; verify current role+ACL/seller both before prepare and at commit. Sync updates successful timestamp only on actual complete sync. PDF is archived in the selected snapshot, not global filesystem or another shop.
5. UI: selected additional-shop schedule controls, opt-in and last attempt/status, Russian failure reasons; owner management only. No misleading delivered state or implicit sync from report view.
6. Acceptance: role/ACL/seller revocation during prepare, stale revision, duplicate due tick/restart, two-shop isolation, sync failure retention, PDF identity/hash/private download, native backup restore and rollback. Independent source review, full relevant suites, exact image staging, actual browser and production acceptance precede closure.

## Work ownership

Root prepares the Gateway contract without editing the currently owned private worker. Backend worker modifications begin only after the shared financial projection commit is frozen and independently accepted. UI and reviewer remain responsible for their current stage5 deliverables before taking schedules. This plan is not deployment or completion evidence.
