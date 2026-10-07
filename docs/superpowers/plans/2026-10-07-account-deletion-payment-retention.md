# Account deletion and payment retention implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. In this session root owns delegation, commits and deployment; this document author makes no financial runtime changes.

**Goal:** Remove an account's operational identity and access without losing its payment/refund history or allowing deletion to race a new charge.

**Architecture:** Keep the original account ID as an inaccessible financial tombstone, rather than rebuilding all financial foreign keys. An additive lifecycle flag and durable payment-operation claim serialize deletion against checkout/recurring initiation. Late provider events update the retained financial rows only and cannot renew access or restore a deleted identity.

**Tech Stack:** Existing FastAPI gateway, SQLite/Postgres connection adapter, YooKassa client and isolated synthetic DB tests; no new dependencies.

**Spec:** `docs/P2_MOBILE_STORE_INVENTORY_2026-10-07.md`; actual public promise in `gateway/templates/privacy.html` §5 (payment/legal records retained for applicable legal periods).

## Global constraints

- No real deletion, payment, provider write or live financial data during tests.
- No new retention years, policy promise, automatic refund or legal interpretation.
- No commits/deploy by this agent; no role/provisioner/image/backup/P1 changes.
- Preserve amounts, provider IDs, payment IDs, refund→payment→subject links and duplicate-event guards.
- A transient network timeout is an unknown payment outcome, not evidence that no charge happened.
- A pending deletion must not create a new payment claim, including manual checkout.
- Scope both SQLite and Postgres; `?` adapter-compatible statements only.
- Original receipt data is not stored locally: the current YooKassa client sends customer email in the outbound request. Do not pretend this patch deletes provider-held receipts. Owner must determine permitted provider retention and deletion disclosures.

## Evidence and why a one-line patch is unsafe

`accounts.delete_account()` currently DELETEs accounts; both DB schemas CASCADE payments and refunds. An isolated synthetic DB went from1 payment to0 after deletion. `run_billing_cycle.charge_due_accounts()` selects due rows, closes the connection, and then calls create_payment using that stale row. The checkout route also calls the provider outside a durable operation claim. `apply_successful_payment()` can set status=active and restore saved payment_method_id after a late success event. Thus merely replacing DELETE by anonymizing email leaves both charge and account-restoration races.

## Files and ownership

Create `gateway/logic/account_lifecycle.py` for lifecycle/claim transitions only.
Modify `gateway/logic/db.py` for additive columns/table on both schemas.
Modify `gateway/logic/accounts.py` for lifecycle guards and tombstone finalization.
Modify `gateway/logic/billing.py`, `gateway/billing_routes.py`, `scripts/run_billing_cycle.py` for claims and non-resurrection settlement.
Modify only `/account/delete` in `gateway/app.py` for orchestration; keep P1 login/logout/middleware hunks unchanged.
Modify `gateway/admin_routes.py` only where account extension/restoration must reject tombstones.
Create `gateway/tests/test_account_lifecycle.py`, `test_deletion_payment_retention.py`, `test_billing_deletion_race.py`; adapt existing deletion tests to check operational erasure plus retained financial rows.

### Task1: durable lifecycle and claims

**Interfaces produced:**
`begin_payment(conn, account_id, operation_key) -> str` returns claimed operation ID or raises `LifecycleConflict`.
`mark_payment_outcome(conn, operation_id, state, provider_id='') -> None`; state is `completed`, `provider_pending` or `unknown`.
`request_deletion(conn, account_id) -> str`; returns `ready` or `blocked_payment`, preserving the deletion request in both cases.
`allow_account_mutation(account) -> bool`; false if deletion requested or finalized.

- [ ] Add `deletion_requested_at TEXT NOT NULL DEFAULT ''`, `deleted_at TEXT NOT NULL DEFAULT ''` to accounts idempotently through the existing SQLite/PG migration style.
- [ ] Add portable table:

```sql
CREATE TABLE IF NOT EXISTS billing_operation_claims (
 operation_id TEXT PRIMARY KEY,
 account_id INTEGER NOT NULL REFERENCES accounts(id),
 operation_key TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL,
 provider_id TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_billing_claim_account_state
 ON billing_operation_claims(account_id,state);
```

- [ ] Serialize every lifecycle/claim transition by touching the same account row before reading flags. This obtains a write/row lock within the caller's existing transaction on both SQLite and Postgres; it does not hold a DB transaction across provider I/O:

```python
conn.execute('UPDATE accounts SET updated_at=updated_at WHERE id=?',(account_id,))
account=conn.execute('SELECT * FROM accounts WHERE id=?',(account_id,)).fetchone()
```

- [ ] `begin_payment` rejects missing account, deletion_requested_at/deleted_at, canceled status, and any claimed/unknown/provider_pending operation; only then inserts a new claim. Identical operation_key returns its existing state and NEVER authorizes a second upstream call. Operation keys remain checkout intent IDs or deterministic recurring account+period keys.
- [ ] `request_deletion` sets deletion_requested_at and status=canceled atomically, so stale due-list rows cannot create claims. If unresolved operations exist, return blocked_payment; leave tenant and financial rows intact for reconciliation. Do not automatically expire unknown claims.
- [ ] Test two DB connections/threads racing request_deletion against begin_payment: either claim wins and deletion blocks, or deletion wins and provider calls remain0. Repeat initialization; records/flags unchanged.

### Task2: integrate all charge paths and settlements

**Consumes:** Task1 interfaces. **Produces:** every checkout/recurring request is protected by a durable claim, and financial-only late settlement.

- [ ] Write failing tests: stale recurring due-row followed by deletion emits no create_payment; checkout on deletion-requested account returns409; a request timeout marks unknown and future attempts with the same intent never call upstream.
- [ ] Add claim acquisition inside a committed DB transaction immediately before provider calls in `billing_checkout` and `charge_due_accounts`. Re-read the account under the lifecycle lock; do not use its pre-claim email/payment method snapshot for eligibility.
- [ ] After the provider returns, persist provider_id and financial payment row in one transaction, then mark claim completed/provider_pending. If process stops after external send but before this write, the unresolved claim remains blocked; reconcile instead of creating a new charge.
- [ ] On transport failure mark unknown using a classified reason, without response text, email, tokens or provider URLs in the claim/log. No automatic unknown→completed transition.
- [ ] Split `apply_successful_payment`/`apply_failed_payment` into recording financial status and changing subscription state. Always preserve payment/refund status; renew access/store payment_method ONLY when lifecycle flags are empty. `apply_refund` must also preserve financial linkage without reactivating a tombstone.
- [ ] Existing webhook authenticity/source verification stays unchanged. Verified provider events resolve a matching pending claim. A webhook for a deleted account does not recreate its tenant, identity or session.
- [ ] Implement reconciliation as a restricted administrative read of a known provider ID, with explicit outcome confirmation. Unknown creates without a provider ID require operator/provider investigation; don't invent a provider lookup-by-idempotence API or retry policy.
- [ ] Run `python -m pytest gateway/tests/test_billing.py gateway/tests/test_billing_routes_webhook.py gateway/tests/test_billing_deletion_race.py -q` using mocked provider calls.

### Task3: operational erasure and financial tombstone

**Interface produced:** `finalize_deletion(conn, account_id) -> None`, callable only after successful tenant removal and with no unresolved claim.

- [ ] Add a synthetic successful payment plus refund, sessions, reset/email tokens, tenant/team, feedback and growth data; verify deletion removes access/PII while preserving payment/refund/claim IDs and values.
- [ ] Confirm password/CSRF/УДАЛИТЬ before request_deletion. Return409 for blocked payment with an actionable reconciliation message. If provisioner removal fails, keep canceled deletion-requested state and return502; retry must be idempotent. Do not restore billing/access automatically.
- [ ] After provisioner removal, finalize one DB transaction: remove tenant_instances (cleans its membership/invitations/link codes), account sessions/reset/verification/email-change tokens, remaining memberships, feedback, attribution and trial-email records. Delete or sanitize structured metadata that may carry user PII; enumerate columns against schema instead of blindly keeping arbitrary JSON.
- [ ] Set email to `deleted-<account_id>-<random_nonce>@invalid.invalid`; clear password_hash/password_salt, payment_method_id, all consent/verification fields, period/grace data; set canceled and deleted_at. `created_at` and account ID remain only as financial subject evidence. No reversible copy of the old email/password is made.
- [ ] Guard authentication/recovery/team/admin extension/any start_trial path against lifecycle flags. Anonymous registration with the original freed email creates a fresh account ID and cannot inherit old payments/team access.
- [ ] `accounts.delete_account` becomes the safe tombstone finalizer for production call sites, or is replaced by an explicitly named finalize function and DELETE removed from production paths. Test-only hard-delete helpers must not be exported as a public route.
- [ ] Retained payment `error_message` becomes a controlled category if it contains arbitrary upstream content; do not destroy provider ID, amount, kind, status, timestamps, original ID or refund linkage. Receipt/audit fields not presently stored cannot be fabricated.
- [ ] Run lifecycle/deletion tests and existing account/password-reset/growth/admin tests. Add real-Postgres migration gate before deployment; SQLite-only tests cannot certify PG rollout.

### Task4: release and policy gate

- [ ] Run complete gateway tests, migration-on-existing synthetic DB twice, concurrent claim/deletion and late-webhook regressions.
- [ ] Review all PII-bearing child tables and financial event metadata. Record exactly what is retained, where, and how access is restricted. Owner/legal sets retention periods separately; do not schedule deletion of payment records in this patch.
- [ ] Prepare public deletion guidance only after the workflow is implemented: cancellation is immediate for new charges, unresolved payments are reconciled before finalization, provider-held receipt records may have separate retention.
- [ ] Root independently reviews the diff and deploys committed HEAD with the normal encrypted backup/restore gate. No real deletion/charge acceptance probe on prod.

## Acceptance matrix

| Case | Required outcome |
|---|---|
| Original successful payment + refund | Original financial IDs/amounts/status links remain after deletion |
| Concurrent checkout/recurring vs delete | No upstream charge starts after lifecycle deletion claim |
| In-flight timeout/unknown | Deletion remains blocked, no blind retry |
| Late payment success/refund | Financial records update; no account/tenant resurrection |
| Provisioner deletion failure | Data intact, canceled pending-deletion state, idempotent retry |
| Old password/session/reset link | No usable identity or access after finalization |
| Re-register original email | New account ID, no old financial/team linkage |
| Migration repeat SQLite+PG | Existing data unchanged; additive migration idempotent |

## Implementation status (source only)

The bounded source package is now prepared: additive lifecycle columns + claims;
checkout/recurring guard; account-lock ordering; blocked unknown deletion; verified
operation/amount/currency/provider binding; late webhook/refund financial-only
settlement; operational PII erasure with retained financial IDs; account/recovery/
admin-extension guards; administrative GET-only upstream reconciliation form.
Local full gateway verification:503 passed,3 skipped,203 subtests; the real PostgreSQL gate was subsequently run independently on the server.
Dedicated real-PG gate passed on isolated disposable server PostgreSQL:1 passed, with helper/network/tmpfs cleanup verified. Coordinated gateway+host-cron release remains required.
No production payment/deletion, commit or deployment was performed by this agent.

Retention is pseudonymization, not a claim of irreversible anonymous data:
provider IDs and financial subject IDs intentionally remain linked. No local
receipt-copy schema existed; original provider receipts remain provider-held.
Owner/legal review must determine lawful retention periods and disclosure of
provider/backups retention before the public deletion guidance is finalized.


Compatibility coverage also blocks pre-upgrade `payments.status=pending` without
claims. These have a separate administrator known-payment reconciliation action,
with verified UUID/account/amount/currency checks and no provider write. The old
host billing job must be drained before switching the gateway/cron pair. Source
cannot recover financial records already erased by earlier hard deletions.

## Independent-review revision

Review reproduced webhook-before-HTTP-return downgrade/duplicate-extension,
manual-period-extension-before-cron-lock, and failed-payment wrong-owner mutation.
These are corrected with terminal-preserving owner/amount-checked UPSERT, owned
pending-only settlement, and due/key eligibility read under the account lock.
Four controlled webhook/response interleavings plus the lock interleaving and
wrong-owner/terminal regressions passed locally (25 focused tests). The earlier
PostgreSQL artifact covers the pre-review snapshot only; a refreshed PG gate is
required once independent review is settled. No extra PG run/commit/deploy has
been made during this revision.

Refreshed post-review real PostgreSQL gate passed:3 tests in1.00s, including
observed row-lock wait/manual extension→zero charges and deletion/charge claim
concurrency. Source hashes match the current revised financial package. Isolated
helper/network/tmpfs/source cleanup was verified; no provider writes were invoked.
