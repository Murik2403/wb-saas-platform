#!/usr/bin/env python3
"""Trial onboarding sequence runner: at most one sequence email per account
per run, at most one per account per day.

Same shape as run_billing_cycle.py -- meant for cron on the VPS, opens the
real control-plane db inside the gateway container (see DEPLOY.md, and the
./scripts:/app/scripts:ro mount in docker-compose.yml). Safe to run hourly:
which email is due is recomputed from scratch every time, and trial_emails'
composite primary key makes a duplicate send impossible even if two runs
overlap.

SENDS NOTHING unless --send is passed. The default is --dry-run, which
prints what would go out and never touches SMTP -- that is the mode the doc
asks for while the sequence is being checked on artificial data
(docs/content/02-trial-email-sequence.md, "Готовность отдельной рассылки").

Order per account: claim the log row, then send. A crash in between loses
one message; the opposite order can deliver the same message twice, which
is the failure mode that actually annoys a customer.

Exit code is nonzero only if a send was attempted and SMTP refused it.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

import config  # noqa: E402
import mailer  # noqa: E402
from logic import accounts, db as control_db, trial_emails  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("run_trial_emails")


def candidate_account_ids(conn) -> list[int]:
    """Only states the sequence can act on, and only accounts that are not
    explicitly classified as demo/staff/test traffic (doc rule 9 -- an
    explicit growth exclusion, never a guess from the email domain)."""
    rows = conn.execute(
        "SELECT a.id FROM accounts a "
        "LEFT JOIN growth_sources g ON g.account_id = a.id "
        "WHERE a.status IN ('trialing','canceled') AND a.email_verified_at <> '' "
        "AND COALESCE(g.exclusion_reason,'') = '' "
        "ORDER BY a.id"
    ).fetchall()
    return [int(row["id"]) for row in rows]


def prepare(conn, account_id: int) -> tuple[str, str, str] | None:
    """Re-read everything and decide, immediately before a send.

    Returns (email_key, subject, body) or None. A body that still shows one
    of the doc's %ПЛЕЙСХОЛДЕР% tokens is refused here rather than sent
    half-filled (doc rule 8)."""
    account = accounts.get_account_by_id(conn, account_id)
    if account is None:
        return None
    tenant = accounts.get_tenant_for_account(conn, account_id)
    email_key = trial_emails.due_email_key(
        account,
        trial_emails.growth_events(conn, account_id),
        sent=trial_emails.sent_log(conn, account_id),
        opted_out=trial_emails.is_opted_out(conn, account_id),
        tenant_status=tenant["status"] if tenant else "",
    )
    if email_key is None:
        return None
    unsubscribe_link = trial_emails.unsubscribe_url(account_id)
    if not trial_emails.unsubscribe_token(account_id):
        logger.error(
            "Account %s: no unsubscribe link (WB_SAAS_INTERNAL_API_SECRET/"
            "GROWTH_COOKIE_SECRET unset) -- refusing to send %s", account_id, email_key,
        )
        return None
    subject, body = trial_emails.render_email(
        email_key,
        cabinet_url=config.tenant_url(tenant["slug"]) if tenant else config.base_url(),
        unsubscribe_link=unsubscribe_link,
        period_end=account["current_period_end"],
    )
    unfilled = trial_emails.unfilled_placeholders(subject + "\n" + body)
    if unfilled:
        logger.warning(
            "Account %s: email %s not sent -- unconfirmed %s (see the TODO constants "
            "in gateway/logic/trial_emails.py)", account_id, email_key, ", ".join(unfilled),
        )
        return None
    return email_key, subject, body


def run(send: bool) -> int:
    failures = 0
    with control_db.connect() as conn:
        account_ids = candidate_account_ids(conn)

    for account_id in account_ids:
        # A fresh connection (and a fresh read) per account: the scan above
        # may be minutes old by now, and a payment in between must win.
        with control_db.connect() as conn:
            decision = prepare(conn, account_id)
            if decision is None:
                continue
            email_key, subject, body = decision
            account = accounts.get_account_by_id(conn, account_id)
            if not send:
                logger.info("[dry-run] account %s <%s> -> email %s: %s",
                            account_id, account["email"], email_key, subject)
                continue
            if not trial_emails.claim_send(conn, account_id, email_key):
                logger.info("Account %s: email %s already claimed by another run", account_id, email_key)
                continue

        delivered = mailer.send_email(account["email"], subject, body)
        with control_db.connect() as conn:
            trial_emails.finish_send(conn, account_id, email_key, delivered=delivered)
        if delivered:
            logger.info("Account %s: sent email %s", account_id, email_key)
        else:
            logger.error("Account %s: SMTP refused email %s -- not retried", account_id, email_key)
            failures += 1
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Print what would be sent and send nothing (default)")
    parser.add_argument("--send", dest="dry_run", action="store_false",
                        help="Actually deliver the due emails")
    args = parser.parse_args()
    if args.dry_run:
        logger.info("Dry run -- no email will be sent. Pass --send to deliver.")
    failures = run(send=not args.dry_run)
    if failures:
        logger.error("%d email(s) failed at the SMTP step", failures)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
