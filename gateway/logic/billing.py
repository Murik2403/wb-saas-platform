"""Pure business logic for subscriptions: trial, payments, grace period.

Same rule as logic/accounts.py: zero imports of fastapi/httpx/the YooKassa
SDK. Everything that decides "does this account get access" or "is this
account due for a charge" lives here and is unit-tested; yookassa_client.py
and the FastAPI routes are thin wrappers that call into this module.

Subscription state lives on accounts.status (shared with logic/accounts.py
-- see ACCOUNT_STATUSES there) plus three extra columns:
  current_period_end -- trial end date until first payment, then paid-through date
  payment_method_id  -- YooKassa's saved payment method, for unattended recurring charges
  past_due_since      -- set when a charge fails or a trial lapses unpaid; starts
                         the grace-period countdown (config.GRACE_PERIOD_DAYS)

State machine:
    pending --(container healthy)--> trialing --(trial ends, no card)--> past_due
    trialing --(successful payment)--> active
    active --(recurring charge fails)--> past_due --(grace period expires)--> canceled
    past_due --(successful payment)--> active
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import config
from . import account_lifecycle, accounts, growth

PAYMENT_KINDS = {"initial", "recurring", "manual"}
PAYMENT_STATUSES = {"pending", "succeeded", "failed", "canceled"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# --------------------------------------------------------------------------
# Trial
# --------------------------------------------------------------------------

def start_trial(conn: sqlite3.Connection, account_id: int, trial_days: int = config.TRIAL_DAYS) -> None:
    if account_lifecycle.closed(account_lifecycle.lock_account(conn, account_id)):
        raise ValueError("Аккаунт удаляется или удалён")
    period_end = (_now() + timedelta(days=trial_days)).isoformat(timespec="seconds")
    conn.execute(
        "UPDATE accounts SET status='trialing', current_period_end=?, past_due_since='', updated_at=? WHERE id=?",
        (period_end, _now_iso(), int(account_id)),
    )
    growth.record_trial_started(conn, account_id, occurred_at=_now())


# --------------------------------------------------------------------------
# Payments
# --------------------------------------------------------------------------

def record_payment_attempt(
    conn: sqlite3.Connection,
    account_id: int,
    yookassa_payment_id: str,
    kind: str,
    amount_rub: float,
    status: str = "pending",
    period_start: str = "",
    period_end: str = "",
) -> int:
    if kind not in PAYMENT_KINDS:
        raise ValueError(f"Недопустимый тип платежа: {kind!r}")
    if status not in PAYMENT_STATUSES:
        raise ValueError(f"Недопустимый статус платежа: {status!r}")
    now = _now_iso()
    cur = conn.execute(
        """
        INSERT INTO payments(account_id, yookassa_payment_id, kind, amount_rub, status,
                              period_start, period_end, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(yookassa_payment_id) DO UPDATE SET
          status=CASE WHEN payments.status IN ('succeeded','failed','canceled') THEN payments.status ELSE excluded.status END,
          updated_at=CASE WHEN payments.status IN ('succeeded','failed','canceled') THEN payments.updated_at ELSE excluded.updated_at END
        WHERE payments.account_id=excluded.account_id AND payments.amount_rub=excluded.amount_rub
        """,
        (int(account_id), yookassa_payment_id, kind, amount_rub, status, period_start, period_end, now, now),
    )
    if cur.rowcount == 0:
        raise ValueError("Платёж уже зарегистрирован для другого аккаунта или суммы")
    row = conn.execute(
        "SELECT id FROM payments WHERE yookassa_payment_id=?", (yookassa_payment_id,)
    ).fetchone()
    return int(row["id"])


def list_payments(conn: sqlite3.Connection, account_id: int, limit: int = 50) -> list[sqlite3.Row]:
    """Return the account's newest payment attempts for billing history."""
    bounded = max(1, min(int(limit), 200))
    return conn.execute(
        "SELECT id, yookassa_payment_id, kind, amount_rub, status, period_start, period_end, created_at, updated_at "
        "FROM payments WHERE account_id=? ORDER BY created_at DESC LIMIT ?",
        (int(account_id), bounded),
    ).fetchall()


def apply_refund(conn: sqlite3.Connection, account_id: int, yookassa_payment_id: str, amount_rub: float, refund_id: str, status: str = "succeeded") -> None:
    """Record an idempotent YooKassa refund and revoke access for a full refund."""
    account = account_lifecycle.lock_account(conn, account_id)
    payment = conn.execute("SELECT id FROM payments WHERE account_id=? AND yookassa_payment_id=?", (int(account_id), yookassa_payment_id)).fetchone()
    if payment is None:
        raise ValueError("Платёж не найден")
    now = _now_iso()
    conn.execute(
        "INSERT INTO refunds(account_id, payment_id, yookassa_refund_id, amount_rub, status, created_at, updated_at) VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(yookassa_refund_id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at",
        (int(account_id), int(payment["id"]), refund_id, float(amount_rub), status, now, now),
    )
    if status == "succeeded":
        conn.execute("UPDATE payments SET status='canceled', updated_at=? WHERE id=?", (now, int(payment["id"])))
        if not account_lifecycle.closed(account):
            conn.execute("UPDATE accounts SET status='past_due', current_period_end=?, past_due_since=?, updated_at=? WHERE id=?", (now, now, now, int(account_id)))


def apply_successful_payment(
    conn: sqlite3.Connection,
    account_id: int,
    yookassa_payment_id: str,
    payment_method_id: str = "",
    period_days: int = config.BILLING_PERIOD_DAYS,
    *,
    is_test: bool = False,
) -> None:
    """Extends the paid-through date and clears any past_due state.

    Idempotent per yookassa_payment_id: YooKassa's webhook delivery is
    at-least-once (it retries on any non-2xx response, a timeout, or just
    its own duplicate-delivery behaviour), so this function WILL be called
    more than once for the same payment id in normal operation -- that must
    never extend current_period_end twice for one payment. Guarded below by
    checking the payments row's own status before doing anything; only a
    payment not already recorded as 'succeeded' actually extends the period.
    """
    account_lifecycle.lock_account(conn, account_id)
    owned_payment=conn.execute("SELECT status FROM payments WHERE yookassa_payment_id=? AND account_id=?",(yookassa_payment_id,int(account_id))).fetchone()
    if owned_payment is None:
        return
    account_lifecycle.settle_provider(conn, account_id, yookassa_payment_id, owned_payment["status"] in {"succeeded","failed","canceled"})
    # Idempotency as an atomic claim, not a read-then-write. YooKassa delivers
    # webhooks at-least-once and can retry them concurrently; a plain
    # "SELECT status; if succeeded return" guard lets two concurrent calls both
    # pass the check and each extend the period (60 days for one payment).
    # Flipping the row to 'succeeded' in one UPDATE ... WHERE status!='succeeded'
    # lets exactly one caller win (rowcount==1); the loser gets rowcount==0 and
    # returns without extending. SQLite's write lock (with the busy_timeout from
    # db.connect) serializes the two connections so the claim is atomic. If the
    # account lookup below fails, the whole transaction rolls back (db.connect
    # only commits on clean exit), un-claiming the payment too.
    claimed = conn.execute(
        "UPDATE payments SET status='succeeded', updated_at=? "
        "WHERE yookassa_payment_id=? AND account_id=? AND status='pending'",
        (_now_iso(), yookassa_payment_id, int(account_id)),
    )
    if claimed.rowcount == 0:
        # Provider test classification may arrive on a later, verified
        # webhook. It must still correct measurement despite billing's
        # idempotent early return, without extending access again.
        if is_test:
            payment = conn.execute(
                "SELECT id FROM payments WHERE yookassa_payment_id=? AND account_id=?",
                (yookassa_payment_id, int(account_id)),
            ).fetchone()
            if payment is not None:
                growth.record_payment_success(conn, account_id, int(payment["id"]), is_test=True, occurred_at=_now())
        return  # already applied, or no matching payment row -- nothing to extend

    account = accounts.get_account_by_id(conn, account_id, include_deleted=True)
    if account is None:
        raise ValueError(f"Аккаунт {account_id} не найден.")
    account_lifecycle.settle_provider(conn, account_id, yookassa_payment_id, True)
    if account_lifecycle.closed(account):
        return

    # Extend from the later of "now" or the current period end, so paying
    # early (or exactly on time) doesn't lose days, but a very late payment
    # doesn't grant a huge backdated bonus either.
    current_end = _parse_iso(account["current_period_end"]) or _now()
    base = max(current_end, _now())
    new_period_end = (base + timedelta(days=period_days)).isoformat(timespec="seconds")

    conn.execute(
        """
        UPDATE accounts
           SET status='active', current_period_end=?, past_due_since='',
               payment_method_id=COALESCE(NULLIF(?, ''), payment_method_id), updated_at=?
         WHERE id=?
        """,
        (new_period_end, payment_method_id, _now_iso(), int(account_id)),
    )
    payment = conn.execute(
        "SELECT id FROM payments WHERE yookassa_payment_id=? AND account_id=?",
        (yookassa_payment_id, int(account_id)),
    ).fetchone()
    if payment is not None:
        growth.record_payment_success(conn, account_id, int(payment["id"]), is_test=is_test, occurred_at=_now())


def admin_extend_period(conn: sqlite3.Connection, account_id: int, days: int) -> None:
    """Manual comp/goodwill extension from the admin panel -- same
    later-of-now-or-current-period-end extension rule as
    apply_successful_payment, but with no payment record backing it (no
    yookassa_payment_id to guard idempotency with, since this is a one-off
    operator action, not a retried webhook)."""
    account_lifecycle.lock_account(conn, account_id)
    account = accounts.get_account_by_id(conn, account_id)
    if account is None:
        raise ValueError(f"Аккаунт {account_id} не найден.")
    if account_lifecycle.closed(account):
        raise ValueError("Аккаунт удаляется или удалён")

    current_end = _parse_iso(account["current_period_end"]) or _now()
    base = max(current_end, _now())
    new_period_end = (base + timedelta(days=int(days))).isoformat(timespec="seconds")

    conn.execute(
        "UPDATE accounts SET status='active', current_period_end=?, past_due_since='', updated_at=? WHERE id=?",
        (new_period_end, _now_iso(), int(account_id)),
    )


def apply_failed_payment(conn: sqlite3.Connection, account_id: int, yookassa_payment_id: str, error: str = "") -> None:
    locked_account = account_lifecycle.lock_account(conn, account_id)
    if account_lifecycle.closed(locked_account):
        error = ""
    claimed=conn.execute(
        "UPDATE payments SET status='failed', error_message=?, updated_at=? WHERE yookassa_payment_id=? AND account_id=? AND status='pending'",
        (error, _now_iso(), yookassa_payment_id, int(account_id)),
    )
    if claimed.rowcount == 0:
        return
    account = accounts.get_account_by_id(conn, account_id, include_deleted=True)
    if account is None:
        return
    account_lifecycle.settle_provider(conn, account_id, yookassa_payment_id, True)
    if account_lifecycle.closed(account):
        return

    # Only start the grace-period clock the first time; don't keep resetting
    # past_due_since on every retry, or a client could stay in grace forever
    # by having their card decline repeatedly.
    if not account["past_due_since"]:
        conn.execute(
            "UPDATE accounts SET status='past_due', past_due_since=?, updated_at=? WHERE id=?",
            (_now_iso(), _now_iso(), int(account_id)),
        )
    else:
        conn.execute(
            "UPDATE accounts SET status='past_due', updated_at=? WHERE id=?",
            (_now_iso(), int(account_id)),
        )


# --------------------------------------------------------------------------
# Daily billing cycle (see scripts/run_billing_cycle.py)
# --------------------------------------------------------------------------

def accounts_due_for_recurring_charge(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Trialing/active accounts whose period has ended and who have a saved
    card on file -- these should be auto-charged for the next period."""
    now = _now_iso()
    return conn.execute(
        """
        SELECT * FROM accounts
         WHERE status IN ('trialing', 'active')
           AND current_period_end <> '' AND current_period_end <= ?
           AND payment_method_id <> ''
        """,
        (now,),
    ).fetchall()


def trials_expiring_without_payment(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Trialing accounts whose trial ended with no card on file -- these
    can't be auto-charged, so they go straight to past_due (grace period)
    to prompt a manual checkout instead."""
    now = _now_iso()
    return conn.execute(
        """
        SELECT * FROM accounts
         WHERE status='trialing'
           AND current_period_end <> '' AND current_period_end <= ?
           AND payment_method_id = ''
        """,
        (now,),
    ).fetchall()


def expire_trial_without_payment(conn: sqlite3.Connection, account_id: int) -> None:
    conn.execute(
        "UPDATE accounts SET status='past_due', past_due_since=?, updated_at=? WHERE id=? AND past_due_since='' AND deletion_requested_at='' AND deleted_at=''",
        (_now_iso(), _now_iso(), int(account_id)),
    )


def accounts_past_grace_period(conn: sqlite3.Connection, grace_days: int = config.GRACE_PERIOD_DAYS) -> list[sqlite3.Row]:
    cutoff = (_now() - timedelta(days=grace_days)).isoformat(timespec="seconds")
    return conn.execute(
        """
        SELECT * FROM accounts
         WHERE status='past_due' AND past_due_since <> '' AND past_due_since <= ?
        """,
        (cutoff,),
    ).fetchall()


def cancel_account(conn: sqlite3.Connection, account_id: int) -> None:
    accounts.set_account_status(conn, account_id, "canceled")


def days_left_in_grace_period(account: sqlite3.Row | dict[str, Any], grace_days: int = config.GRACE_PERIOD_DAYS) -> int:
    """For display: "your access ends in N days unless you update payment"."""
    past_due_since = _parse_iso(account["past_due_since"]) if account["past_due_since"] else None
    if past_due_since is None:
        return grace_days
    elapsed = (_now() - past_due_since).days
    return max(0, grace_days - elapsed)
