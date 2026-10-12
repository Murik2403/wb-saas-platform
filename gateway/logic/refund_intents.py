"""Durable idempotency for outgoing refunds.

An outgoing refund moves real money, so the *intent* is written and committed before the provider is called, and its
id is the provider's Idempotence-Key. Whatever happens next (timeout, crash, double click, retry after a restart)
the same intent is resumed with the same key, so the provider returns the refund it already made instead of making a
second one, and the amount stays reserved against the payment until the outcome is known.

States of an intent (``refund_intents.state``):
  claimed   written before the call; nothing known yet
  unknown   the call ended without a trustworthy answer (timeout, 5xx, unreadable reply): the refund may exist
  bound     the provider's refund is verified and recorded in ``refunds`` (terminal)
  rejected  the provider clearly refused, nothing was refunded (terminal, the reservation is released)
  stale     an unknown intent older than the provider's key lifetime that was checked against the provider's own list
            and found to have produced no refund (terminal, released)

Open intents (claimed/unknown) count as reserved money, together with pending/succeeded rows of ``refunds``.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

OPEN_STATES = ("claimed", "unknown")
# YooKassa honours an Idempotence-Key for 24 hours; stay clear of the edge. Older attempts are reconciled, never resent.
RESUME_WINDOW = timedelta(hours=23)


class StaleIntent(Exception):
    """The matching open intent is too old to resend safely; it must be reconciled with the provider first."""

    def __init__(self, intent_id: str):
        super().__init__("Возврат запрошен давно и не подтверждён; сначала сверьте его с YooKassa")
        self.intent_id = intent_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp() -> str:
    return _now().isoformat(timespec="seconds")


def initialize(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS refund_intents (
        intent_id TEXT PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        payment_id INTEGER NOT NULL REFERENCES payments(id),
        provider_payment_id TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        state TEXT NOT NULL,
        provider_refund_id TEXT NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT '',
        requested_by INTEGER NOT NULL DEFAULT 0,
        request_ref TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_refund_intents_payment ON refund_intents(payment_id, state)")
    # The caller's own request id: the same id on the same payment is the same refund, whenever it is repeated.
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_refund_intents_ref ON refund_intents(payment_id, request_ref) "
                 "WHERE request_ref != ''")
    # A second open attempt for the same payment and amount cannot exist, even if two requests race past the lock.
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_refund_intents_open ON refund_intents(payment_id, amount_cents) "
                 "WHERE state IN ('claimed','unknown')")


def open_intents(conn, payment_id: int) -> list[Any]:
    return conn.execute("SELECT * FROM refund_intents WHERE payment_id=? AND state IN ('claimed','unknown') ORDER BY created_at",
                        (int(payment_id),)).fetchall()


def get(conn, intent_id: str):
    return conn.execute("SELECT * FROM refund_intents WHERE intent_id=?", (intent_id,)).fetchone()


def _age_ok(intent) -> bool:
    try:
        created = datetime.fromisoformat(intent["created_at"])
    except ValueError:
        return False
    return _now() - created < RESUME_WINDOW


def claim(conn, account_id: int, payment, requested_cents: int | None, cents_of: Callable[[Any], int],
          requested_by: int = 0, request_ref: str = "") -> tuple[str, int, str]:
    """Reserve one refund attempt and commit-ready it. Returns (intent_id, amount_cents, mode).

    mode: 'new' (send it), 'resumed' (an open attempt: send it again with the same key) or 'done' (the same
    ``request_ref`` already produced a booked refund: send nothing).
    The caller must hold the account lock and commit before calling the provider. ``requested_cents`` of None means
    "everything still refundable". A ``request_ref`` makes a repeated call exactly the same refund however late it comes.
    """
    request_ref = (request_ref or "").strip()[:64]
    if request_ref:
        same = conn.execute("SELECT * FROM refund_intents WHERE payment_id=? AND request_ref=?", (payment["id"], request_ref)).fetchone()
        if same is not None:
            if requested_cents is not None and int(same["amount_cents"]) != requested_cents:
                raise ValueError("Этот request_id уже использован для другой суммы")
            if same["state"] == "bound":
                return same["intent_id"], int(same["amount_cents"]), "done"
            if same["state"] in OPEN_STATES:
                if not _age_ok(same):
                    raise StaleIntent(same["intent_id"])
                return same["intent_id"], int(same["amount_cents"]), "resumed"
            raise ValueError("Этот request_id уже использован: попытка закрыта, возьмите новый")
    price = cents_of(payment["amount_rub"])
    settled = sum(cents_of(row["amount_rub"]) for row in conn.execute(
        "SELECT amount_rub FROM refunds WHERE payment_id=? AND status IN ('pending','succeeded')", (payment["id"],)).fetchall())
    open_rows = open_intents(conn, payment["id"])
    reserved_open = sum(int(row["amount_cents"]) for row in open_rows)
    remaining = price - settled - reserved_open

    existing = None
    if requested_cents is None and remaining <= 0 and len(open_rows) == 1:
        existing = open_rows[0]  # retrying the refund that is already in flight
    elif requested_cents is not None:
        existing = next((row for row in open_rows if int(row["amount_cents"]) == requested_cents), None)
    if existing is not None:
        if not _age_ok(existing):
            raise StaleIntent(existing["intent_id"])
        if request_ref and not existing["request_ref"]:
            conn.execute("UPDATE refund_intents SET request_ref=? WHERE intent_id=?", (request_ref, existing["intent_id"]))
        return existing["intent_id"], int(existing["amount_cents"]), "resumed"

    amount = remaining if requested_cents is None else requested_cents
    if amount <= 0 or amount > remaining:
        raise ValueError("Возврат превышает доступный остаток платежа")
    intent_id = secrets.token_hex(16)
    stamp = _stamp()
    conn.execute(
        "INSERT INTO refund_intents(intent_id,account_id,payment_id,provider_payment_id,amount_cents,state,requested_by,request_ref,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (intent_id, int(account_id), int(payment["id"]), payment["yookassa_payment_id"], amount, "claimed", int(requested_by), request_ref, stamp, stamp))
    return intent_id, amount, "new"


def _set(conn, intent_id: str, state: str, error: str = "", refund_id: str = "") -> None:
    conn.execute(
        "UPDATE refund_intents SET state=?, error=?, provider_refund_id=CASE WHEN ?!='' THEN ? ELSE provider_refund_id END, updated_at=? "
        "WHERE intent_id=? AND state IN ('claimed','unknown')",
        (state, error[:300], refund_id, refund_id, _stamp(), intent_id))


def mark_unknown(conn, intent_id: str, error: str) -> None:
    _set(conn, intent_id, "unknown", error)


def mark_rejected(conn, intent_id: str, error: str) -> None:
    _set(conn, intent_id, "rejected", error)


def mark_bound(conn, intent_id: str, refund_id: str) -> None:
    _set(conn, intent_id, "bound", "", refund_id)


def bind_from_webhook(conn, payment_id: int, cents: int, refund_id: str) -> None:
    """A verified refund notification closes the oldest open intent for the same payment and amount."""
    row = conn.execute(
        "SELECT intent_id FROM refund_intents WHERE payment_id=? AND amount_cents=? AND state IN ('claimed','unknown') "
        "ORDER BY created_at LIMIT 1", (int(payment_id), int(cents))).fetchone()
    if row is not None:
        mark_bound(conn, row["intent_id"], refund_id)


def reconcile(conn, intent_id: str, provider_refunds: list[dict], cents_of: Callable[[Any], int]) -> tuple[str, dict | None]:
    """Settle an old open intent against the provider's own list for its payment (never resends the request).

    Returns (new state, refund). 'bound' with a refund: the provider has a matching refund that is not booked here yet,
    the caller must book it (billing.apply_refund) in the same transaction. 'bound' with None: it is already booked.
    'stale': the provider has none and the key window is over, so nothing was refunded. 'unknown': still inside the
    window, resume the intent instead.
    """
    intent = get(conn, intent_id)
    if intent is None or intent["state"] not in OPEN_STATES:
        raise ValueError("Попытка возврата не найдена или уже закрыта")
    known = {row["yookassa_refund_id"] for row in conn.execute(
        "SELECT yookassa_refund_id FROM refunds WHERE payment_id=?", (intent["payment_id"],)).fetchall()}
    matches, recorded = [], []
    for refund in provider_refunds:
        try:
            same = (refund.get("payment_id") == intent["provider_payment_id"]
                    and refund.get("status") in {"pending", "succeeded"}
                    and cents_of((refund.get("amount") or {}).get("value")) == int(intent["amount_cents"]))
        except ValueError:
            same = False
        if same:
            (recorded if refund.get("id") in known else matches).append(refund)
    if len(matches) == 1:
        mark_bound(conn, intent_id, matches[0]["id"])
        return "bound", matches[0]
    if len(matches) > 1:
        raise ValueError("У YooKassa несколько подходящих возвратов; сверьте вручную")
    if recorded:  # the refund exists and is already booked (webhook came first): just close the attempt
        mark_bound(conn, intent_id, recorded[0]["id"])
        return "bound", None
    if _age_ok(intent):
        return "unknown", None
    _set(conn, intent_id, "stale", "no refund at provider after key window")
    return "stale", None
