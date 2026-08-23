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
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
from logic import accounts, billing, csrf, db as control_db

logger = logging.getLogger("wb_saas_gateway.admin")

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def _current_account(request: Request):
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    if not token:
        return None
    with control_db.connect() as conn:
        return accounts.resolve_session(conn, token)


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
        return RedirectResponse("/login", status_code=303)

    with control_db.connect() as conn:
        tenants = accounts.list_all_tenants_overview(conn)

    csrf_token = csrf.get_or_create_token(request)
    response = templates.TemplateResponse(
        request,
        "admin_dashboard.html",
        {"tenants": tenants, "csrf_token": csrf_token},
    )
    csrf.set_cookie(response, csrf_token)
    return response


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
