"""Internal API for tenant containers to reach the gateway.

Tenant containers are deliberately NOT on the gateway's docker network (see
provisioning.py's per-tenant network isolation, added so tenants can't reach
each other) -- so a tenant container calls this over the same public HTTPS
domain everything else uses, not a docker-internal hostname. Authenticated
by a tenant-scoped secret (config.INTERNAL_API_SECRET + slug) baked into each
tenant container's environment at provision time, not a user session -- there
is no human in the loop for this call, so no CSRF/cookie auth applies here.

Callers are tenant-app/reports/delivery.py (email and Telegram delivery of
a scheduled PDF report) and tenant-app/pages/reports_page.py (Telegram
link-code issuance/status, so a user can connect their chat).
"""
from __future__ import annotations

from internal_auth import verify_credential
import logging

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

import config
import mailer
import telegram_bot
from logic import accounts, db as control_db

logger = logging.getLogger("wb_saas_gateway.internal")

router = APIRouter()
MAX_REPORT_UPLOAD_BYTES = config.MAX_REPORT_UPLOAD_BYTES


class TenantMemberRoleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: StrictStr = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    account: StrictStr = Field(pattern=r"^[1-9][0-9]{0,18}$")

    @field_validator("account")
    @classmethod
    def sqlite_account_id(cls, value: str) -> str:
        if int(value) > 2**63 - 1:
            raise ValueError("invalid account identifier")
        return value


@router.post("/internal/tenant-member-role")
def tenant_member_role(payload: TenantMemberRoleInput, request: Request):
    """Fresh membership for a bounded scheduled delegation, not an access grant.

    Auth uses the existing exact tenant credential. Unknown, revoked and
    unavailable memberships all return the same null result. No global account
    lookup details or credentials are exposed and successful replies are not cached.
    """
    if not _secret_is_valid(request.headers.get("x-internal-secret", ""), payload.slug):
        raise HTTPException(status_code=403, detail="forbidden")
    role = None
    with control_db.connect() as conn:
        conn.execute("BEGIN")
        tenant = accounts.get_tenant_by_slug(conn, payload.slug)
        account = accounts.get_account_by_id(conn, int(payload.account))
        if tenant is not None and tenant["status"] == "running" and accounts.tenant_has_access(conn, tenant) and accounts.is_email_verified(account):
            membership = accounts.get_tenant_membership(conn, int(tenant["id"]), int(payload.account))
            if membership is not None and membership["role"] in accounts.TENANT_ROLES:
                role = membership["role"]
    return JSONResponse({"role": role}, headers={"Cache-Control": "no-store"})


def _secret_is_valid(provided: str, slug: str = "") -> bool:
    # Empty INTERNAL_API_SECRET means the feature is unconfigured -- refuse
    # everything rather than accepting an empty-string secret as valid.
    return verify_credential(config.INTERNAL_API_SECRET, slug, provided)


async def _read_report_pdf(file: UploadFile) -> bytes:
    """Read one bounded report payload without materializing an unlimited file."""
    pdf_bytes = await file.read(MAX_REPORT_UPLOAD_BYTES + 1)
    if len(pdf_bytes) > MAX_REPORT_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="report file is too large")
    return pdf_bytes


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

    status = mailer.send_report_email(account["email"], report_name, pdf_bytes, filename)
    # ``True`` remains accepted for older test doubles/integrations while the
    # real mailer returns the explicit transport state.
    if status != "accepted" and status is not True:
        raise HTTPException(status_code=502, detail="email delivery failed")


@router.post("/internal/send-report-email")
async def send_report_email(
    request: Request,
    slug: str = Form(...),
    report_name: str = Form(...),
    file: UploadFile = File(...),
):
    if not _secret_is_valid(request.headers.get("x-internal-secret", ""), slug):
        raise HTTPException(status_code=403, detail="forbidden")

    pdf_bytes = await _read_report_pdf(file)
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


def report_delivery_status(slug: str) -> dict:
    with control_db.connect() as conn:
        tenant = accounts.get_tenant_by_slug(conn, slug)
        if tenant is None:
            raise HTTPException(status_code=404, detail="unknown tenant")
        account = accounts.get_account_by_id(conn, int(tenant["account_id"]))
        linked = bool(accounts.get_telegram_chat_id_for_slug(conn, slug))
    if account is None:
        raise HTTPException(status_code=404, detail="unknown account")
    bot_username = telegram_bot.get_bot_username()
    return {
        "linked": linked,
        "email": account["email"],
        "email_configured": bool(config.SMTP_HOST),
        "telegram_configured": bool(config.TELEGRAM_BOT_TOKEN and bot_username),
        "bot_username": bot_username,
    }


def deliver_report_telegram(slug: str, report_name: str, pdf_bytes: bytes, filename: str) -> None:
    with control_db.connect() as conn:
        chat_id = accounts.get_telegram_chat_id_for_slug(conn, slug)
    if not chat_id:
        raise HTTPException(status_code=409, detail="telegram not linked")
    try:
        result = telegram_bot.send_document(chat_id, pdf_bytes, filename, caption=f"Отчёт «{report_name}» — MARKETSHELPER")
        # Telegram may return HTTP 200 for an application-level failure.
        # Treat only an explicit Bot API `ok: true` response as delivered;
        # otherwise the tenant report run must expose a delivery error.
        if not isinstance(result, dict) or result.get("ok") is not True:
            logger.error("Telegram rejected report for slug %s: %r", slug, result)
            raise HTTPException(status_code=502, detail="telegram delivery failed")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to send Telegram document for slug %s", slug)
        raise HTTPException(status_code=502, detail="telegram delivery failed")


@router.post("/internal/telegram-link-code")
def telegram_link_code(request: Request, slug: str = Form(...)):
    if not _secret_is_valid(request.headers.get("x-internal-secret", ""), slug):
        raise HTTPException(status_code=403, detail="forbidden")
    code = create_telegram_link_code(slug)
    return JSONResponse({"code": code, "ttl_minutes": accounts.TELEGRAM_LINK_CODE_TTL_MINUTES})


@router.get("/internal/telegram-status")
def telegram_status(request: Request, slug: str):
    if not _secret_is_valid(request.headers.get("x-internal-secret", ""), slug):
        raise HTTPException(status_code=403, detail="forbidden")
    return JSONResponse(report_delivery_status(slug))


@router.post("/internal/send-report-telegram")
async def send_report_telegram(
    request: Request,
    slug: str = Form(...),
    report_name: str = Form(...),
    file: UploadFile = File(...),
):
    if not _secret_is_valid(request.headers.get("x-internal-secret", ""), slug):
        raise HTTPException(status_code=403, detail="forbidden")

    pdf_bytes = await _read_report_pdf(file)
    deliver_report_telegram(slug, report_name, pdf_bytes, file.filename or "report.pdf")
    return JSONResponse({"status": "ok"})
