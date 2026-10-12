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
KEY_LIFETIME = timedelta(hours=24)


class StaleIntent(Exception):
    """The matching open intent is too old to resend safely; it must be reconciled with the provider first."""

    def __init__(self, intent_id: str):
        super().__init__("Возврат запрошен давно и не подтверждён; сначала сверьте его с YooKassa")
        self.intent_id = intent_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp() -> str:
    return _now().isoformat(timespec="seconds")


def initialize(conn, *, postgres=False) -> None:
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
    for column, definition in (("provider_description", "TEXT NOT NULL DEFAULT ''"),
                               ("attempt_count", "INTEGER NOT NULL DEFAULT 1")):
        if postgres:
            conn.execute(f"ALTER TABLE refund_intents ADD COLUMN IF NOT EXISTS {column} {definition}")
        elif column not in {row[1] for row in conn.execute("PRAGMA table_info(refund_intents)")}:
            conn.execute(f"ALTER TABLE refund_intents ADD COLUMN {column} {definition}")
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
        created = _date(intent["created_at"])
    except (ValueError, TypeError):
        return False
    return _now() - created < RESUME_WINDOW


def _date(value) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None
    if parsed is None or parsed.tzinfo is None:
        raise ValueError("Дата возврата не подтверждена")
    return parsed.astimezone(timezone.utc)


def claim(conn, account_id: int, payment, requested_cents: int | None, cents_of: Callable[[Any], int],
          requested_by: int = 0, request_ref: str = "") -> tuple[str, int, str]:
    """Reserve one refund attempt and commit-ready it. Returns (intent_id, amount_cents, mode).

    mode: 'new' (send it), 'resumed' (an open attempt: send it again with the same key) or 'done' (the same
    ``request_ref`` already produced a booked refund: send nothing).
    The caller must hold the account lock and commit before calling the provider. ``requested_cents`` of None means
    "everything still refundable". A ``request_ref`` makes a repeated call exactly the same refund however late it comes.
    """
    request_ref = (request_ref or "").strip()
    if len(request_ref) > 64:
        raise ValueError("request_id длиннее 64 символов")
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
                conn.execute("UPDATE refund_intents SET attempt_count=attempt_count+1 WHERE intent_id=?", (same["intent_id"],))
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
        if request_ref and existing["request_ref"] and request_ref != existing["request_ref"]:
            raise ValueError("Открыта другая попытка той же суммы; повторите её исходный request_id")
        if not _age_ok(existing):
            raise StaleIntent(existing["intent_id"])
        if request_ref and not existing["request_ref"]:
            conn.execute("UPDATE refund_intents SET request_ref=? WHERE intent_id=?", (request_ref, existing["intent_id"]))
        conn.execute("UPDATE refund_intents SET attempt_count=attempt_count+1 WHERE intent_id=?", (existing["intent_id"],))
        return existing["intent_id"], int(existing["amount_cents"]), "resumed"

    amount = remaining if requested_cents is None else requested_cents
    if amount <= 0 or amount > remaining:
        raise ValueError("Возврат превышает доступный остаток платежа")
    intent_id = secrets.token_hex(16)
    stamp = _stamp()
    conn.execute(
        "INSERT INTO refund_intents(intent_id,account_id,payment_id,provider_payment_id,amount_cents,state,requested_by,request_ref,created_at,updated_at,provider_description) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (intent_id, int(account_id), int(payment["id"]), payment["yookassa_payment_id"], amount, "claimed", int(requested_by), request_ref, stamp, stamp,
         f"MARKETSHELPER refund {intent_id}"))
    return intent_id, amount, "new"


def _set(conn, intent_id: str, state: str, error: str = "", refund_id: str = "") -> None:
    conn.execute(
        "UPDATE refund_intents SET state=?, error=?, provider_refund_id=CASE WHEN ?!='' THEN ? ELSE provider_refund_id END, updated_at=? "
        "WHERE intent_id=? AND state IN ('claimed','unknown')",
        (state, error[:300], refund_id, refund_id, _stamp(), intent_id))


def mark_unknown(conn, intent_id: str, error: str) -> None:
    _set(conn, intent_id, "unknown", error)


def mark_rejected(conn, intent_id: str, error: str, *, authoritative=False) -> None:
    # A refusal of a later/parallel request says nothing about the first call.
    # Only the first, sole call's definitive failure or a verified cancellation can release money.
    conn.execute("UPDATE refund_intents SET state=CASE WHEN ? OR (state='claimed' AND attempt_count=1) "
                 "THEN 'rejected' ELSE 'unknown' END,error=?,updated_at=? "
                 "WHERE intent_id=? AND state IN ('claimed','unknown')",
                 (bool(authoritative), error[:300], _stamp(), intent_id))


def mark_bound(conn, intent_id: str, refund_id: str) -> None:
    other = conn.execute("SELECT intent_id FROM refund_intents WHERE provider_refund_id=? AND intent_id<>?",
                         (refund_id, intent_id)).fetchone()
    if other is not None:
        raise ValueError("Возврат уже связан с другой попыткой")
    _set(conn, intent_id, "bound", "", refund_id)


def bind_from_webhook(conn, payment_id: int, cents: int, refund_id: str, refund: dict | None = None) -> None:
    """Only an exact persisted provider marker can close an open intent; no amount-only guessing."""
    if not isinstance(refund, dict) or not refund.get("description"):
        return  # Legacy/unmarked money is booked normally but its uncertain intent needs manual reconciliation.
    if conn.execute("SELECT 1 FROM refund_intents WHERE provider_refund_id=?", (refund_id,)).fetchone():
        return  # Duplicate/old webhook cannot consume the next equal-amount intent.
    row = conn.execute("SELECT * FROM refund_intents WHERE payment_id=? AND amount_cents=? "
                       "AND provider_description=? AND state IN ('claimed','unknown')",
                       (int(payment_id), int(cents), refund["description"])).fetchone()
    if row is not None:
        try:
            if (refund.get("payment_id") != row["provider_payment_id"]
                    or (refund.get("amount") or {}).get("currency") != "RUB"
                    or _date(refund.get("created_at")) < _date(row["created_at"])):
                return
        except (ValueError, TypeError):
            return
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
    created = _date(intent["created_at"])
    matches, ambiguous = [], False
    for refund in provider_refunds:
        try:
            if (not isinstance(refund, dict) or not isinstance(refund.get("id"), str) or not refund["id"]
                    or refund.get("payment_id") != intent["provider_payment_id"]
                    or refund.get("status") not in {"pending", "succeeded", "canceled"}
                    or (refund.get("amount") or {}).get("currency") != "RUB"):
                raise ValueError("Сведения возврата у провайдера не подтверждены")
            cents = cents_of(refund["amount"].get("value"))
            refund_created = _date(refund.get("created_at"))
        except (TypeError, AttributeError, ValueError) as exc:
            raise ValueError("Сведения возврата у провайдера не подтверждены; резерв сохранён") from exc
        if cents != int(intent["amount_cents"]):
            continue
        exact = bool(intent["provider_description"] and refund.get("description") == intent["provider_description"])
        if exact:
            if refund_created < created:
                raise ValueError("Дата связанного возврата раньше попытки; резерв сохранён")
            matches.append(refund)
        elif refund["id"] not in known and refund_created >= created and refund["status"] != "canceled":
            ambiguous = True  # Unmarked/manual/legacy same-amount refund must not be guessed away.
    if ambiguous:
        raise ValueError("Есть несопоставленный возврат той же суммы; требуется ручная сверка, резерв сохранён")
    if len(matches) == 1:
        found = matches[0]
        if found["status"] == "canceled":
            mark_rejected(conn, intent_id, "verified provider cancellation", authoritative=True)
            return "rejected", found if found["id"] not in known else None
        mark_bound(conn, intent_id, found["id"])
        return "bound", found if found["id"] not in known else None
    if len(matches) > 1:
        raise ValueError("У YooKassa несколько подходящих возвратов; сверьте вручную")
    if _now() - created < KEY_LIFETIME:
        return "unknown", None
    if _now() - created >= timedelta(days=1095):
        raise ValueError("Попытка старше доступной истории провайдера; требуется ручная сверка")
    _set(conn, intent_id, "stale", "no refund at provider after key window")
    return "stale", None
