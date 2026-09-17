"""Pure business logic for the gateway: accounts, passwords, sessions, and
the tenant-provisioning state machine.

Deliberately has ZERO imports of fastapi/docker/uvicorn/httpx. The FastAPI
routes and Docker orchestration are thin wrappers around these functions; if
a bug exists in "who can log in" or "whose data is whose", it should be
catchable here, not only by manually clicking through the running site.

New records use Argon2id. Legacy PBKDF2-HMAC-SHA256 records remain valid and
are upgraded only after their owner proves knowledge of the password.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type

import config

PBKDF2_ITERATIONS = 260_000
SESSION_TOKEN_BYTES = 32
SESSION_TTL_HOURS = 24 * 30  # 30 days
EMAIL_VERIFICATION_TTL_HOURS = 24
ARGON2_HASHER = PasswordHasher(
    time_cost=2,
    memory_cost=19 * 1024,
    parallelism=1,
    type=Type.ID,
)
# A valid Argon2id record used only when an email has no account. Verifying
# against it keeps the negative path comparable to a wrong-password attempt
# and avoids leaking account existence through the hashing algorithm's cost.
DUMMY_PASSWORD_SALT = ""
DUMMY_PASSWORD_HASH = ARGON2_HASHER.hash("not-a-real-account-password")

ACCOUNT_STATUSES = {"pending", "trialing", "active", "past_due", "canceled"}
# trialing/active/past_due all grant dashboard access -- past_due is a
# grace period after a failed or missing payment (see logic/billing.py),
# not an immediate lockout. Only "pending" (still provisioning) and
# "canceled" (grace period expired, or the client cancelled) are blocked.
ACCESS_ALLOWED_STATUSES = {"trialing", "active", "past_due"}

TENANT_STATUSES = {"provisioning", "running", "stopped", "failed"}
TENANT_ROLES = {"owner", "manager", "viewer"}
TENANT_INVITATION_TTL_DAYS = 7

# This value is stored with each registration consent so the operator can
# identify the policy edition that was presented to that account.
PDN_POLICY_VERSION = "2026-09-11"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SLUG_CHARS_RE = re.compile(r"[^a-z0-9-]+")
COMMON_WEAK_PASSWORDS = {
    "password",
    "12345678",
    "123456789",
    "1234567890",
    "qwerty",
    "qwertyui",
    "123456",
    "1234567",
    "12345678910",
    "admin",
    "welcome",
    "letmein",
    "iloveyou",
    "abc123",
    "11111111",
    "00000000",
    "пароль",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------

def _hash_legacy_pbkdf2(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS).hex()


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """Return a new Argon2id PHC record, or a legacy PBKDF2 record for verification."""
    if salt is not None:
        return _hash_legacy_pbkdf2(password, salt), salt.hex()
    return ARGON2_HASHER.hash(password), ""


def verify_password(password: str, password_hash_hex: str, salt_hex: str) -> bool:
    if password_hash_hex.startswith("$argon2id$"):
        try:
            return bool(ARGON2_HASHER.verify(password_hash_hex, password))
        except (InvalidHashError, VerificationError):
            return False
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    candidate = _hash_legacy_pbkdf2(password, salt)
    return hmac.compare_digest(candidate, password_hash_hex)


def needs_password_rehash(password_hash: str) -> bool:
    """Whether a verified record is legacy or below current Argon2id settings."""
    if not password_hash.startswith("$argon2id$"):
        return True
    try:
        return ARGON2_HASHER.check_needs_rehash(password_hash)
    except (InvalidHashError, VerificationError):
        return True


def password_issues(password: str) -> list[str]:
    """Returns a list of Russian-language problems with the password; empty list = OK."""
    issues = []
    if len(password or "") < 8:
        issues.append("Пароль должен быть не короче 8 символов.")
    if password and password.strip() != password:
        issues.append("Пароль не должен начинаться или заканчиваться пробелом.")
    if password and password.lower() in COMMON_WEAK_PASSWORDS:
        issues.append("Пароль слишком распространённый. Выберите более уникальный вариант.")
    return issues


def validate_email(email: str) -> bool:
    return bool(email) and bool(_EMAIL_RE.match(email.strip()))


# --------------------------------------------------------------------------
# Slugs (subdomain-safe tenant identifiers)
# --------------------------------------------------------------------------

def slugify_base(email: str) -> str:
    local_part = (email or "").split("@", 1)[0].strip().lower()
    slug = _SLUG_CHARS_RE.sub("-", local_part).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        slug = "client"
    return slug[:32]


def generate_unique_slug(conn: sqlite3.Connection, email: str, max_attempts: int = 20) -> str:
    base = slugify_base(email)
    candidate = base
    for attempt in range(max_attempts):
        existing = conn.execute(
            "SELECT 1 FROM tenant_instances WHERE slug=?", (candidate,)
        ).fetchone()
        if existing is None:
            return candidate
        candidate = f"{base}-{secrets.token_hex(2)}"
    raise RuntimeError("Не удалось подобрать уникальный slug для клиента.")


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------

def create_account(
    conn: sqlite3.Connection,
    email: str,
    password: str,
    pdn_consent: bool = True,
    email_verified: bool = True,
) -> int:
    # Defaults to True so the many tests/fixtures exercising unrelated
    # behavior (billing, password reset, etc.) don't all need updating --
    # the one caller that matters for real compliance is the actual
    # registration route (gateway/app.py register_submit()), which always
    # passes the real checkbox value, never this default.
    email = (email or "").strip().lower()
    if not validate_email(email):
        raise ValueError("Некорректный email.")
    issues = password_issues(password)
    if issues:
        raise ValueError(" ".join(issues))
    if not pdn_consent:
        # 152-ФЗ requires actual recorded consent, not just a client-side
        # `required` attribute someone could strip -- enforced here too, not
        # only in the HTML form (see gateway/templates/register.html).
        raise ValueError("Нужно подтвердить согласие на обработку персональных данных.")
    existing = conn.execute("SELECT id FROM accounts WHERE email=?", (email,)).fetchone()
    if existing is not None:
        raise ValueError("Аккаунт с таким email уже существует.")
    password_hash, salt = hash_password(password)
    now = _now_iso()
    cur = conn.execute(
        """
        INSERT INTO accounts(
            email, password_hash, password_salt, status, pdn_consent_at,
            pdn_policy_version, email_verified_at, created_at, updated_at
        )
        VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (email, password_hash, salt, now, PDN_POLICY_VERSION, now if email_verified else "", now, now),
    )
    # By column name, not position ([0]) -- sqlite3.Row supports both, but
    # Postgres's dict_row (see logic/db.py's _connect_postgres) is a plain
    # dict, which has no integer keys at all (KeyError: 0). Caught by
    # tests/test_db_backend_postgres.py's real-Postgres round-trip.
    return int(cur.fetchone()["id"])


def get_account_by_id(conn: sqlite3.Connection, account_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM accounts WHERE id=?", (int(account_id),)).fetchone()


def get_account_by_email(conn: sqlite3.Connection, email: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM accounts WHERE email=?", ((email or "").strip().lower(),)).fetchone()


def is_email_verified(account: sqlite3.Row | dict[str, Any] | None) -> bool:
    """Return whether the account owner has completed email verification."""
    return bool(account is not None and str(account.get("email_verified_at", "") if isinstance(account, dict) else account["email_verified_at"]))


def authenticate_account(conn: sqlite3.Connection, email: str, password: str) -> sqlite3.Row | None:
    account = get_account_by_email(conn, email)
    if account is None:
        verify_password(password, DUMMY_PASSWORD_HASH, DUMMY_PASSWORD_SALT)
        return None
    if not verify_password(password, account["password_hash"], account["password_salt"]):
        return None
    if needs_password_rehash(account["password_hash"]):
        password_hash, salt = hash_password(password)
        conn.execute(
            "UPDATE accounts SET password_hash=?, password_salt=?, updated_at=? WHERE id=?",
            (password_hash, salt, _now_iso(), int(account["id"])),
        )
        account = get_account_by_id(conn, int(account["id"]))
    return account


def change_password(conn: sqlite3.Connection, account_id: int, current_password: str, new_password: str) -> None:
    account = get_account_by_id(conn, int(account_id))
    if account is None or not verify_password(current_password, account["password_hash"], account["password_salt"]):
        raise ValueError("Текущий пароль указан неверно.")
    issues = password_issues(new_password)
    if issues:
        raise ValueError(" ".join(issues))
    password_hash, salt = hash_password(new_password)
    conn.execute("UPDATE accounts SET password_hash=?, password_salt=?, updated_at=? WHERE id=?", (password_hash, salt, _now_iso(), int(account_id)))
    revoke_all_sessions_for_account(conn, int(account_id))


def create_email_change_token(conn: sqlite3.Connection, account_id: int, new_email: str, ttl_minutes: int = 60) -> str:
    email = (new_email or "").strip().lower()
    if not validate_email(email):
        raise ValueError("Некорректный email.")
    existing = get_account_by_email(conn, email)
    if existing is not None and int(existing["id"]) != int(account_id):
        raise ValueError("Этот email уже используется.")
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    conn.execute("INSERT INTO email_change_tokens(token_hash, account_id, new_email, created_at, expires_at) VALUES (?, ?, ?, ?, ?)", (_hash_token(token), int(account_id), email, now.isoformat(timespec="seconds"), (now + timedelta(minutes=ttl_minutes)).isoformat(timespec="seconds")))
    return token


def consume_email_change_token(conn: sqlite3.Connection, token: str) -> int:
    row = conn.execute("SELECT * FROM email_change_tokens WHERE token_hash=?", (_hash_token(token),)).fetchone()
    if row is None or row["consumed_at"]:
        raise ValueError("Ссылка подтверждения email недействительна.")
    expires = datetime.fromisoformat(row["expires_at"])
    if expires < datetime.now(timezone.utc):
        raise ValueError("Ссылка подтверждения email истекла.")
    existing = get_account_by_email(conn, row["new_email"])
    if existing is not None and int(existing["id"]) != int(row["account_id"]):
        # The address could have been claimed after the link was sent. Do not
        # let a late confirmation turn into a raw UNIQUE constraint error.
        raise ValueError("Этот email уже используется.")
    now = _now_iso()
    conn.execute("UPDATE accounts SET email=?, updated_at=? WHERE id=?", (row["new_email"], now, int(row["account_id"])))
    conn.execute("UPDATE email_change_tokens SET consumed_at=? WHERE token_hash=?", (now, row["token_hash"]))
    revoke_all_sessions_for_account(conn, int(row["account_id"]))
    return int(row["account_id"])


def create_email_verification_token(conn: sqlite3.Connection, account_id: int, ttl_hours: int = EMAIL_VERIFICATION_TTL_HOURS) -> str:
    account = get_account_by_id(conn, int(account_id))
    if account is None:
        raise ValueError("Аккаунт не найден.")
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    conn.execute(
        "DELETE FROM email_verification_tokens WHERE account_id=? AND consumed_at IS NULL",
        (int(account_id),),
    )
    conn.execute(
        "INSERT INTO email_verification_tokens(token_hash, account_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (_hash_token(token), int(account_id), now.isoformat(timespec="seconds"), (now + timedelta(hours=ttl_hours)).isoformat(timespec="seconds")),
    )
    return token


def consume_email_verification_token(conn: sqlite3.Connection, token: str) -> int:
    row = conn.execute(
        "SELECT * FROM email_verification_tokens WHERE token_hash=?",
        (_hash_token(token),),
    ).fetchone()
    if row is None or row["consumed_at"]:
        raise ValueError("Ссылка подтверждения email недействительна.")
    expires = datetime.fromisoformat(row["expires_at"])
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        raise ValueError("Ссылка подтверждения email истекла.")
    account = get_account_by_id(conn, int(row["account_id"]))
    if account is None:
        raise ValueError("Аккаунт не найден.")
    now = _now_iso()
    conn.execute(
        "UPDATE accounts SET email_verified_at=?, updated_at=? WHERE id=?",
        (now, now, int(row["account_id"])),
    )
    conn.execute(
        "UPDATE email_verification_tokens SET consumed_at=? WHERE token_hash=?",
        (now, row["token_hash"]),
    )
    return int(row["account_id"])


def set_account_status(conn: sqlite3.Connection, account_id: int, status: str) -> None:
    if status not in ACCOUNT_STATUSES:
        raise ValueError(f"Недопустимый статус аккаунта: {status!r}")
    conn.execute(
        "UPDATE accounts SET status=?, updated_at=? WHERE id=?",
        (status, _now_iso(), int(account_id)),
    )


def account_has_access(account: sqlite3.Row | dict[str, Any] | None) -> bool:
    if account is None:
        return False
    return str(account["status"]) in ACCESS_ALLOWED_STATUSES


def delete_account(conn: sqlite3.Connection, account_id: int) -> None:
    """Delete control-plane personal data after the tenant is removed."""
    conn.execute("DELETE FROM accounts WHERE id=?", (int(account_id),))


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(conn: sqlite3.Connection, account_id: int, ttl_hours: int = SESSION_TTL_HOURS) -> str:
    token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=ttl_hours)
    conn.execute(
        "INSERT INTO sessions(token_hash, account_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (_hash_token(token), int(account_id), now.isoformat(timespec="seconds"), expires_at.isoformat(timespec="seconds")),
    )
    return token


def resolve_session(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    """Returns the account row for a valid, unexpired session token, else None."""
    if not token:
        return None
    row = conn.execute(
        "SELECT * FROM sessions WHERE token_hash=?", (_hash_token(token),)
    ).fetchone()
    if row is None:
        return None
    expires_at = datetime.fromisoformat(row["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (row["token_hash"],))
        return None
    return get_account_by_id(conn, int(row["account_id"]))


def purge_expired_sessions(conn: sqlite3.Connection) -> int:
    """Bulk-delete every expired session, not just the one being presented.
    resolve_session only prunes a token when its owner shows up with it, so
    sessions from abandoned/logged-out devices linger forever. Call this
    periodically (see scripts/run_billing_cycle.py) to keep the table small."""
    cur = conn.execute(
        "DELETE FROM sessions WHERE expires_at < ?",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"),),
    )
    return int(cur.rowcount or 0)


def revoke_session(conn: sqlite3.Connection, token: str) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (_hash_token(token),))


def revoke_all_sessions_for_account(conn: sqlite3.Connection, account_id: int) -> None:
    conn.execute("DELETE FROM sessions WHERE account_id=?", (int(account_id),))


# --------------------------------------------------------------------------
# Tenant provisioning state machine
# --------------------------------------------------------------------------

def create_tenant_instance(conn: sqlite3.Connection, account_id: int, slug: str) -> int:
    existing = conn.execute(
        "SELECT id FROM tenant_instances WHERE account_id=?", (int(account_id),)
    ).fetchone()
    if existing is not None:
        raise ValueError("У этого аккаунта уже есть выделенный экземпляр.")
    now = _now_iso()
    container_name = f"wb-tenant-{slug}"
    cur = conn.execute(
        """
        INSERT INTO tenant_instances(account_id, slug, container_name, status, created_at, updated_at)
        VALUES (?, ?, ?, 'provisioning', ?, ?)
        RETURNING id
        """,
        (int(account_id), slug, container_name, now, now),
    )
    tenant_id = int(cur.fetchone()["id"])
    conn.execute(
        """
        INSERT INTO tenant_memberships(tenant_id, account_id, role, created_at, updated_at)
        VALUES (?, ?, 'owner', ?, ?)
        """,
        (tenant_id, int(account_id), now, now),
    )
    return tenant_id


def set_tenant_status(conn: sqlite3.Connection, tenant_id: int, status: str, note: str = "") -> None:
    if status not in TENANT_STATUSES:
        raise ValueError(f"Недопустимый статус экземпляра: {status!r}")
    conn.execute(
        "UPDATE tenant_instances SET status=?, note=?, updated_at=? WHERE id=?",
        (status, note, _now_iso(), int(tenant_id)),
    )


def mark_tenant_provisioned(conn: sqlite3.Connection, tenant_id: int, account_id: int) -> None:
    """Called once the tenant's container is confirmed healthy.

    This only flips the *infrastructure* state (the container is up). It
    deliberately does NOT touch billing/account status -- that's
    logic/billing.py's job (billing.start_trial), called right after this
    by the same caller (see provisioning.py). Keeping the two separate
    avoids a circular import (billing.py needs account status helpers from
    this module) and keeps "is the container running" distinct from "does
    this account get to use it".
    """
    set_tenant_status(conn, tenant_id, "running")


def get_tenant_for_account(conn: sqlite3.Connection, account_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM tenant_instances WHERE account_id=?", (int(account_id),)
    ).fetchone()


def get_tenant_by_slug(conn: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM tenant_instances WHERE slug=?", (slug,)).fetchone()


def get_tenant_by_id(conn: sqlite3.Connection, tenant_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM tenant_instances WHERE id=?", (int(tenant_id),)).fetchone()


def get_tenant_membership(conn: sqlite3.Connection, tenant_id: int, account_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM tenant_memberships WHERE tenant_id=? AND account_id=?",
        (int(tenant_id), int(account_id)),
    ).fetchone()


def get_accessible_tenant_for_account(conn: sqlite3.Connection, account_id: int) -> sqlite3.Row | None:
    """Return the tenant a user can open, including their role.

    Owners are inserted into tenant_memberships for new and migrated tenants,
    so this single query covers both the original owner flow and invited users.
    """
    return conn.execute(
        """
        SELECT t.*, tm.role AS membership_role
        FROM tenant_instances t
        JOIN tenant_memberships tm ON tm.tenant_id=t.id
        WHERE tm.account_id=?
        ORDER BY CASE WHEN tm.role='owner' THEN 0 ELSE 1 END, t.created_at
        LIMIT 1
        """,
        (int(account_id),),
    ).fetchone()


def list_tenant_members(conn: sqlite3.Connection, tenant_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT tm.account_id, tm.role, tm.created_at, tm.updated_at, a.email,
               a.status AS account_status
        FROM tenant_memberships tm
        JOIN accounts a ON a.id=tm.account_id
        WHERE tm.tenant_id=?
        ORDER BY CASE WHEN tm.role='owner' THEN 0 WHEN tm.role='manager' THEN 1 ELSE 2 END, a.email
        """,
        (int(tenant_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def create_membership_invitation(
    conn: sqlite3.Connection,
    tenant_id: int,
    email: str,
    role: str = "viewer",
    ttl_days: int = TENANT_INVITATION_TTL_DAYS,
) -> str:
    email = (email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise ValueError("Укажите корректный email приглашённого пользователя.")
    if role not in TENANT_ROLES - {"owner"}:
        raise ValueError("Можно пригласить только менеджера или наблюдателя.")
    tenant = conn.execute("SELECT id FROM tenant_instances WHERE id=?", (int(tenant_id),)).fetchone()
    if tenant is None:
        raise ValueError("Кабинет не найден.")
    account = get_account_by_email(conn, email)
    if account is not None and get_tenant_membership(conn, int(tenant_id), int(account["id"])) is not None:
        raise ValueError("Этот пользователь уже добавлен в кабинет.")
    # Only one active invitation per address; issuing a new one invalidates
    # the old link without exposing the raw token in the database.
    conn.execute(
        "DELETE FROM tenant_invitations WHERE tenant_id=? AND email=? AND consumed_at IS NULL",
        (int(tenant_id), email),
    )
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO tenant_invitations(token_hash, tenant_id, email, role, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (_hash_token(token), int(tenant_id), email, role, now.isoformat(timespec="seconds"),
         (now + timedelta(days=max(1, int(ttl_days)))).isoformat(timespec="seconds")),
    )
    return token


def get_pending_membership_invitation(conn: sqlite3.Connection, token: str, email: str = "") -> sqlite3.Row | None:
    token = (token or "").strip()
    if not token:
        return None
    row = conn.execute(
        "SELECT * FROM tenant_invitations WHERE token_hash=? AND consumed_at IS NULL",
        (_hash_token(token),),
    ).fetchone()
    if row is None:
        return None
    expires_at = datetime.fromisoformat(row["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        return None
    if email and str(row["email"]).strip().lower() != (email or "").strip().lower():
        return None
    return row


def consume_membership_invitation(conn: sqlite3.Connection, token: str, account_id: int) -> int:
    """Consume an invitation for the logged-in, matching email account."""
    token = (token or "").strip()
    row = conn.execute(
        "SELECT * FROM tenant_invitations WHERE token_hash=?", (_hash_token(token),)
    ).fetchone()
    if row is None or row["consumed_at"] is not None:
        raise ValueError("Приглашение недействительно или уже использовано.")
    expires_at = datetime.fromisoformat(row["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise ValueError("Срок действия приглашения истёк.")
    account = get_account_by_id(conn, int(account_id))
    if account is None or str(account["email"]).strip().lower() != str(row["email"]).strip().lower():
        raise ValueError("Это приглашение предназначено для другого email.")
    existing = get_tenant_membership(conn, int(row["tenant_id"]), int(account_id))
    now = _now_iso()
    if existing is None:
        conn.execute(
            "INSERT INTO tenant_memberships(tenant_id, account_id, role, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (int(row["tenant_id"]), int(account_id), row["role"], now, now),
        )
    else:
        conn.execute(
            "UPDATE tenant_memberships SET role=?, updated_at=? WHERE tenant_id=? AND account_id=?",
            (row["role"], now, int(row["tenant_id"]), int(account_id)),
        )
    conn.execute("UPDATE tenant_invitations SET consumed_at=? WHERE token_hash=?", (now, row["token_hash"]))
    return int(row["tenant_id"])


def tenant_owner_account(conn: sqlite3.Connection, tenant_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT a.* FROM accounts a JOIN tenant_instances t ON t.account_id=a.id
        WHERE t.id=?
        """,
        (int(tenant_id),),
    ).fetchone()


def tenant_has_access(conn: sqlite3.Connection, tenant: sqlite3.Row | dict[str, Any] | None) -> bool:
    if tenant is None:
        return False
    owner = tenant_owner_account(conn, int(tenant["id"]))
    return account_has_access(owner)


# --------------------------------------------------------------------------
# Telegram report delivery: linking a Telegram chat to an account
# --------------------------------------------------------------------------

TELEGRAM_LINK_CODE_TTL_MINUTES = 15
# Excludes visually ambiguous characters (0/O, 1/I/L) -- a human is meant to
# read this off their dashboard and type it into Telegram by hand.
_LINK_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def create_telegram_link_code(conn: sqlite3.Connection, account_id: int, max_attempts: int = 20) -> str:
    """Generates a short one-time code the account owner types into the
    Telegram bot as `/link <code>` to associate their chat with this
    account -- see telegram_link_codes' schema comment in logic/db.py for
    why this indirection is needed at all."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=TELEGRAM_LINK_CODE_TTL_MINUTES)
    for _ in range(max_attempts):
        code = "".join(secrets.choice(_LINK_CODE_ALPHABET) for _ in range(6))
        try:
            conn.execute(
                "INSERT INTO telegram_link_codes(code, account_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (code, int(account_id), now.isoformat(timespec="seconds"), expires_at.isoformat(timespec="seconds")),
            )
            return code
        except sqlite3.IntegrityError:
            continue  # extremely unlikely code collision -- retry with a fresh one
    raise RuntimeError("Не удалось сгенерировать код привязки Telegram.")


def consume_telegram_link_code(conn: sqlite3.Connection, code: str, chat_id: str) -> bool:
    """Returns True and links `chat_id` to the code's account if the code is
    valid, unexpired, and unused; False otherwise. Single-use, same pattern
    as password_reset.consume_reset_token."""
    code = (code or "").strip().upper()
    if not code:
        return False
    row = conn.execute("SELECT * FROM telegram_link_codes WHERE code=?", (code,)).fetchone()
    if row is None or row["consumed_at"] is not None:
        return False
    expires_at = datetime.fromisoformat(row["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        return False

    conn.execute(
        "UPDATE telegram_link_codes SET consumed_at=? WHERE code=?",
        (_now_iso(), code),
    )
    conn.execute(
        "UPDATE tenant_instances SET telegram_chat_id=?, updated_at=? WHERE account_id=?",
        (str(chat_id), _now_iso(), int(row["account_id"])),
    )
    return True


def get_telegram_chat_id_for_slug(conn: sqlite3.Connection, slug: str) -> str:
    tenant = get_tenant_by_slug(conn, slug)
    if tenant is None:
        return ""
    return str(tenant["telegram_chat_id"] or "")


# --------------------------------------------------------------------------
# Admin panel
# --------------------------------------------------------------------------

def is_admin_account(account: sqlite3.Row | None) -> bool:
    """True only for a logged-in account whose email is in the
    WB_SAAS_ADMIN_EMAILS allowlist -- no dedicated role column, this is a
    single-operator tool, not a multi-admin permission system. `account` is
    a sqlite3.Row from resolve_session/get_account_by_id (not a dict), so
    indexing must use row["email"], not .get()."""
    if account is None or not config.ADMIN_EMAILS:
        return False
    try:
        email = str(account["email"]).strip().lower()
    except (IndexError, KeyError):
        return False
    return email in config.ADMIN_EMAILS


def list_all_tenants_overview(conn: sqlite3.Connection) -> list[dict]:
    """Every tenant joined with its owning account, newest first -- the data
    behind the admin dashboard's overview table."""
    rows = conn.execute(
        """
        SELECT
            t.id AS tenant_id, t.slug, t.container_name,
            t.status AS tenant_status, t.note AS tenant_note, t.created_at AS tenant_created_at,
            a.id AS account_id, a.email, a.status AS account_status,
            a.current_period_end, a.past_due_since
        FROM tenant_instances t
        JOIN accounts a ON a.id = t.account_id
        ORDER BY t.created_at DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]
