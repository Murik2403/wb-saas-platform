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
import ipaddress
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import PlainTextResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
import yookassa_client
from client_ip import client_ip
from logging_config import bind_account_context
from logic import account_lifecycle, accounts, billing, csrf, db as control_db
from rate_limiter import RateLimiter
from template_setup import create_templates

logger = logging.getLogger("wb_saas_gateway.billing")

router = APIRouter()
templates = create_templates()
webhook_global_limiter = RateLimiter(
    requests_per_window=120,
    window_seconds=60,
    storage_backend="db",
    db_connect=control_db.connect,
)
webhook_source_limiter = RateLimiter(
    requests_per_window=20,
    window_seconds=60,
    storage_backend="db",
    db_connect=control_db.connect,
)


def _webhook_source_allowed(source: str) -> bool:
    if not config.YOOKASSA_WEBHOOK_CIDRS:
        return False
    try:
        address = ipaddress.ip_address(source)
        return any(address in ipaddress.ip_network(cidr) for cidr in config.YOOKASSA_WEBHOOK_CIDRS)
    except ValueError:
        return False


def _current_account(request: Request):
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    if not token:
        return None
    with control_db.connect() as conn:
        account = accounts.resolve_session(conn, token)
    if account is not None:
        bind_account_context(account["id"])
    return account


def store_billing_page(request: Request):
    """Store builds of the mobile app must not sell: neutral page, no payment UI."""
    return templates.TemplateResponse(request, "store_billing.html", {})


def _is_store_app(request: Request) -> bool:
    return config.is_store_app(request.headers.get("user-agent", ""))


@router.get("/billing", response_class=HTMLResponse)
def billing_status(request: Request):
    if _is_store_app(request):
        return store_billing_page(request)
    account = _current_account(request)
    if account is None:
        return RedirectResponse("/login", status_code=303)

    days_left = None
    if account["status"] == "trialing" and account["current_period_end"]:
        end = datetime.fromisoformat(account["current_period_end"])
        days_left = max(0, (end - datetime.now(timezone.utc)).days)
    elif account["status"] == "past_due":
        days_left = billing.days_left_in_grace_period(account)

    with control_db.connect() as conn:
        payments = billing.list_payments(conn, int(account["id"]))
    csrf_token = csrf.get_or_create_token(request)
    response = templates.TemplateResponse(
        request,
        "billing.html",
        {
            "account": account,
            "days_left": days_left,
            "price_rub": config.SUBSCRIPTION_PRICE_RUB,
            "csrf_token": csrf_token,
            "payments": payments,
        },
    )
    csrf.set_cookie(response, csrf_token)
    return response


@router.post("/billing/checkout")
def billing_checkout(request: Request, csrf_token: str = Form("")):
    if _is_store_app(request):
        return store_billing_page(request)
    account = _current_account(request)
    if account is None:
        return RedirectResponse("/login", status_code=303)
    if not csrf.verify(request, csrf_token):
        return RedirectResponse("/billing", status_code=303)

    import uuid
    try:
        with control_db.connect() as conn:
            operation = account_lifecycle.begin_payment(conn, int(account["id"]), "checkout:" + uuid.uuid4().hex, allow_canceled=True, expected_amount_rub=f"{config.SUBSCRIPTION_PRICE_RUB:.2f}")
            account = accounts.get_account_by_id(conn, int(account["id"]))
    except account_lifecycle.LifecycleConflict as exc:
        return PlainTextResponse(str(exc), status_code=409)
    return_url = f"{'https' if config.COOKIE_SECURE else 'http'}://{config.PARENT_DOMAIN}/billing/return"
    try:
        payment = yookassa_client.create_payment(
            amount_rub=config.SUBSCRIPTION_PRICE_RUB,
            description=f"Подписка MARKETSHELPER — {account['email']}",
            return_url=return_url,
            save_payment_method=True,
            idempotence_key=operation,
            metadata={"account_id": str(account["id"]), "mh_operation_id": operation},
            customer_email=account["email"],
        )
        if not isinstance(payment, dict) or not isinstance(payment.get("id"), str) or not payment["id"]:
            raise ValueError("Invalid payment response")
    except Exception:
        with control_db.connect() as conn:
            account_lifecycle.mark_payment_outcome(conn, operation, "unknown")
        logger.warning("Payment outcome unknown for account %s; reconciliation required", account["id"])
        return templates.TemplateResponse(
            request,
            "billing.html",
            {
                "account": account,
                "days_left": None,
                "price_rub": config.SUBSCRIPTION_PRICE_RUB,
                "error": "Результат оплаты пока неизвестен. Не повторяйте платёж; обратитесь в поддержку для сверки.",
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
        account_lifecycle.mark_payment_outcome(conn, operation, "completed" if payment.get("status") in {"succeeded", "canceled"} else "provider_pending", payment["id"])
        if payment.get("status") == "succeeded":
            billing.apply_successful_payment(conn, int(account["id"]), payment["id"],
                payment_method_id=yookassa_client.extract_payment_method_id(payment),is_test=bool(payment.get("test",False)))
        elif payment.get("status") == "canceled":
            billing.apply_failed_payment(conn,int(account["id"]),payment["id"],error="canceled")

    confirmation = payment.get("confirmation") or {}
    confirmation_url = confirmation.get("confirmation_url")
    if not confirmation_url:
        logger.error("YooKassa payment %s has no confirmation_url", payment.get("id"))
        return RedirectResponse("/billing", status_code=303)
    return RedirectResponse(confirmation_url, status_code=303)


@router.get("/billing/return", response_class=HTMLResponse)
def billing_return(request: Request):
    if _is_store_app(request):
        return store_billing_page(request)
    # The customer lands here right after leaving YooKassa's hosted page --
    # the payment may not have been confirmed by the webhook yet, so this
    # is deliberately just a "we're processing it" page, not a final
    # success/failure message. /billing (linked from here) reflects the
    # real, current, server-verified status.
    return templates.TemplateResponse(request, "billing_return.html")


@router.get("/billing/cancel", response_class=HTMLResponse)
def billing_cancel_confirm(request: Request):
    if _is_store_app(request):
        return store_billing_page(request)
    """Confirmation step before self-service cancellation -- deliberately a
    separate GET page rather than a bare button on /billing, so a client
    can't lose access to their own data from a single accidental click."""
    account = _current_account(request)
    if account is None:
        return RedirectResponse("/login", status_code=303)
    if account["status"] == "canceled":
        return RedirectResponse("/billing", status_code=303)
    csrf_token = csrf.get_or_create_token(request)
    response = templates.TemplateResponse(request, "billing_cancel.html", {"account": account, "csrf_token": csrf_token})
    csrf.set_cookie(response, csrf_token)
    return response


@router.post("/billing/cancel")
def billing_cancel_submit(request: Request, background_tasks: BackgroundTasks, csrf_token: str = Form("")):
    if _is_store_app(request):
        return store_billing_page(request)
    account = _current_account(request)
    if account is None:
        return RedirectResponse("/login", status_code=303)
    if not csrf.verify(request, csrf_token):
        return RedirectResponse("/billing", status_code=303)
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
    source = client_ip(request)
    if not config.YOOKASSA_WEBHOOK_CIDRS:
        logger.error("YooKassa webhook denied: source CIDRs are not configured")
        return JSONResponse({"error": "webhook source allowlist is not configured"}, status_code=503)
    if not _webhook_source_allowed(source):
        logger.warning("YooKassa webhook denied from untrusted source %s", source)
        return JSONResponse({"error": "untrusted webhook source"}, status_code=403)
    if not webhook_global_limiter.is_allowed("global") or not webhook_source_limiter.is_allowed(source):
        return JSONResponse({"error": "too many webhook requests"}, status_code=429)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    payment_id = (body.get("object") or {}).get("id")
    if not payment_id:
        return JSONResponse({"error": "missing payment id"}, status_code=400)

    event_type = str(body.get("event") or "")
    if event_type.startswith("refund."):
        try:
            refund = yookassa_client.get_refund(payment_id)
        except yookassa_client.YooKassaError:
            logger.exception("Failed to verify YooKassa refund %s via API", payment_id)
            return JSONResponse({"error": "verification failed"}, status_code=502)
        # Body/event status is unsigned. Only the authenticated provider GET
        # can reserve, settle or release a pending refund's local amount.
        try:
            if not isinstance(refund, dict) or refund.get("id") != payment_id:
                raise ValueError("Verified refund ID mismatch")
            original_payment_id = refund.get("payment_id")
            refund_status = refund.get("status")
            refund_amount = refund.get("amount")
            if (not isinstance(original_payment_id, str) or not original_payment_id.strip()
                    or refund_status not in {"pending", "succeeded", "canceled"}
                    or not isinstance(refund_amount, dict) or refund_amount.get("currency") != "RUB"):
                raise ValueError("Verified refund metadata mismatch")
            cents = billing._refund_cents(refund_amount.get("value"))
            with control_db.connect() as conn:
                original = conn.execute("SELECT id,account_id FROM payments WHERE yookassa_payment_id=?", (original_payment_id,)).fetchone()
                previous = conn.execute("SELECT payment_id FROM refunds WHERE yookassa_refund_id=?", (payment_id,)).fetchone()
                if previous is not None and (original is None or previous["payment_id"] != original["id"]):
                    raise ValueError("Verified refund payment binding mismatch")
                if original is not None:
                    billing.apply_refund(conn, int(original["account_id"]), original_payment_id,
                                         cents / 100, payment_id, status=refund_status)
                    if refund_status in {"pending", "succeeded"}:
                        from logic import refund_intents
                        refund_intents.bind_from_webhook(conn, int(original["id"]), cents, payment_id)
        except (ValueError, TypeError):
            logger.warning("Rejected mismatched verified YooKassa refund %s", payment_id)
            return JSONResponse({"error": "verified refund mismatch"}, status_code=502)
        return JSONResponse({"status": "ok"})

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
    try:
        account_id = int(account_id_raw)
    except (TypeError, ValueError):
        # A payment created outside our checkout (e.g. manually in the YooKassa
        # dashboard) could carry a non-numeric account_id -- ack it so YooKassa
        # stops retrying rather than 500-ing on every delivery.
        logger.error("YooKassa payment %s has non-numeric account_id metadata: %r", payment_id, account_id_raw)
        return JSONResponse({"error": "invalid account_id"}, status_code=200)

    if not isinstance(payment, dict) or payment.get("id") != payment_id:
        return JSONResponse({"error": "verified payment mismatch"}, status_code=502)
    status = payment.get("status")
    with control_db.connect() as conn:
        operation_id = (payment.get("metadata") or {}).get("mh_operation_id")
        if operation_id:
            try:
                claim = account_lifecycle.bind_verified_provider(conn, account_id, operation_id, payment)
            except account_lifecycle.LifecycleConflict:
                return JSONResponse({"error": "payment operation mismatch"}, status_code=200)
            if conn.execute("SELECT id FROM payments WHERE yookassa_payment_id=?", (payment_id,)).fetchone() is None:
                billing.record_payment_attempt(conn, account_id, payment_id,
                    "recurring" if claim["operation_key"].startswith("recurring:") else "manual",
                    float(claim["expected_amount_rub"]), status="pending")
        if status == "succeeded":
            payment_method_id = yookassa_client.extract_payment_method_id(payment)
            billing.apply_successful_payment(
                conn, account_id, payment_id, payment_method_id=payment_method_id,
                period_days=config.BILLING_PERIOD_DAYS,
                is_test=bool(payment.get("test", False)),
            )
        elif status in ("canceled",):
            error = ((payment.get("cancellation_details") or {}).get("reason")) or "canceled"
            billing.apply_failed_payment(conn, account_id, payment_id, error=str(error))
        # "pending"/"waiting_for_capture" -- not a final state yet, nothing to do.

    return JSONResponse({"status": "ok"})
