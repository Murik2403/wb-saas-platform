"""Durable charge/deletion ordering and inaccessible financial tombstones."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import secrets
from . import erasure_ledger

class LifecycleConflict(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def closed(account):
    if account is None:
        return True
    row = dict(account)
    if row.get("deletion_requested_at") or row.get("deleted_at"):
        return True
    try:
        return erasure_ledger.is_erased(row)
    except erasure_ledger.LedgerUnavailable:
        # Enabled but inaccessible/corrupt guard must never grant access.
        return True


def initialize(conn, *, postgres=False):
    for column in ("deletion_requested_at", "deleted_at"):
        if postgres:
            conn.execute(f"ALTER TABLE accounts ADD COLUMN IF NOT EXISTS {column} TEXT NOT NULL DEFAULT ''")
        elif column not in {row[1] for row in conn.execute("PRAGMA table_info(accounts)")}:
            conn.execute(f"ALTER TABLE accounts ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
    conn.execute("""CREATE TABLE IF NOT EXISTS billing_operation_claims (
        operation_id TEXT PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id),
        operation_key TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
        provider_id TEXT NOT NULL DEFAULT '', expected_amount_rub TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    if postgres:
        conn.execute("ALTER TABLE billing_operation_claims ADD COLUMN IF NOT EXISTS expected_amount_rub TEXT NOT NULL DEFAULT ''")
    elif "expected_amount_rub" not in {row[1] for row in conn.execute("PRAGMA table_info(billing_operation_claims)")}:
        conn.execute("ALTER TABLE billing_operation_claims ADD COLUMN expected_amount_rub TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_billing_claim_account_state ON billing_operation_claims(account_id,state)")


def lock_account(conn, account_id):
    # Same write/row lock on SQLite and PostgreSQL; the caller commits BEFORE I/O.
    conn.execute("UPDATE accounts SET updated_at=updated_at WHERE id=?", (int(account_id),))
    return conn.execute("SELECT * FROM accounts WHERE id=?", (int(account_id),)).fetchone()


def begin_payment(conn, account_id, operation_key, *, allow_canceled=False, expected_amount_rub=""):
    account = lock_account(conn, account_id)
    if closed(account) or (account["status"] == "canceled" and not allow_canceled):
        raise LifecycleConflict("Аккаунт удаляется или недоступен для оплаты")
    if conn.execute("SELECT 1 FROM billing_operation_claims WHERE operation_key=?", (operation_key,)).fetchone():
        raise LifecycleConflict("Эта платёжная операция уже зарегистрирована")
    if unresolved(conn, account_id):
        raise LifecycleConflict("Предыдущая платёжная операция ещё не сверена")
    operation_id = secrets.token_hex(16)
    stamp = now()
    conn.execute("INSERT INTO billing_operation_claims(operation_id,account_id,operation_key,state,provider_id,expected_amount_rub,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                 (operation_id,int(account_id),operation_key,"claimed","",expected_amount_rub,stamp,stamp))
    return operation_id


def unresolved(conn, account_id):
    claim=conn.execute("SELECT 1 FROM billing_operation_claims WHERE account_id=? AND state!='completed' LIMIT 1", (int(account_id),)).fetchone()
    pending=conn.execute("SELECT 1 FROM payments WHERE account_id=? AND status='pending' LIMIT 1",(int(account_id),)).fetchone()
    return bool(claim or pending)


def mark_payment_outcome(conn, operation_id, state, provider_id=""):
    if state not in {"completed", "provider_pending", "unknown"}:
        raise ValueError("Invalid payment operation state")
    conn.execute("UPDATE billing_operation_claims SET state=CASE WHEN state='completed' THEN state ELSE ? END,provider_id=CASE WHEN ?!='' THEN ? ELSE provider_id END,updated_at=? WHERE operation_id=?",
                 (state,provider_id,provider_id,now(),operation_id))


def settle_provider(conn, account_id, provider_id, terminal):
    conn.execute("UPDATE billing_operation_claims SET state=?,updated_at=? WHERE account_id=? AND provider_id=?",
                 ("completed" if terminal else "provider_pending",now(),int(account_id),provider_id))


def request_deletion(conn, account_id):
    account = lock_account(conn, account_id)
    if account is None:
        raise LifecycleConflict("Аккаунт не найден")
    try:
        erasure_ledger.record(account)
    except erasure_ledger.LedgerUnavailable:
        raise LifecycleConflict("Реестр удаления недоступен; повторите позже") from None
    conn.execute("UPDATE accounts SET deletion_requested_at=CASE WHEN deletion_requested_at='' THEN ? ELSE deletion_requested_at END,status='canceled',updated_at=? WHERE id=?", (now(),now(),int(account_id)))
    return "blocked_payment" if unresolved(conn,account_id) else "ready"


def finalize_deletion(conn, account_id):
    account=lock_account(conn,account_id)
    if account is None:
        return
    if unresolved(conn,account_id):
        raise LifecycleConflict("Платёжная операция ещё не сверена")
    if dict(account).get("deleted_at"):
        return
    try:
        erasure_ledger.record(account, finalized=True)
    except erasure_ledger.LedgerUnavailable:
        raise LifecycleConflict("Реестр удаления недоступен; повторите позже") from None
    # Fixed tables only; provider IDs/amounts/refund links are intentionally retained.
    conn.execute("DELETE FROM tenant_instances WHERE account_id=?",(int(account_id),))
    for table in ("sessions","tenant_memberships","telegram_link_codes","password_reset_tokens",
                  "email_verification_tokens","email_change_tokens","product_feedback","growth_events",
                  "growth_sources","trial_emails","trial_email_optouts"):
        conn.execute(f"DELETE FROM {table} WHERE account_id=?",(int(account_id),))
    # Invitations to the former identity must not transfer to a re-registration.
    conn.execute("DELETE FROM tenant_invitations WHERE email=?",(account["email"],))
    conn.execute("UPDATE payments SET error_message='' WHERE account_id=?",(int(account_id),))
    conn.execute("UPDATE accounts SET email=?,password_hash='',password_salt='',status='canceled',current_period_end='',payment_method_id='',past_due_since='',email_verified_at='',pdn_consent_at='',pdn_policy_version='',marketing_consent_at='',deletion_requested_at=CASE WHEN deletion_requested_at='' THEN ? ELSE deletion_requested_at END,deleted_at=?,updated_at=? WHERE id=?",
                 (f"deleted-{account_id}-{secrets.token_hex(12)}@invalid.invalid",now(),now(),now(),int(account_id)))


def bind_verified_provider(conn, account_id, operation_id, payment):
    """Only a freshly verified merchant API response may enter this function."""
    account=lock_account(conn,account_id)
    row=conn.execute("SELECT * FROM billing_operation_claims WHERE operation_id=? AND account_id=?",(operation_id,int(account_id))).fetchone()
    if account is None or row is None:
        raise LifecycleConflict("Платёжная операция не найдена")
    payment_id=payment.get("id")
    metadata=payment.get("metadata") or {}
    amount=payment.get("amount") or {}
    try:
        valid_amount=bool(row["expected_amount_rub"]) and Decimal(str(amount.get("value"))) == Decimal(row["expected_amount_rub"])
    except (InvalidOperation,ValueError,TypeError):
        valid_amount=False
    if not isinstance(payment_id,str) or not payment_id or metadata.get("mh_operation_id")!=operation_id or str(metadata.get("account_id"))!=str(account_id) or amount.get("currency")!="RUB" or not valid_amount:
        raise LifecycleConflict("Данные платежа не соответствуют операции")
    if row["provider_id"] and row["provider_id"]!=payment_id:
        raise LifecycleConflict("Операция уже связана с другим платежом")
    existing=conn.execute("SELECT account_id FROM payments WHERE yookassa_payment_id=?",(payment_id,)).fetchone()
    if existing is not None and int(existing["account_id"])!=int(account_id):
        raise LifecycleConflict("Платёж относится к другому аккаунту")
    mark_payment_outcome(conn,operation_id,"completed" if payment.get("status") in {"succeeded","canceled"} else "provider_pending",payment_id)
    return dict(row)


def require_open(conn, account_id):
    account=lock_account(conn,account_id)
    if closed(account):
        raise LifecycleConflict("Аккаунт удаляется или удалён")
    return account
