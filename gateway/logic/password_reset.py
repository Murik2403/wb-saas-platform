"""Pure business logic for "forgot password" links.

Same rules as the rest of logic/: zero imports of fastapi/smtplib/docker,
stdlib + sqlite3 only, so the whole request -> consume lifecycle is
unit-testable without a real mail server. Sending the email itself is a
separate, thin, untested-here wrapper (see ../mailer.py) -- exactly the
same split already used for accounts.py/billing.py vs
provisioning.py/yookassa_client.py.

Security properties this module is responsible for:
  - Only a SHA-256 hash of the raw token is ever stored (mirrors
    accounts.py's session tokens) -- a leak of this table alone cannot be
    used to reset anyone's password.
  - Single-use: a token is rejected the moment it's been consumed once,
    even if it hasn't expired yet.
  - Time-limited (config.PASSWORD_RESET_TTL_MINUTES, default 60 minutes).
  - Resetting a password revokes every existing session for that account --
    an attacker who had a stolen session cookie loses it the moment the
    legitimate owner resets their password.
  - request_password_reset() returns None for an unknown email, exactly
    like a raw token for a known one, so the *caller* (the HTTP route) can
    show the identical "if that email exists, we sent a link" message
    either way -- this module never tells the route "email not found" as
    a distinguishable outcome by design, to avoid leaking which emails are
    registered via response-timing/content differences.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

import config
from logic import accounts

RESET_TOKEN_BYTES = 32


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def create_reset_token(conn: sqlite3.Connection, account_id: int, ttl_minutes: int = config.PASSWORD_RESET_TTL_MINUTES) -> str:
    token = secrets.token_urlsafe(RESET_TOKEN_BYTES)
    now = _now()
    expires_at = now + timedelta(minutes=ttl_minutes)
    conn.execute(
        "INSERT INTO password_reset_tokens(token_hash, account_id, created_at, expires_at) VALUES (?,?,?,?)",
        (_hash_token(token), int(account_id), now.isoformat(timespec="seconds"), expires_at.isoformat(timespec="seconds")),
    )
    return token


def request_password_reset(conn: sqlite3.Connection, email: str, ttl_minutes: int = config.PASSWORD_RESET_TTL_MINUTES) -> str | None:
    """Returns a fresh raw reset token for a known email, or None for an
    unknown one -- deliberately the only signal this function gives, so the
    HTTP layer can't accidentally leak account existence through a richer
    return value."""
    account = accounts.get_account_by_email(conn, email)
    if account is None:
        return None
    return create_reset_token(conn, int(account["id"]), ttl_minutes=ttl_minutes)


def consume_reset_token(conn: sqlite3.Connection, raw_token: str, new_password: str) -> int:
    """Validates and burns a reset token, sets the new password, and revokes
    every existing session for the account. Returns the account_id on
    success; raises ValueError (Russian-language, safe to show the user) on
    any failure."""
    if not raw_token:
        raise ValueError("Ссылка для сброса пароля недействительна.")
    row = conn.execute(
        "SELECT * FROM password_reset_tokens WHERE token_hash=?", (_hash_token(raw_token),)
    ).fetchone()
    if row is None:
        raise ValueError("Ссылка для сброса пароля недействительна.")
    if row["consumed_at"]:
        raise ValueError("Эта ссылка уже была использована. Запросите новую.")
    if _parse_iso(row["expires_at"]) < _now():
        raise ValueError("Ссылка для сброса пароля истекла. Запросите новую.")

    issues = accounts.password_issues(new_password)
    if issues:
        raise ValueError(" ".join(issues))

    account_id = int(row["account_id"])
    password_hash, salt = accounts.hash_password(new_password)
    now = _now_iso()
    conn.execute(
        "UPDATE accounts SET password_hash=?, password_salt=?, updated_at=? WHERE id=?",
        (password_hash, salt, now, account_id),
    )
    conn.execute(
        "UPDATE password_reset_tokens SET consumed_at=? WHERE token_hash=?",
        (now, row["token_hash"]),
    )
    accounts.revoke_all_sessions_for_account(conn, account_id)
    return account_id


def delete_expired_reset_tokens(conn: sqlite3.Connection) -> int:
    """Housekeeping only -- unconsumed tokens are already rejected once
    expired, this just keeps the table from growing forever. Safe to call
    from the same daily cron as run_billing_cycle.py, or not at all."""
    cur = conn.execute(
        "DELETE FROM password_reset_tokens WHERE expires_at < ?", (_now_iso(),)
    )
    return int(cur.rowcount or 0)
