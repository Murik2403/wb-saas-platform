"""Pure business logic for the gateway: accounts, passwords, sessions, and
the tenant-provisioning state machine.

Deliberately has ZERO imports of fastapi/docker/uvicorn/httpx -- everything
here is stdlib + sqlite3, so it can be unit-tested in any Python environment
(including one with no network access to install third-party packages,
which is exactly the constraint this module was first written under). The
FastAPI routes and Docker orchestration are thin wrappers around these
functions; if a bug exists in "who can log in" or "whose data is whose",
it should be catchable here, not only by manually clicking through the
running site.

Password hashing uses stdlib hashlib.pbkdf2_hmac (no bcrypt/argon2
dependency required) -- this is a legitimate, long-standing recommended
approach (Django defaulted to PBKDF2 for years), not a placeholder.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import config

PBKDF2_ITERATIONS = 260_000
SESSION_TOKEN_BYTES = 32
SESSION_TTL_HOURS = 24 * 30  # 30 days

ACCOUNT_STATUSES = {"pending", "trialing", "active", "past_due", "canceled"}
# trialing/active/past_due all grant dashboard access -- past_due is a
# grace period after a failed or missing payment (see logic/billing.py),
# not an immediate lockout. Only "pending" (still provisioning) and
# "canceled" (grace period expired, or the client cancelled) are blocked.
ACCESS_ALLOWED_STATUSES = {"trialing", "active", "past_due"}

TENANT_STATUSES = {"provisioning", "running", "stopped", "failed"}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SLUG_CHARS_RE = re.compile(r"[^a-z0-9-]+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------

def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """Returns (password_hash_hex, salt_hex)."""
    if salt is None:
        salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return digest.hex(), salt.hex()


def verify_password(password: str, password_hash_hex: str, salt_hex: str) -> bool:
    salt = bytes.fromhex(salt_hex)
    candidate, _ = hash_password(password, salt=salt)
    return hmac.compare_digest(candidate, password_hash_hex)


def password_issues(password: str) -> list[str]:
    """Returns a list of Russian-language problems with the password; empty list = OK."""
    issues = []
    if len(password or "") < 8:
        issues.append("Пароль должен быть не короче 8 символов.")
    if password and password.strip() != password:
        issues.append("Пароль не должен начинаться или заканчиваться пробелом.")
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

def create_account(conn: sqlite3.Connection, email: str, password: str, pdn_consent: bool = True) -> int:
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
        INSERT INTO accounts(email, password_hash, password_salt, status, pdn_consent_at, created_at, updated_at)
        VALUES (?, ?, ?, 'pending', ?, ?, ?)
        RETURNING id
        """,
        (email, password_hash, salt, now, now, now),
    )
    return int(cur.fetchone()[0])


def get_account_by_id(conn: sqlite3.Connection, account_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM accounts WHERE id=?", (int(account_id),)).fetchone()


def get_account_by_email(conn: sqlite3.Connection, email: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM accounts WHERE email=?", ((email or "").strip().lower(),)).fetchone()


def authenticate_account(conn: sqlite3.Connection, email: str, password: str) -> sqlite3.Row | None:
    account = get_account_by_email(conn, email)
    if account is None:
        return None
    if not verify_password(password, account["password_hash"], account["password_salt"]):
        return None
    return account


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
    return int(cur.fetchone()[0])


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
