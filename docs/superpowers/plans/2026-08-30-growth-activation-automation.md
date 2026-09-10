# Growth Activation Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make MARKETSHELPER's acquisition-to-activation funnel measurable and automatically guide new trial users to their first useful Wildberries report.

**Architecture:** Add a small first-party funnel event store to the existing gateway control-plane database. Capture anonymous campaign parameters before registration, attach them to the account at signup, and record server-side lifecycle events at registration, provisioning, first tenant access, first sync/report, and payment. Add a scheduled lifecycle worker that sends consent-safe email messages based on durable account state; Telegram remains an optional product channel, not a marketing channel by default.

**Tech Stack:** FastAPI, SQLite/PostgreSQL-compatible gateway logic, Jinja2, existing SMTP mailer, Python `unittest`, Docker Compose cron/worker scripts.

**Spec:** Product growth strategy agreed in this conversation: measure UTM and activation events, automate onboarding and trial reminders, surface a first useful result, and preserve existing billing/security behavior.

## Global Constraints

- Do not store raw passwords, API tokens, payment data, or sensitive Wildberries data in analytics events.
- Preserve CSRF protection, registration rate limiting, PDN consent handling, and current billing transitions.
- Lifecycle jobs must be idempotent; retrying a job must not duplicate the same message or event.
- Email must remain disabled when SMTP is not configured, matching the existing mailer behavior.
- All schema changes must be safe for existing SQLite installations and the documented PostgreSQL backend.

---

### Task 1: Add campaign attribution and funnel-event storage

**Files:**
- Modify: `gateway/logic/db.py`
- Modify: `gateway/logic/accounts.py`
- Create: `gateway/logic/growth.py`
- Test: `gateway/tests/test_growth.py`

**Interfaces:**
- `growth.capture_event(conn, event_name: str, account_id: int | None = None, anonymous_id: str = "", properties: dict[str, str] | None = None) -> None`
- `growth.get_pending_lifecycle_actions(conn, now_iso: str) -> list[sqlite3.Row]`
- `growth.mark_lifecycle_action_sent(conn, action_id: int, sent_at: str) -> None`
- `accounts.update_acquisition_metadata(conn, account_id: int, metadata: dict[str, str]) -> None`

- [ ] **Step 1: Write failing tests** for event insertion, JSON property allowlisting, attribution persistence, unique event deduplication, and schema upgrade from an existing database.
- [ ] **Step 2: Run `python -m unittest gateway.tests.test_growth -v` and verify the new tests fail because the growth schema/API does not exist.**
- [ ] **Step 3: Add `acquisition_source`, `acquisition_medium`, `acquisition_campaign`, `acquisition_content`, and `anonymous_id` columns to `accounts`; add `growth_events` and `lifecycle_actions` tables with indexes and idempotency keys.**
- [ ] **Step 4: Implement the minimal pure logic in `gateway/logic/growth.py` with an explicit property allowlist and stable event idempotency key.**
- [ ] **Step 5: Run `python -m unittest gateway.tests.test_growth -v` and verify all tests pass.**

### Task 2: Capture attribution and server-side activation events

**Files:**
- Modify: `gateway/app.py`
- Modify: `gateway/provisioning.py`
- Modify: `tenant-app/api_server.py`
- Modify: `gateway/tests/test_client_ip.py` or create `gateway/tests/test_growth_routes.py`
- Test: `gateway/tests/test_growth_routes.py`

**Interfaces:**
- `growth.read_attribution_cookie(request: Request) -> dict[str, str]`
- `growth.set_attribution_cookie(response: Response, metadata: dict[str, str]) -> None`
- `POST /internal/growth/event` accepts an authenticated event payload from a tenant and returns `204`.

- [ ] **Step 1: Write failing route tests** for UTM capture on `/`, attribution transfer on `/register`, and rejection of unauthenticated internal events.
- [ ] **Step 2: Run `python -m unittest gateway.tests.test_growth_routes -v` and verify failure.**
- [ ] **Step 3: Capture only `utm_source`, `utm_medium`, `utm_campaign`, `utm_content`, `utm_term`, and a random anonymous ID in a short-lived first-party cookie.**
- [ ] **Step 4: Persist those values during registration and emit `landing_view`, `signup_started`, `signup_completed`, `tenant_provisioned`, `first_tenant_access`, and `first_sync_completed` events at the existing lifecycle boundaries.**
- [ ] **Step 5: Run the focused route tests plus `python -m unittest discover -s gateway/tests -t .` and verify no existing gateway tests regress.**

### Task 3: Add activation messaging and trial lifecycle worker

**Files:**
- Modify: `gateway/mailer.py`
- Create: `gateway/lifecycle_worker.py`
- Create: `scripts/run_lifecycle_worker.py`
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Test: `gateway/tests/test_lifecycle_worker.py`

**Interfaces:**
- `lifecycle_worker.build_actions(conn, now: datetime) -> int`
- `lifecycle_worker.send_due_actions(conn, now: datetime) -> int`
- `mailer.send_lifecycle_email(to_email: str, subject: str, body_text: str) -> bool`

- [ ] **Step 1: Write failing tests** for the activation sequence: welcome, API-not-connected reminder, first-result reminder, trial-ending reminders at 7/3/1 days, and payment-success suppression.
- [ ] **Step 2: Run `python -m unittest gateway.tests.test_lifecycle_worker -v` and verify failure.**
- [ ] **Step 3: Implement idempotent action generation from account/tenant status and recorded events; schedule no marketing message without `pdn_consent_at`.**
- [ ] **Step 4: Add plain-text Russian email templates with one CTA per message: connect WB, open the first report, or continue subscription.**
- [ ] **Step 5: Add a one-shot worker command and a Compose service/cron invocation using the existing control-plane database and SMTP configuration.**
- [ ] **Step 6: Run focused lifecycle tests and the full gateway test suite; verify SMTP-disabled mode performs no external send and exits cleanly.**

### Task 4: Add activation reporting to the admin dashboard

**Files:**
- Modify: `gateway/admin_routes.py`
- Modify: `gateway/templates/admin_dashboard.html`
- Test: `gateway/tests/test_admin.py`

**Interfaces:**
- `growth.get_funnel_summary(conn, since_iso: str) -> dict[str, int | float]`

- [ ] **Step 1: Write failing tests** for counts and conversion rates across landing views, signups, API connection, first sync, and paid accounts.
- [ ] **Step 2: Run the focused admin tests and verify failure.**
- [ ] **Step 3: Implement aggregate queries with zero-safe division and a fixed default window of the last 30 days.**
- [ ] **Step 4: Render a compact activation table and top acquisition-source table without exposing customer email addresses.**
- [ ] **Step 5: Run the full gateway suite and verify the admin page still requires the existing admin allowlist.**

### Task 5: Add referral attribution as a separate growth slice

**Files:**
- Modify: `gateway/logic/growth.py`
- Modify: `gateway/app.py`
- Modify: `gateway/templates/home.html`
- Modify: `gateway/templates/register.html`
- Test: `gateway/tests/test_referrals.py`

**Interfaces:**
- `growth.create_referral_code(conn, account_id: int) -> str`
- `growth.resolve_referral_code(conn, code: str) -> int | None`

- [ ] **Step 1: Write failing tests** for valid referral capture, self-referral rejection, and duplicate signup idempotency.
- [ ] **Step 2: Run `python -m unittest gateway.tests.test_referrals -v` and verify failure.**
- [ ] **Step 3: Add opaque referral codes and a `referral_attributions` table; never expose account IDs in URLs.**
- [ ] **Step 4: Add a restrained referral CTA after a user has reached first-sync activation, not on the anonymous landing page.**
- [ ] **Step 5: Run the full gateway suite and document the manual reward/anti-abuse policy before enabling financial rewards.**

## Verification Checklist

- [ ] `python -m unittest discover -s gateway/tests -t .`
- [ ] `python -m unittest discover -s tenant-app/tests -t .`
- [ ] `python -m compileall gateway tenant-app scripts`
- [ ] Existing registration, provisioning, billing, Telegram linking, password reset, and admin authorization tests remain green.
- [ ] A fresh local database and an upgraded copy of the existing SQLite schema both initialize successfully.
- [ ] SMTP-disabled execution creates no outbound connection and does not mark lifecycle actions as sent.
