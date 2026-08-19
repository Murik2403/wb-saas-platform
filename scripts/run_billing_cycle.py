#!/usr/bin/env python3
"""Daily billing cycle: charge accounts whose period has ended, expire
unpaid trials into the grace period, and cancel (+ stop the container of)
accounts that have been past_due longer than the grace period allows.

Meant to run once a day from cron on the VPS -- see DEPLOY.md for the
crontab line. Idempotent to run more than once a day: accounts already
billed for the current period won't show up in accounts_due_for_recurring_charge
again until their new current_period_end passes, and record_payment_attempt
is keyed on YooKassa's own payment id so a re-run can't double-charge.

Exit code is nonzero if any recurring charge raised an exception talking to
YooKassa (network/API failure) -- a plain "card declined" is NOT an error
here, that's the expected, correctly-handled past_due path; only outright
failures to reach YooKassa should page someone.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

import config  # noqa: E402
import provisioning  # noqa: E402
import yookassa_client  # noqa: E402
from logic import accounts, billing, db as control_db  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("run_billing_cycle")


def charge_due_accounts() -> int:
    """Returns the number of accounts where talking to YooKassa itself failed
    (as opposed to a normal card decline, which is handled, not an error)."""
    with control_db.connect() as conn:
        due = billing.accounts_due_for_recurring_charge(conn)

    api_failures = 0
    for account in due:
        account_id = int(account["id"])
        logger.info("Charging account %s (%s) for next period", account_id, account["email"])
        try:
            payment = yookassa_client.create_payment(
                amount_rub=config.SUBSCRIPTION_PRICE_RUB,
                description=f"Продление подписки WB Control — {account['email']}",
                payment_method_id=account["payment_method_id"],
                metadata={"account_id": str(account_id)},
            )
        except yookassa_client.YooKassaError:
            logger.exception("YooKassa API call failed for account %s", account_id)
            api_failures += 1
            continue

        with control_db.connect() as conn:
            billing.record_payment_attempt(
                conn, account_id, payment["id"], "recurring", config.SUBSCRIPTION_PRICE_RUB,
                status="pending",
            )
            status = payment.get("status")
            if status == "succeeded":
                payment_method_id = yookassa_client.extract_payment_method_id(payment) or account["payment_method_id"]
                billing.apply_successful_payment(
                    conn, account_id, payment["id"], payment_method_id=payment_method_id,
                    period_days=config.BILLING_PERIOD_DAYS,
                )
                logger.info("Account %s charged successfully", account_id)
            elif status == "canceled":
                reason = (payment.get("cancellation_details") or {}).get("reason", "declined")
                billing.apply_failed_payment(conn, account_id, payment["id"], error=str(reason))
                logger.warning("Account %s charge declined: %s", account_id, reason)
            # else: pending/waiting_for_capture -- the YooKassa webhook will finalize this.
    return api_failures


def expire_unpaid_trials() -> None:
    with control_db.connect() as conn:
        expiring = billing.trials_expiring_without_payment(conn)
        for account in expiring:
            logger.info("Trial ended without payment for account %s (%s) -- starting grace period",
                        account["id"], account["email"])
            billing.expire_trial_without_payment(conn, int(account["id"]))


def cancel_overdue_accounts() -> None:
    with control_db.connect() as conn:
        overdue = billing.accounts_past_grace_period(conn)
        tenants = {int(a["id"]): accounts.get_tenant_for_account(conn, int(a["id"])) for a in overdue}
        for account in overdue:
            billing.cancel_account(conn, int(account["id"]))
            logger.info("Account %s (%s) canceled -- grace period expired", account["id"], account["email"])

    for account_id, tenant in tenants.items():
        if tenant is None:
            continue
        try:
            provisioning.stop_tenant(tenant["slug"])
            logger.info("Stopped container for cancelled account %s (slug=%s)", account_id, tenant["slug"])
        except Exception:
            logger.exception("Failed to stop container for cancelled account %s (slug=%s)", account_id, tenant["slug"])


def main() -> int:
    api_failures = charge_due_accounts()
    expire_unpaid_trials()
    cancel_overdue_accounts()
    if api_failures:
        logger.error("%d account(s) could not be billed due to YooKassa API failures -- will retry next run", api_failures)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
