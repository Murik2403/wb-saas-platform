"""Internal API for tenant containers to reach the gateway.

Tenant containers are deliberately NOT on the gateway's docker network (see
provisioning.py's per-tenant network isolation, added so tenants can't reach
each other) -- so a tenant container calls this over the same public HTTPS
domain everything else uses, not a docker-internal hostname. Authenticated
by a shared secret (config.INTERNAL_API_SECRET) baked into every tenant
container's environment at provision time, not a user session -- there is
no human in the loop for this call, so no CSRF/cookie auth applies here.

Callers are tenant-app/reports/delivery.py (email and Telegram delivery of
a scheduled PDF report) and tenant-app/pages/reports_page.py (Telegram
link-code issuance/status, so a user can connect their chat).
"""
from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

import config
import mailer
import telegram_bot
from logic import accounts, db as control_db

logger = logging.getLogger("wb_saas_gateway.internal")

router = APIRouter()


def _secret_is_valid(provided: str) -> bool:
    # Empty INTERNAL_API_SECRET means the feature is unconfigured -- refuse
    # everything rather than accepting an empty-string secret as valid.
    return bool(config.INTERNAL_API_SECRET) and hmac.compare_digest(provided, config.INTERNAL_API_SECRET)


def deliver_report_email(slug: str, report_name: str, pdf_bytes: bytes, filename: str) -> None:
    """Looks up the account owning `slug` and emails it the report. Raises
    HTTPException on any failure (unknown tenant/account, SMTP failure) --
    kept separate from the route function so it's testable without spinning
    up a real ASGI request (see tests/test_internal_routes.py)."""
    with control_db.connect() as conn:
        tenant = accounts.get_tenant_by_slug(conn, slug)
        if tenant is None:
            raise HTTPException(status_code=404, detail="unknown tenant")
        account = accounts.get_account_by_id(conn, int(tenant["account_id"]))
    if account is None:
        raise HTTPException(status_code=404, detail="unknown account")

    ok = mailer.send_report_email(account["email"], report_name, pdf_bytes, filename)
    if not ok:
        raise HTTPException(status_code=502, detail="email delivery failed")


@router.post("/internal/send-report-email")
async def send_report_email(
    request: Request,
    slug: str = Form(...),
    report_name: str = Form(...),
    file: UploadFile = File(...),
):
    if not _secret_is_valid(request.headers.get("x-internal-secret", "")):
        raise HTTPException(status_code=403, detail="forbidden")

    pdf_bytes = await file.read()
    deliver_report_email(slug, report_name, pdf_bytes, file.filename or "report.pdf")
    return JSONResponse({"status": "ok"})


def create_telegram_link_code(slug: str) -> str:
    """Raises HTTPException(404) for an unknown slug. Separate from the
    route function for the same testability reason as deliver_report_email."""
    with control_db.connect() as conn:
        tenant = accounts.get_tenant_by_slug(conn, slug)
        if tenant is None:
            raise HTTPException(status_code=404, detail="unknown tenant")
        return accounts.create_telegram_link_code(conn, int(tenant["account_id"]))


def telegram_is_linked(slug: str) -> bool:
    with control_db.connect() as conn:
        return bool(accounts.get_telegram_chat_id_for_slug(conn, slug))


def deliver_report_telegram(slug: str, report_name: str, pdf_bytes: bytes, filename: str) -> None:
    with control_db.connect() as conn:
        chat_id = accounts.get_telegram_chat_id_for_slug(conn, slug)
    if not chat_id:
        raise HTTPException(status_code=409, detail="telegram not linked")
    try:
        telegram_bot.send_document(chat_id, pdf_bytes, filename, caption=f"Отчёт «{report_name}» — MARKETSHELPER")
    except Exception:
        logger.exception("Failed to send Telegram document for slug %s", slug)
        raise HTTPException(status_code=502, detail="telegram delivery failed")


@router.post("/internal/telegram-link-code")
def telegram_link_code(request: Request, slug: str = Form(...)):
    if not _secret_is_valid(request.headers.get("x-internal-secret", "")):
        raise HTTPException(status_code=403, detail="forbidden")
    code = create_telegram_link_code(slug)
    return JSONResponse({"code": code, "ttl_minutes": accounts.TELEGRAM_LINK_CODE_TTL_MINUTES})


@router.get("/internal/telegram-status")
def telegram_status(request: Request, slug: str):
    if not _secret_is_valid(request.headers.get("x-internal-secret", "")):
        raise HTTPException(status_code=403, detail="forbidden")
    return JSONResponse({"linked": telegram_is_linked(slug)})


@router.post("/internal/send-report-telegram")
async def send_report_telegram(
    request: Request,
    slug: str = Form(...),
    report_name: str = Form(...),
    file: UploadFile = File(...),
):
    if not _secret_is_valid(request.headers.get("x-internal-secret", "")):
        raise HTTPException(status_code=403, detail="forbidden")

    pdf_bytes = await file.read()
    deliver_report_telegram(slug, report_name, pdf_bytes, file.filename or "report.pdf")
    return JSONResponse({"status": "ok"})
