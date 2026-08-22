"""Gateway FastAPI app: registration, login, session cookies, and the
Traefik ForwardAuth endpoint that gates every tenant subdomain.

This file is intentionally thin -- almost everything it does is call into
logic/accounts.py (unit-tested, see tests/test_accounts.py) or
provisioning.py (Docker orchestration). If you're debugging "why did login
fail" or "why does a client have access they shouldn't", suspect
logic/accounts.py first and re-run its test suite; this file should mostly
just be marshalling HTTP <-> those functions.

Cannot be executed in the sandbox this was authored in (no network access
to install fastapi/uvicorn/jinja2) -- must be smoke-tested on a real host
before any paying customer touches it. See ../DEPLOY.md.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import config
import mailer
from billing_routes import router as billing_router
from logic import accounts, db as control_db, password_reset
from rate_limiter import RateLimiter
from telegram_notifier import send_telegram_alert

logger = logging.getLogger("wb_saas_gateway")

# Limits auth POST routes to 10 requests per 60 seconds per client IP
auth_rate_limiter = RateLimiter(requests_per_window=10, window_seconds=60)

app = FastAPI(title="MARKETSHELPER Gateway")
app.include_router(billing_router)
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).resolve().parent / "static")),
    name="static",
)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def _current_account(request: Request):
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    if not token:
        return None
    with control_db.connect() as conn:
        return accounts.resolve_session(conn, token)


def _set_session_cookie(response: Response, token: str) -> None:
    kwargs = dict(
        key=config.SESSION_COOKIE_NAME,
        value=token,
        max_age=accounts.SESSION_TTL_HOURS * 3600,
        httponly=True,
        samesite="lax",
        secure=config.COOKIE_SECURE,
        path="/",
    )
    domain = config.cookie_domain()
    if domain:
        kwargs["domain"] = domain
    response.set_cookie(**kwargs)


def _clear_session_cookie(response: Response) -> None:
    domain = config.cookie_domain()
    response.delete_cookie(config.SESSION_COOKIE_NAME, path="/", domain=domain or None)


@app.on_event("startup")
async def _startup() -> None:
    with control_db.connect() as conn:
        control_db.init_db(conn)

    # Background task to periodically check and stop idle tenant containers
    import asyncio
    async def _idle_check_loop():
        import provisioning
        while True:
            await asyncio.sleep(300)  # Check every 5 minutes
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, provisioning.stop_idle_tenants)
            except Exception as exc:
                logger.error("Error in background idle stop check: %s", exc)

    asyncio.create_task(_idle_check_loop())


# --------------------------------------------------------------------------
# Public pages
# --------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    account = _current_account(request)
    if account is not None:
        return _redirect_for_account(account)
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "price_rub": config.SUBSCRIPTION_PRICE_RUB,
            "period_days": config.BILLING_PERIOD_DAYS,
            "trial_days": config.TRIAL_DAYS,
            "current_year": datetime.now(timezone.utc).year,
        },
    )


# Public informational pages -- required by YooKassa's site review before it
# will enable payments (real pricing/description, offer/terms, contacts +
# legal details), and just generally good practice for a paid service.
# Deliberately unauthenticated and linked from home.html's footer so both
# reviewers and customers can find them without logging in.

@app.get("/pricing", response_class=HTMLResponse)
def pricing_page(request: Request):
    return templates.TemplateResponse(
        request,
        "pricing.html",
        {
            "price_rub": config.SUBSCRIPTION_PRICE_RUB,
            "period_days": config.BILLING_PERIOD_DAYS,
            "trial_days": config.TRIAL_DAYS,
        },
    )


@app.get("/offer", response_class=HTMLResponse)
def offer_page(request: Request):
    return templates.TemplateResponse(request, "offer.html")


@app.get("/contacts", response_class=HTMLResponse)
def contacts_page(request: Request):
    return templates.TemplateResponse(request, "contacts.html")


@app.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request):
    return templates.TemplateResponse(request, "privacy.html")


@app.get("/register", response_class=HTMLResponse)
def register_form(request: Request):
    account = _current_account(request)
    if account is not None:
        return _redirect_for_account(account)
    return templates.TemplateResponse(request, "register.html")


@app.post("/register")
def register_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    email: str = Form(...),
    password: str = Form(...),
    # A checkbox only appears in the POST body at all when checked -- an
    # unchecked box means this arrives as None, not "false", hence Optional
    # rather than a plain bool Form field (which would 422 on omission).
    pdn_consent: str | None = Form(None),
):
    client_ip = request.client.host if request.client else "unknown"
    if not auth_rate_limiter.is_allowed(f"register:{client_ip}"):
        return templates.TemplateResponse(
            request, "register.html", {"error": "Слишком много попыток. Попробуйте позже.", "email": email}, status_code=429
        )
    with control_db.connect() as conn:
        try:
            account_id = accounts.create_account(conn, email, password, pdn_consent=bool(pdn_consent))
            slug = accounts.generate_unique_slug(conn, email)
            tenant_id = accounts.create_tenant_instance(conn, account_id, slug)
        except ValueError as exc:
            return templates.TemplateResponse(
                request, "register.html", {"error": str(exc), "email": email}, status_code=400
            )
        token = accounts.create_session(conn, account_id)

    # Provisioning talks to Docker and can take real wall-clock time (pulling
    # an image, starting a container, waiting for a health check) -- it runs
    # after the response is sent so the user isn't stuck waiting on this request.
    import provisioning  # local import: keeps this module importable/testable without docker installed

    background_tasks.add_task(provisioning.provision_tenant_background, account_id, tenant_id, slug)

    response = RedirectResponse("/provisioning", status_code=303)
    _set_session_cookie(response, token)
    return response


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "", reset: str = ""):
    account = _current_account(request)
    if account is not None:
        return _redirect_for_account(account)
    return templates.TemplateResponse(
        request, "login.html", {"next": next, "password_was_reset": bool(reset)}
    )


@app.post("/login")
def login_submit(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("")):
    client_ip = request.client.host if request.client else "unknown"
    if not auth_rate_limiter.is_allowed(f"login:{client_ip}"):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Слишком много попыток входа. Пожалуйста, подождите минуту.", "email": email, "next": next},
            status_code=429,
        )
    with control_db.connect() as conn:
        account = accounts.authenticate_account(conn, email, password)
        if account is None:
            return templates.TemplateResponse(
                request,
                "login.html",
                {"error": "Неверный email или пароль.", "email": email, "next": next},
                status_code=400,
            )
        token = accounts.create_session(conn, account["id"])

    # Only ever honour a same-origin relative "next" (never redirect to an
    # arbitrary absolute URL from a query param -- that's an open-redirect
    # vector). auth_verify() below passes the originally-requested tenant
    # URL as an absolute URL, so it deliberately falls through to
    # _redirect_for_account() instead: the user lands on their own
    # dashboard root rather than the exact deep link, which is an
    # acceptable trade-off for not trusting redirect targets from a URL a
    # user could hand-craft.
    if next and next.startswith("/") and not next.startswith("//"):
        response = RedirectResponse(next, status_code=303)
    else:
        response = _redirect_for_account(account)
    _set_session_cookie(response, token)
    return response


@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_form(request: Request):
    account = _current_account(request)
    if account is not None:
        return _redirect_for_account(account)
    return templates.TemplateResponse(request, "forgot_password.html")


@app.post("/forgot-password")
def forgot_password_submit(request: Request, background_tasks: BackgroundTasks, email: str = Form(...)):
    client_ip = request.client.host if request.client else "unknown"
    if not auth_rate_limiter.is_allowed(f"forgot:{client_ip}"):
        return templates.TemplateResponse(
            request,
            "forgot_password.html",
            {"error": "Слишком много запросов на сброс пароля. Попробуйте позже."},
            status_code=429,
        )
    # Always show the exact same confirmation regardless of whether the
    # email is registered -- see logic/password_reset.py's module docstring
    # for why. The only difference between "known" and "unknown" email is
    # an email that never gets sent, which isn't observable from here.
    with control_db.connect() as conn:
        token = password_reset.request_password_reset(conn, email)
    if token:
        reset_url = f"{config.base_url()}/reset-password?{urlencode({'token': token})}"
        background_tasks.add_task(
            mailer.send_password_reset_email, email.strip().lower(), reset_url, config.PASSWORD_RESET_TTL_MINUTES
        )
    return templates.TemplateResponse(request, "forgot_password.html", {"submitted": True})


@app.get("/reset-password", response_class=HTMLResponse)
def reset_password_form(request: Request, token: str = ""):
    if not token:
        return RedirectResponse("/forgot-password", status_code=303)
    return templates.TemplateResponse(request, "reset_password.html", {"token": token})


@app.post("/reset-password")
def reset_password_submit(
    request: Request, token: str = Form(...), password: str = Form(...), password_confirm: str = Form(...),
):
    if password != password_confirm:
        return templates.TemplateResponse(
            request,
            "reset_password.html",
            {"token": token, "error": "Пароли не совпадают."},
            status_code=400,
        )
    with control_db.connect() as conn:
        try:
            password_reset.consume_reset_token(conn, token, password)
        except ValueError as exc:
            return templates.TemplateResponse(
                request, "reset_password.html", {"token": token, "error": str(exc)}, status_code=400
            )
    # Every session for the account was just revoked by consume_reset_token
    # (including, if it existed, whatever session this browser was holding)
    # -- send the user to a normal login with their new password.
    response = RedirectResponse("/login?" + urlencode({"reset": "1"}), status_code=303)
    _clear_session_cookie(response)
    return response


@app.post("/logout")
def logout(request: Request):
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    if token:
        with control_db.connect() as conn:
            accounts.revoke_session(conn, token)
    response = RedirectResponse("/", status_code=303)
    _clear_session_cookie(response)
    return response


@app.get("/provisioning", response_class=HTMLResponse)
def provisioning_status(request: Request):
    account = _current_account(request)
    if account is None:
        return RedirectResponse("/login", status_code=303)
    with control_db.connect() as conn:
        tenant = accounts.get_tenant_for_account(conn, account["id"])
    if tenant is not None and tenant["status"] == "running" and accounts.account_has_access(account):
        return RedirectResponse(config.tenant_url(tenant["slug"]), status_code=303)
    status = tenant["status"] if tenant is not None else "provisioning"
    return templates.TemplateResponse(request, "provisioning.html", {"status": status})


def _redirect_for_account(account) -> RedirectResponse:
    with control_db.connect() as conn:
        tenant = accounts.get_tenant_for_account(conn, account["id"])
    if tenant is not None and tenant["status"] == "running" and accounts.account_has_access(account):
        return RedirectResponse(config.tenant_url(tenant["slug"]), status_code=303)
    return RedirectResponse("/provisioning", status_code=303)


# --------------------------------------------------------------------------
# Admin Dashboard
# --------------------------------------------------------------------------

@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, msg: str = "", err: str = ""):
    account = _current_account(request)
    if account is None or not accounts.is_admin_account(account):
        return RedirectResponse("/login", status_code=303)

    with control_db.connect() as conn:
        tenants = accounts.list_all_tenants_overview(conn)

    return templates.TemplateResponse(
        request,
        "admin_dashboard.html",
        {
            "tenants": tenants,
            "parent_domain": config.PARENT_DOMAIN,
            "message": msg,
            "error": err,
        },
    )


@app.post("/admin/tenant/{slug}/action")
def admin_tenant_action(request: Request, slug: str, action: str = Form(...)):
    account = _current_account(request)
    if account is None or not accounts.is_admin_account(account):
        return RedirectResponse("/login", status_code=303)

    import provisioning
    msg = ""
    err = ""

    if action == "wake":
        ok = provisioning.wake_tenant(slug)
        if ok:
            msg = f"Контейнер {slug} успешно запущен."
        else:
            err = f"Не удалось запустить контейнер {slug}."
    elif action == "stop":
        provisioning.stop_tenant(slug)
        msg = f"Контейнер {slug} остановлен."

    from urllib.parse import urlencode
    query = urlencode({k: v for k, v in [("msg", msg), ("err", err)] if v})
    return RedirectResponse(f"/admin?{query}" if query else "/admin", status_code=303)


@app.post("/admin/account/{account_id}/extend")
def admin_account_extend(request: Request, account_id: int, days: int = Form(30)):
    account = _current_account(request)
    if account is None or not accounts.is_admin_account(account):
        return RedirectResponse("/login", status_code=303)

    with control_db.connect() as conn:
        acc = accounts.get_account_by_id(conn, account_id)
        if acc is not None:
            from datetime import datetime, timedelta, timezone
            current_end_str = acc["current_period_end"]
            try:
                current_dt = datetime.fromisoformat(current_end_str) if current_end_str else datetime.now(timezone.utc)
            except Exception:
                current_dt = datetime.now(timezone.utc)
            if current_dt < datetime.now(timezone.utc):
                current_dt = datetime.now(timezone.utc)

            new_end = (current_dt + timedelta(days=days)).isoformat(timespec="seconds")
            conn.execute(
                "UPDATE accounts SET current_period_end=?, status='active', past_due_since='', updated_at=? WHERE id=?",
                (new_end, datetime.now(timezone.utc).isoformat(timespec="seconds"), account_id),
            )

    return RedirectResponse("/admin?msg=" + urlencode({"m": f"Подписка продлена на {days} дней."})[2:], status_code=303)


# --------------------------------------------------------------------------
# Traefik ForwardAuth: gates every request to *.{TENANT_SUBDOMAIN_PREFIX}.{PARENT_DOMAIN}
# --------------------------------------------------------------------------

@app.get("/auth/verify")
def auth_verify(request: Request):
    """Traefik calls this for every request to a tenant subdomain (via the
    forwardauth middleware, see traefik/dynamic.yml). A 2xx response lets the
    original request through to the tenant's container; anything else is
    sent back to the browser verbatim, so we redirect straight to /login.
    """
    forwarded_host = request.headers.get("x-forwarded-host", "")
    forwarded_proto = request.headers.get("x-forwarded-proto", "https")
    forwarded_uri = request.headers.get("x-forwarded-uri", "/")
    slug = _slug_from_host(forwarded_host)

    account = _current_account(request)
    login_url = f"{('https' if config.COOKIE_SECURE else 'http')}://{config.PARENT_DOMAIN}/login"

    if account is None or not accounts.account_has_access(account) or not slug:
        return _redirect_to_login(login_url, forwarded_proto, forwarded_host, forwarded_uri)

    with control_db.connect() as conn:
        tenant = accounts.get_tenant_for_account(conn, account["id"])
        if tenant is None or tenant["slug"] != slug:
            return _redirect_to_login(login_url, forwarded_proto, forwarded_host, forwarded_uri)

        # If tenant is stopped (e.g. idle auto-sleep), attempt on-demand auto-wake
        if tenant["status"] == "stopped":
            import provisioning
            if not provisioning.wake_tenant(slug):
                return RedirectResponse(f"{config.base_url()}/provisioning", status_code=302)
            tenant = accounts.get_tenant_for_account(conn, account["id"])

        if tenant is None or tenant["status"] != "running":
            return _redirect_to_login(login_url, forwarded_proto, forwarded_host, forwarded_uri)

        accounts.touch_tenant_activity(conn, tenant["id"])

    return Response(status_code=200)


def _slug_from_host(host: str) -> str:
    host = (host or "").split(":", 1)[0]
    suffix = f".{config.TENANT_SUBDOMAIN_PREFIX}.{config.PARENT_DOMAIN}"
    if host.endswith(suffix):
        return host[: -len(suffix)]
    return ""


def _redirect_to_login(login_url: str, proto: str, host: str, uri: str) -> RedirectResponse:
    # Matches the "next" query param login_form()/login_submit() read. This
    # will be an absolute URL (the tenant subdomain being requested), which
    # login_submit() deliberately does NOT redirect to directly -- see the
    # comment there. It's still worth passing along for a future "which
    # workspace were you trying to reach" message.
    original = f"{proto}://{host}{uri}"
    return RedirectResponse(f"{login_url}?{urlencode({'next': original})}", status_code=302)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
