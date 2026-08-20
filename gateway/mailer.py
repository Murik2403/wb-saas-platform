"""Thin SMTP sender for transactional email (currently: password reset
links only). Deliberately boring -- stdlib smtplib/email only, no
third-party mail SDK/API dependency, so there's nothing here to install
and nothing here to unit test beyond "does it build a well-formed
message" (see the message-building tests in gateway/tests/test_mailer.py).
Actually talking to an SMTP server cannot be exercised in the sandbox this
was authored in (no network) -- verify on a real host before relying on
password reset in production, see DEPLOY.md.

If config.SMTP_HOST is empty, send_email() logs the message instead of
sending it -- lets the rest of the app (registration, login, etc.) keep
working in an environment that hasn't configured SMTP yet, at the cost of
password reset silently not delivering. The route that calls this should
still tell the user "if that email exists, we sent a link" either way (see
billing/password-reset security note in logic/password_reset.py) -- an
unconfigured SMTP host is an operator mistake to catch in logs, not
something to expose to the end user.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

import config

logger = logging.getLogger("wb_saas_gateway.mailer")


def build_message(to_email: str, subject: str, body_text: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.SMTP_FROM_EMAIL
    msg["To"] = to_email
    msg.set_content(body_text)
    return msg


def send_email(to_email: str, subject: str, body_text: str) -> bool:
    """Returns True if the message was handed off to an SMTP server (or
    logged, in the no-SMTP-configured fallback), False on a delivery
    failure. Never raises -- a mail outage must not turn into a 500 for the
    person requesting a password reset; the caller should show the same
    "check your email" message regardless (see logic/password_reset.py)."""
    if not config.SMTP_HOST:
        logger.warning(
            "WB_SAAS_SMTP_HOST is not configured -- not sending email, logging instead. To: %s Subject: %s",
            to_email, subject,
        )
        logger.info("Email body that would have been sent:\n%s", body_text)
        return True

    message = build_message(to_email, subject, body_text)
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as smtp:
            if config.SMTP_USE_STARTTLS:
                smtp.starttls()
            if config.SMTP_USER:
                smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(message)
        return True
    except Exception:
        logger.exception("Failed to send email to %s", to_email)
        return False


def send_password_reset_email(to_email: str, reset_url: str, ttl_minutes: int) -> bool:
    subject = "Восстановление пароля — Marketshelper"
    body = (
        f"Кто-то (надеемся, что вы) запросил сброс пароля для аккаунта {to_email} "
        "в Marketshelper.\n\n"
        f"Чтобы задать новый пароль, перейдите по ссылке в течение {ttl_minutes} минут:\n"
        f"{reset_url}\n\n"
        "Если вы не запрашивали сброс пароля, просто проигнорируйте это письмо -- "
        "пароль останется прежним."
    )
    return send_email(to_email, subject, body)
