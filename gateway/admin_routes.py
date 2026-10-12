"""Admin HTTP routes: a read-only overview of every account/tenant plus a
few manual operator actions (stop/start a tenant container, comp-extend a
subscription). Kept in its own router (included from app.py), same reasoning
as billing_routes.py -- this doesn't belong tangled into registration/login.

Access control is a plain email allowlist (config.ADMIN_EMAILS), not a role
column -- see logic/accounts.py's is_admin_account docstring for why. Every
mutating route here follows the same CSRF pattern as billing_routes.py.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import config
from logging_config import bind_account_context
from logic import accounts, billing, csrf, growth, product_feedback, db as control_db
from template_setup import create_templates

logger = logging.getLogger("wb_saas_gateway.admin")

router = APIRouter()
templates = create_templates()


def _current_account(request: Request):
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    if not token:
        return None
    with control_db.connect() as conn:
        account = accounts.resolve_session(conn, token)
    if account is not None:
        bind_account_context(account["id"])
    return account


def _require_admin(request: Request):
    """Returns the account row if it's a logged-in admin, else None -- every
    route below must redirect to /login when this is None, so a non-admin
    (or logged-out visitor) never learns /admin exists beyond a 303."""
    account = _current_account(request)
    if account is None or not accounts.is_admin_account(account):
        return None
    return account


@router.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request):
    account = _require_admin(request)
    if account is None:
        # Preserve the requested admin destination so an administrator lands
        # here after signing in instead of being sent to the tenant.
        return RedirectResponse("/login?next=%2Fadmin", status_code=303)

    with control_db.connect() as conn:
        tenants = accounts.list_all_tenants_overview(conn)
        growth_report = growth.growth_summary(conn)
        feedback_report = product_feedback.summary(conn)
        billing_operations=[dict(row) for row in conn.execute("SELECT operation_id,account_id,state,provider_id,created_at FROM billing_operation_claims WHERE state!='completed' ORDER BY created_at LIMIT 100").fetchall()]
        pending_payments=[dict(row) for row in conn.execute("SELECT account_id,yookassa_payment_id,created_at FROM payments WHERE status='pending' ORDER BY created_at LIMIT 100").fetchall()]
        exclusions = {int(row["account_id"]): row["exclusion_reason"] for row in conn.execute("SELECT account_id,exclusion_reason FROM growth_sources").fetchall()}
        for tenant in tenants:
            tenant["growth_exclusion"] = exclusions.get(tenant["account_id"], "")

    csrf_token = csrf.get_or_create_token(request)
    response = templates.TemplateResponse(
        request,
        "admin_dashboard.html",
        {"tenants": tenants, "csrf_token": csrf_token, "growth": growth_report, "product_feedback": feedback_report, "billing_operations": billing_operations, "pending_payments": pending_payments},
    )
    response.headers["Cache-Control"] = "private, no-store"
    csrf.set_cookie(response, csrf_token)
    return response


@router.post("/admin/account/{account_id}/growth-exclusion")
def admin_growth_exclusion(request: Request, account_id: int, reason: str = Form(""), csrf_token: str = Form("")):
    if _require_admin(request) is None:
        return RedirectResponse("/login", status_code=303)
    if not csrf.verify(request, csrf_token):
        return RedirectResponse("/admin", status_code=303)
    if reason not in growth.EXCLUSION_REASONS:
        return RedirectResponse("/admin", status_code=303)
    with control_db.connect() as conn:
        if accounts.get_account_by_id(conn, account_id) is not None:
            growth.set_account_exclusion(conn, account_id, reason)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/tenant/{slug}/stop")
def admin_tenant_stop(request: Request, slug: str, csrf_token: str = Form("")):
    account = _require_admin(request)
    if account is None:
        return RedirectResponse("/login", status_code=303)
    if not csrf.verify(request, csrf_token):
        return RedirectResponse("/admin", status_code=303)

    import provisioning  # local import: keeps this module importable/testable without docker installed

    try:
        provisioning.stop_tenant(slug)
    except Exception:
        logger.exception("admin: failed to stop tenant %s", slug)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/tenant/{slug}/start")
def admin_tenant_start(request: Request, slug: str, csrf_token: str = Form("")):
    account = _require_admin(request)
    if account is None:
        return RedirectResponse("/login", status_code=303)
    if not csrf.verify(request, csrf_token):
        return RedirectResponse("/admin", status_code=303)

    import provisioning  # local import: keeps this module importable/testable without docker installed

    try:
        # upgrade_tenant recreates the container on a fresh network -- the
        # same operation used to roll out a code fix also works to bring a
        # previously-stopped tenant back up, since stop_tenant already tore
        # down its old network (see provisioning.py's isolation cleanup).
        provisioning.upgrade_tenant(slug)
    except Exception:
        logger.exception("admin: failed to start tenant %s", slug)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/account/{account_id}/extend")
def admin_account_extend(request: Request, account_id: int, days: int = Form(30), csrf_token: str = Form("")):
    account = _require_admin(request)
    if account is None:
        return RedirectResponse("/login", status_code=303)
    if not csrf.verify(request, csrf_token):
        return RedirectResponse("/admin", status_code=303)

    with control_db.connect() as conn:
        try:
            billing.admin_extend_period(conn, account_id, days)
        except ValueError:
            logger.exception("admin: failed to extend account %s", account_id)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/payment/{payment_id}/refund")
def admin_payment_refund(request: Request, payment_id: str, amount: float | None = Form(None), csrf_token: str = Form("")):
    """Issue and record a YooKassa refund; only admins with CSRF may invoke it."""
    account = _require_admin(request)
    if account is None:
        return RedirectResponse("/login", status_code=303)
    if not csrf.verify(request, csrf_token):
        return RedirectResponse("/admin", status_code=303)
    try:
        import yookassa_client
        # Validate the local payment before any external financial action.
        # Pending refunds reserve money too: do not request it a second time.
        with control_db.connect() as conn:
            payment = conn.execute("SELECT * FROM payments WHERE yookassa_payment_id=?", (payment_id,)).fetchone()
            if payment is None or payment["status"] != "succeeded":
                raise ValueError("Платёж не найден")
            if accounts.get_account_by_id(conn, payment["account_id"], include_deleted=True) is None:
                raise ValueError("Аккаунт платежа не найден")
            price = billing._refund_cents(payment["amount_rub"])
            reserved = sum(billing._refund_cents(row["amount_rub"]) for row in conn.execute(
                "SELECT amount_rub FROM refunds WHERE payment_id=? AND status IN ('pending','succeeded')",
                (payment["id"],)).fetchall())
            remaining = price - reserved
            requested = remaining if amount is None else billing._refund_cents(amount)
            if requested <= 0 or requested > remaining:
                raise ValueError("Возврат превышает доступный остаток платежа")
            payment_account_id = int(payment["account_id"])
        refund = yookassa_client.create_refund(payment_id, amount_rub=requested / 100)
        if not isinstance(refund, dict):
            raise ValueError("Недопустимый ответ YooKassa")
        refund_id = refund.get("id")
        refund_status = refund.get("status")
        refund_amount = refund.get("amount")
        if (not isinstance(refund_id, str) or not refund_id.strip()
                or refund.get("payment_id") != payment_id
                or refund_status not in {"pending", "succeeded", "canceled"}
                or not isinstance(refund_amount, dict) or refund_amount.get("currency") != "RUB"):
            raise ValueError("Ответ YooKassa не соответствует возврату")
        verified_cents = billing._refund_cents(refund_amount.get("value"))
        if verified_cents != requested:
            raise ValueError("Сумма ответа YooKassa не соответствует запросу")
        with control_db.connect() as conn:
            billing.apply_refund(conn, payment_account_id, payment_id, verified_cents / 100,
                                 refund_id, status=refund_status)
    except Exception:
        logger.exception("admin: failed to refund payment")
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/billing-operation/{operation_id}/reconcile")
def admin_reconcile_payment(request: Request, operation_id: str, provider_payment_id: str = Form(...), csrf_token: str = Form("")):
    """Read-only upstream reconciliation; never resend a charge or auto-refund."""
    if _require_admin(request) is None:
        return RedirectResponse("/login", status_code=303)
    if not csrf.verify(request, csrf_token):
        return RedirectResponse("/admin", status_code=303)
    from logic import account_lifecycle
    import yookassa_client
    from fastapi.responses import PlainTextResponse
    with control_db.connect() as conn:
        claim=conn.execute("SELECT * FROM billing_operation_claims WHERE operation_id=?",(operation_id,)).fetchone()
    if claim is None:
        return PlainTextResponse("Операция не найдена",status_code=404)
    import uuid
    try:
        provider_payment_id=str(uuid.UUID(provider_payment_id))
    except (ValueError,AttributeError):
        return PlainTextResponse("Некорректный идентификатор платежа",status_code=422)
    try:
        verified=yookassa_client.get_payment(provider_payment_id)
    except Exception:
        return PlainTextResponse("Не удалось сверить платёж; операция остаётся заблокированной",status_code=502)
    if not isinstance(verified,dict) or verified.get("id") != provider_payment_id:
        return PlainTextResponse("Идентификатор платежа не совпадает",status_code=409)
    try:
        with control_db.connect() as conn:
            claim=account_lifecycle.bind_verified_provider(conn,int(claim["account_id"]),operation_id,verified)
            if conn.execute("SELECT 1 FROM payments WHERE yookassa_payment_id=?",(verified["id"],)).fetchone() is None:
                billing.record_payment_attempt(conn,int(claim["account_id"]),verified["id"],
                    "recurring" if claim["operation_key"].startswith("recurring:") else "manual",
                    float(claim["expected_amount_rub"]))
            if verified.get("status")=="succeeded":
                billing.apply_successful_payment(conn,int(claim["account_id"]),verified["id"],is_test=bool(verified.get("test",False)))
            elif verified.get("status")=="canceled":
                billing.apply_failed_payment(conn,int(claim["account_id"]),verified["id"],error="canceled")
    except account_lifecycle.LifecycleConflict:
        return PlainTextResponse("Платёж не соответствует операции",status_code=409)
    return RedirectResponse("/admin",status_code=303)


@router.post("/admin/payment/{payment_id}/reconcile")
def admin_reconcile_known_payment(request: Request, payment_id: str, csrf_token: str = Form("")):
    """Resolve pre-claim legacy pending records using authenticated provider GET."""
    from fastapi.responses import PlainTextResponse
    from decimal import Decimal,InvalidOperation
    import uuid,yookassa_client
    if _require_admin(request) is None:
        return RedirectResponse("/login",status_code=303)
    if not csrf.verify(request,csrf_token):
        return RedirectResponse("/admin",status_code=303)
    try:payment_id=str(uuid.UUID(payment_id))
    except (ValueError,AttributeError):return PlainTextResponse("Некорректный идентификатор платежа",status_code=422)
    with control_db.connect() as conn:
        original=conn.execute("SELECT * FROM payments WHERE yookassa_payment_id=?",(payment_id,)).fetchone()
    if original is None:return PlainTextResponse("Платёж не найден",status_code=404)
    try:verified=yookassa_client.get_payment(payment_id)
    except Exception:return PlainTextResponse("Не удалось сверить платёж",status_code=502)
    try:
        amount=verified.get("amount") or {};metadata=verified.get("metadata") or {}
        valid=verified.get("id")==payment_id and str(metadata.get("account_id"))==str(original["account_id"]) and amount.get("currency")=="RUB" and Decimal(str(amount.get("value")))==Decimal(str(original["amount_rub"]))
    except (InvalidOperation,ValueError,TypeError,AttributeError):valid=False
    if not valid:return PlainTextResponse("Данные платежа не совпадают",status_code=409)
    with control_db.connect() as conn:
        if verified.get("status")=="succeeded":
            billing.apply_successful_payment(conn,int(original["account_id"]),payment_id,is_test=bool(verified.get("test",False)))
        elif verified.get("status")=="canceled":
            billing.apply_failed_payment(conn,int(original["account_id"]),payment_id,error="canceled")
    return RedirectResponse("/admin",status_code=303)
