"""Billing HTTP routes: subscription status page, checkout redirect, and
the YooKassa webhook. Kept in its own router (included from app.py) so
app.py's registration/login/auth-verify logic doesn't get tangled up with
payment plumbing.

Like app.py, this is a thin wrapper over logic/billing.py (tested) and
yookassa_client.py (untestable here, kept boring) -- see those modules'
docstrings.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
import yookassa_client
from logic import accounts, billing, db as control_db

logger = logging.getLogger("wb_saas_gateway.billing")

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def _current_account(request: Request):
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    if not token:
        return None
    with control_db.connect() as conn:
        return accounts.resolve_session(conn, token)


@router.get("/billing", response_class=HTMLResponse)
def billing_status(request: Request):
    account = _current_account(request)
    if account is None:
        return RedirectResponse("/login", status_code=303)

    days_left = None
    if account["status"] == "trialing" and account["current_period_end"]:
        end = datetime.fromisoformat(account["current_period_end"])
        days_left = max(0, (end - datetime.now(timezone.utc)).days)
    elif account["status"] == "past_due":
        days_left = billing.days_left_in_grace_period(account)

    return templates.TemplateResponse(
        "billing.html",
        {
            "request": request,
            "account": account,
            "days_left": days_left,
            "price_rub": config.SUBSCRIPTION_PRICE_RUB,
        },
    )


@router.post("/billing/checkout")
def billing_checkout(request: Request):
    account = _current_account(request)
    if account is None:
        return RedirectResponse("/login", status_code=303)

    return_url = f"{'https' if config.COOKIE_SECURE else 'http'}://{config.PARENT_DOMAIN}/billing/return"
    try:
        payment = yookassa_client.create_payment(
            amount_rub=config.SUBSCRIPTION_PRICE_RUB,
            description=f"Подписка WB Control — {account['email']}",
            return_url=return_url,
            save_payment_method=True,
            metadata={"account_id": str(account["id"])},
        )
    except yookassa_client.YooKassaError:
        logger.exception("Failed to create YooKassa payment for account %s", account["id"])
        return templates.TemplateResponse(
            "billing.html",
            {
                "request": request,
                "account": account,
                "days_left": None,
                "price_rub": config.SUBSCRIPTION_PRICE_RUB,
                "error": "Не получилось начать оплату. Попробуйте ещё раз через пару минут.",
            },
            status_code=502,
        )

    with control_db.connect() as conn:
        # "manual" covers both the very first payment and any later
        # user-initiated recovery checkout (e.g. from past_due) -- the
        # audit trail cares mainly about succeeded/failed, not this label;
        # only the automated daily job (scripts/run_billing_cycle.py) uses
        # "recurring".
        billing.record_payment_attempt(
            conn, account["id"], payment["id"], "manual", config.SUBSCRIPTION_PRICE_RUB, status="pending"
        )

    confirmation = payment.get("confirmation") or {}
    confirmation_url = confirmation.get("confirmation_url")
    if not confirmation_url:
        logger.error("YooKassa payment %s has no confirmation_url", payment.get("id"))
        return RedirectResponse("/billing", status_code=303)
    return RedirectResponse(confirmation_url, status_code=303)


@router.get("/billing/return", response_class=HTMLResponse)
def billing_return(request: Request):
    # The customer lands here right after leaving YooKassa's hosted page --
    # the payment may not have been confirmed by the webhook yet, so this
    # is deliberately just a "we're processing it" page, not a final
    # success/failure message. /billing (linked from here) reflects the
    # real, current, server-verified status.
    return templates.TemplateResponse("billing_return.html", {"request": request})


@router.get("/billing/cancel", response_class=HTMLResponse)
def billing_cancel_confirm(request: Request):
    """Confirmation step before self-service cancellation -- deliberately a
    separate GET page rather than a bare button on /billing, so a client
    can't lose access to their own data from a single accidental click."""
    account = _current_account(request)
    if account is None:
        return RedirectResponse("/login", status_code=303)
    if account["status"] == "canceled":
        return RedirectResponse("/billing", status_code=303)
    return templates.TemplateResponse("billing_cancel.html", {"request": request, "account": account})


@router.post("/billing/cancel")
def billing_cancel_submit(request: Request, background_tasks: BackgroundTasks):
    account = _current_account(request)
    if account is None:
        return RedirectResponse("/login", status_code=303)
    if account["status"] != "canceled":
        with control_db.connect() as conn:
            billing.cancel_account(conn, account["id"])
            tenant = accounts.get_tenant_for_account(conn, account["id"])
        # Stopping the container talks to Docker and can take a moment; the
        # account is already locked out (status='canceled' revokes access
        # immediately via account_has_access), so there's no rush -- do it
        # after the response the same way registration's provisioning does.
        if tenant is not None and tenant["status"] == "running":
            import provisioning  # local import: keeps this module importable/testable without docker installed

            background_tasks.add_task(provisioning.stop_tenant, tenant["slug"])
    return RedirectResponse("/billing", status_code=303)


@router.post("/webhooks/yookassa")
async def yookassa_webhook(request: Request):
    """YooKassa calls this on payment status changes. Per yookassa_client's
    module docstring: notifications are unsigned, so the payment id is used
    only to look up the *real* status via an authenticated API call --
    nothing in the POST body itself is trusted for the actual decision.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    payment_id = (body.get("object") or {}).get("id")
    if not payment_id:
        return JSONResponse({"error": "missing payment id"}, status_code=400)

    try:
        payment = yookassa_client.get_payment(payment_id)
    except yookassa_client.YooKassaError:
        logger.exception("Failed to verify YooKassa payment %s via API", payment_id)
        # 502 tells YooKassa to retry the webhook later rather than giving up.
        return JSONResponse({"error": "verification failed"}, status_code=502)

    account_id_raw = (payment.get("metadata") or {}).get("account_id")
    if not account_id_raw:
        logger.error("YooKassa payment %s has no account_id in metadata", payment_id)
        return JSONResponse({"error": "no account_id"}, status_code=200)  # ack -- retrying won't fix a bad payment
    account_id = int(account_id_raw)

    status = payment.get("status")
    with control_db.connect() as conn:
        if status == "succeeded":
            payment_method_id = yookassa_client.extract_payment_method_id(payment)
            billing.apply_successful_payment(
                conn, account_id, payment_id, payment_method_id=payment_method_id,
                period_days=config.BILLING_PERIOD_DAYS,
            )
        elif status in ("canceled",):
            error = ((payment.get("cancellation_details") or {}).get("reason")) or "canceled"
            billing.apply_failed_payment(conn, account_id, payment_id, error=str(error))
        # "pending"/"waiting_for_capture" -- not a final state yet, nothing to do.

    return JSONResponse({"status": "ok"})
