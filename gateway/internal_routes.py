"""Internal API for tenant containers to reach the gateway.

Tenant containers are deliberately NOT on the gateway's docker network (see
provisioning.py's per-tenant network isolation, added so tenants can't reach
each other) -- so a tenant container calls this over the same public HTTPS
domain everything else uses, not a docker-internal hostname. Authenticated
by a shared secret (config.INTERNAL_API_SECRET) baked into every tenant
container's environment at provision time, not a user session -- there is
no human in the loop for this call, so no CSRF/cookie auth applies here.

Currently the only caller is tenant-app/reports/report_scheduler.py,
emailing a scheduled PDF report to the account's registered address.
"""
from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

import config
import mailer
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
