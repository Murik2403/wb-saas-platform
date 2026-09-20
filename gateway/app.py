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
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import config
import mailer
import password_breach
import growth_web
from client_ip import client_ip as _client_ip
from admin_routes import router as admin_router
from billing_routes import router as billing_router
from internal_routes import router as internal_router
from growth_internal import router as growth_internal_router
from logging_config import StructuredLoggingMiddleware, bind_account_context, setup_logging
from logic import accounts, compliance, csrf, db as control_db, password_reset, growth, trial_emails
from rate_limiter import RateLimiter
from sentry_integration import initialize as initialize_sentry
from template_setup import create_templates
from marketing_routes import create_marketing_router, llms_txt, PUBLIC_MARKETING_PATHS

setup_logging()
initialize_sentry()
logger = logging.getLogger("wb_saas_gateway")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    with control_db.connect() as conn:
        control_db.init_db(conn)
    yield


app = FastAPI(title="MARKETSHELPER Gateway", lifespan=_lifespan)
app.add_middleware(StructuredLoggingMiddleware)
app.add_middleware(growth_web.AcquisitionMiddleware)
app.include_router(growth_web.router)
app.include_router(billing_router)
app.include_router(admin_router)
app.include_router(internal_router)
app.include_router(growth_internal_router)
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).resolve().parent / "static")),
    name="static",
)
templates = create_templates()
app.include_router(create_marketing_router(templates))


def _render_auth_form_error(request: Request, template_name: str, context: dict, status_code: int):
    """Re-render a login/registration form with a matching fresh CSRF cookie."""
    csrf_token = csrf.get_or_create_token(request)
    context = {
        **context,
        "csrf_token": csrf_token,
        "policy_version": accounts.PDN_POLICY_VERSION,
    }
    response = templates.TemplateResponse(
        request, template_name, context, status_code=status_code
    )
    csrf.set_cookie(response, csrf_token)
    return response

# Brute-force protection on /login, /register, /forgot-password. In-memory
# in `gateway/logic/db.py`, so a gateway restart does not wipe protection
# state for active attackers.
auth_rate_limiter = RateLimiter(
    requests_per_window=10,
    window_seconds=60,
    storage_backend="db",
    db_connect=control_db.connect,
)


def _password_policy_error(password: str) -> tuple[str, int] | None:
    """Return a safe form error after an HIBP range check, if any.

    This runs only at full form submission. It deliberately does not affect
    login, and a remote failure occurs before a registration/reset DB write.
    """
    issues = accounts.password_issues(password)
    if issues:
        return " ".join(issues), 400

    try:
        count = password_breach.pwned_password_count(
            password, timeout_seconds=config.HIBP_PASSWORD_TIMEOUT_SECONDS
        )
    except password_breach.PasswordBreachCheckError:
        return "Не удалось проверить пароль по базе утечек. Попробуйте ещё раз.", 503
    if count:
        return "Этот пароль встречался в известных утечках. Выберите другой.", 400
    return None


def _current_account(request: Request):
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    if not token:
        return None
    with control_db.connect() as conn:
        account = accounts.resolve_session(conn, token)
    if account is not None:
        bind_account_context(account["id"])
    return account


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
            "demo_url": config.tenant_url("demo"),
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
    return templates.TemplateResponse(request, "offer.html", {"policy_version": accounts.PDN_POLICY_VERSION})


@app.get("/compliance", response_class=HTMLResponse)
def compliance_page(request: Request):
    return templates.TemplateResponse(
        request,
        "compliance.html",
        {
            "policy_version": accounts.PDN_POLICY_VERSION,
            "compliance_status": compliance.get_compliance_status(),
        },
    )


@app.get("/contacts", response_class=HTMLResponse)
def contacts_page(request: Request):
    return templates.TemplateResponse(
        request,
        "contacts.html",
        {"policy_version": accounts.PDN_POLICY_VERSION},
    )


@app.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request):
    return templates.TemplateResponse(request, "privacy.html", {"policy_version": accounts.PDN_POLICY_VERSION})


# --------------------------------------------------------------------------
# Crawler-facing endpoints
# --------------------------------------------------------------------------

# The public, indexable surface of the gateway. Everything else (auth forms,
# /account, /billing, /admin, /provisioning) is both disallowed here and
# marked noindex in the templates, so a crawler that ignores robots.txt still
# does not put a customer's billing page into a search index.
_SITEMAP_PATHS: tuple[tuple[str, str, str], ...] = (
    # path, changefreq, priority
    ("/", "weekly", "1.0"),
    ("/pricing", "monthly", "0.9"),
    ("/contacts", "yearly", "0.4"),
    ("/compliance", "yearly", "0.3"),
    ("/offer", "yearly", "0.3"),
    ("/privacy", "yearly", "0.3"),
) + tuple((path, "monthly", "0.7") for path in PUBLIC_MARKETING_PATHS)

_ROBOTS_DISALLOW = (
    "/login",
    "/logout",
    "/account",
    "/billing",
    "/admin",
    "/provisioning",
    "/forgot-password",
    "/reset-password",
    "/verify-email",
    "/verify-email-sent",
    "/team/accept",
    "/auth/verify",
    "/internal/",
    "/webhooks/",
    "/healthz",
    "/unsubscribe",
)


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt() -> PlainTextResponse:
    lines = ["User-agent: *"]
    lines += [f"Disallow: {path}" for path in _ROBOTS_DISALLOW]
    lines += [
        "Allow: /",
        # Plain-text summary of the public site for AI crawlers and assistants.
        "Allow: /llms.txt",
        "",
        # Yandex is the search engine that matters for this audience and it is
        # the one that still reads Clean-param/Host-style directives.
        f"Sitemap: {config.base_url()}/sitemap.xml",
        "",
    ]
    return PlainTextResponse("\n".join(lines))


@app.get("/llms.txt", response_class=PlainTextResponse)
def llms_txt_endpoint() -> PlainTextResponse:
    """What MARKETSHELPER is, what it is not, and the public pages.

    Built from MARKETING_PAGES so it cannot drift from the site itself. It is
    deliberately absent from the sitemap: it is a description for assistants,
    not a page to index.
    """
    return PlainTextResponse(llms_txt(config.base_url()))


@app.get("/sitemap.xml")
def sitemap_xml() -> Response:
    base = config.base_url()
    urls = "".join(
        f"<url><loc>{base}{path}</loc>"
        f"<changefreq>{changefreq}</changefreq>"
        f"<priority>{priority}</priority></url>"
        for path, changefreq, priority in _SITEMAP_PATHS
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{urls}"
        "</urlset>"
    )
    return Response(content=body, media_type="application/xml")


@app.get("/register", response_class=HTMLResponse)
def register_form(request: Request, invite: str = ""):
    account = _current_account(request)
    if account is not None:
        return _redirect_for_account(account)
    csrf_token = csrf.get_or_create_token(request)
    response = templates.TemplateResponse(
        request,
        "register.html",
        {"csrf_token": csrf_token, "policy_version": accounts.PDN_POLICY_VERSION, "invite": invite},
    )
    csrf.set_cookie(response, csrf_token)
    return response


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
    csrf_token: str = Form(""),
    invite: str = Form(""),
):
    if not isinstance(invite, str):
        invite = ""
    if not auth_rate_limiter.is_allowed(f"register:{_client_ip(request)}"):
        return _render_auth_form_error(
            request, "register.html", {"error": "Слишком много попыток. Попробуйте позже.", "email": email}, 429
        )
    if not csrf.verify(request, csrf_token):
        return _render_auth_form_error(
            request, "register.html", {"error": "Форма устарела, попробуйте ещё раз.", "email": email}, 400
        )
    policy_error = _password_policy_error(password)
    if policy_error:
        error, status_code = policy_error
        return _render_auth_form_error(request, "register.html", {"error": error, "email": email}, status_code)
    with control_db.connect() as conn:
        try:
            invitation = accounts.get_pending_membership_invitation(conn, invite, email) if invite else None
            if invite and invitation is None:
                return _render_auth_form_error(
                    request, "register.html", {"error": "Приглашение недействительно, истекло или предназначено для другого email.", "email": email, "invite": invite}, 400
                )
            account_id = accounts.create_account(
                conn, email, password, pdn_consent=bool(pdn_consent), email_verified=False
            )
            if invitation is None:
                slug = accounts.generate_unique_slug(conn, email)
                accounts.create_tenant_instance(conn, account_id, slug)
                attribution, touched_at = growth_web.registration_source(request)
                growth.capture_registration_source(
                    conn, account_id, attribution,
                    consent=growth_web.consent_choice(request) == "analytics",
                    touch_at=touched_at,
                )
            verification_token = accounts.create_email_verification_token(conn, account_id)
        except ValueError as exc:
            return _render_auth_form_error(
                request, "register.html", {"error": str(exc), "email": email, "invite": invite}, 400
            )
    verify_params = {"token": verification_token}
    if invite:
        verify_params["invite"] = invite
    verify_url = f"{config.base_url()}/verify-email?{urlencode(verify_params)}"
    background_tasks.add_task(
        mailer.send_email_verification_email,
        email.strip().lower(),
        verify_url,
        accounts.EMAIL_VERIFICATION_TTL_HOURS,
    )
    return RedirectResponse("/verify-email-sent", status_code=303)


@app.get("/unsubscribe", response_class=HTMLResponse)
def unsubscribe_form(request: Request, token: str = ""):
    """Opt-out of the trial onboarding sequence (link in every such email).

    GET only confirms: it must never change anything. Corporate mail scanners
    and antivirus link-prefetchers open every URL in a message, so a GET that
    unsubscribed would quietly opt out people who never clicked. The HMAC
    token in the link is the authorization; no session is required.
    Transactional mail (password, email confirmation, payments) is unaffected.
    """
    valid = trial_emails.account_id_from_token(token) is not None
    return templates.TemplateResponse(
        request,
        "unsubscribe.html",
        {"token": token, "error": "" if valid else "Ссылка отписки недействительна или устарела."},
        status_code=200 if valid else 400,
    )


@app.post("/unsubscribe", response_class=HTMLResponse)
def unsubscribe(request: Request, token: str = Form("")):
    account_id = trial_emails.account_id_from_token(token)
    if account_id is None:
        return templates.TemplateResponse(
            request,
            "unsubscribe.html",
            {"token": "", "error": "Ссылка отписки недействительна или устарела."},
            status_code=400,
        )
    with control_db.connect() as conn:
        trial_emails.record_optout(conn, account_id)
    return templates.TemplateResponse(request, "unsubscribe.html", {"done": True})


@app.get("/verify-email-sent", response_class=HTMLResponse)
def verify_email_sent(request: Request):
    return templates.TemplateResponse(request, "verify_email_sent.html", {"analytics_goals": ["account_created"]})


@app.get("/verify-email")
def verify_email(request: Request, background_tasks: BackgroundTasks, token: str = "", invite: str = ""):
    try:
        with control_db.connect() as conn:
            account_id = accounts.consume_email_verification_token(conn, token)
            if invite:
                accounts.consume_membership_invitation(conn, invite, account_id)
            tenant = accounts.get_accessible_tenant_for_account(conn, account_id)
            if tenant is not None and int(tenant["account_id"]) == account_id:
                growth.record_event(conn, account_id, "email_verified")
            session_token = accounts.create_session(conn, account_id)
    except ValueError as exc:
        return templates.TemplateResponse(request, "verify_email_sent.html", {"error": str(exc)}, status_code=400)

    if tenant is None:
        return templates.TemplateResponse(
            request, "verify_email_sent.html",
            {"error": "Кабинет ещё не создан. Обратитесь в поддержку."}, status_code=500,
        )
    if tenant["status"] == "provisioning":
        import provisioning  # local import keeps app importable without Docker SDK

        background_tasks.add_task(
            provisioning.provision_tenant_background,
            account_id,
            int(tenant["id"]),
            tenant["slug"],
        )
    response = RedirectResponse("/provisioning", status_code=303)
    _set_session_cookie(response, session_token)
    return response


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "", reset: str = "", password_changed: str = "", email_changed: str = "", invite: str = ""):
    account = _current_account(request)
    if account is not None:
        return _redirect_for_account(account)
    csrf_token = csrf.get_or_create_token(request)
    response = templates.TemplateResponse(
        request, "login.html", {"next": next, "invite": invite, "password_was_reset": bool(reset), "password_changed": bool(password_changed), "email_changed": bool(email_changed), "csrf_token": csrf_token}
    )
    csrf.set_cookie(response, csrf_token)
    return response


@app.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    csrf_token: str = Form(""),
    invite: str = Form(""),
):
    if not isinstance(invite, str):
        invite = ""
    if not auth_rate_limiter.is_allowed(f"login:{_client_ip(request)}"):
        return _render_auth_form_error(
            request, "login.html",
            {"error": "Слишком много попыток входа. Пожалуйста, подождите минуту.", "email": email, "next": next, "invite": invite}, 429,
        )
    if not csrf.verify(request, csrf_token):
        return _render_auth_form_error(
            request, "login.html", {"error": "Форма устарела, попробуйте ещё раз.", "email": email, "next": next, "invite": invite}, 400
        )
    with control_db.connect() as conn:
        account = accounts.authenticate_account(conn, email, password)
        if account is None:
            return _render_auth_form_error(
                request, "login.html", {"error": "Неверный email или пароль.", "email": email, "next": next, "invite": invite}, 400
            )
        if not accounts.is_email_verified(account):
            return _render_auth_form_error(
                request,
                "login.html",
                {"error": "Подтвердите email по ссылке из письма, чтобы войти.", "email": email, "next": next, "invite": invite},
                403,
            )
        if invite:
            try:
                accounts.consume_membership_invitation(conn, invite, int(account["id"]))
            except ValueError as exc:
                return _render_auth_form_error(
                    request, "login.html", {"error": str(exc), "email": email, "next": next, "invite": invite}, 400
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
    if not auth_rate_limiter.is_allowed(f"forgot:{_client_ip(request)}"):
        return templates.TemplateResponse(
            request, "forgot_password.html",
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
    policy_error = _password_policy_error(password)
    if policy_error:
        error, status_code = policy_error
        return templates.TemplateResponse(
            request,
            "reset_password.html",
            {"token": token, "error": error},
            status_code=status_code,
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


def _account_page(request: Request, account, message: str = "", error: str = ""):
    csrf_token = csrf.get_or_create_token(request)
    with control_db.connect() as conn:
        tenant = accounts.get_accessible_tenant_for_account(conn, int(account["id"]))
        members = accounts.list_tenant_members(conn, int(tenant["id"])) if tenant is not None and tenant["membership_role"] == "owner" else []
    response = templates.TemplateResponse(request, "account.html", {"account": account, "tenant": tenant, "members": members, "csrf_token": csrf_token, "message": message, "error": error})
    csrf.set_cookie(response, csrf_token)
    return response


@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request):
    account = _current_account(request)
    if account is None:
        return RedirectResponse("/login", status_code=303)
    return _account_page(request, account)


@app.post("/account/team/invite")
def account_team_invite(
    request: Request,
    background_tasks: BackgroundTasks,
    email: str = Form(...),
    role: str = Form("viewer"),
    csrf_token: str = Form(""),
):
    account = _current_account(request)
    if account is None:
        return RedirectResponse("/login", status_code=303)
    if not csrf.verify(request, csrf_token):
        return _account_page(request, account, error="Форма устарела, попробуйте ещё раз.")
    try:
        with control_db.connect() as conn:
            tenant = accounts.get_tenant_for_account(conn, int(account["id"]))
            if tenant is None or accounts.get_tenant_membership(conn, int(tenant["id"]), int(account["id"]))["role"] != "owner":
                raise ValueError("Только владелец кабинета может приглашать пользователей.")
            token = accounts.create_membership_invitation(conn, int(tenant["id"]), email, role)
    except (ValueError, TypeError) as exc:
        return _account_page(request, account, error=str(exc))
    accept_url = f"{config.base_url()}/team/accept?{urlencode({'token': token})}"
    background_tasks.add_task(
        mailer.send_tenant_invitation_email,
        email.strip().lower(), tenant["slug"], role, accept_url, accounts.TENANT_INVITATION_TTL_DAYS,
    )
    return _account_page(request, account, message="Приглашение отправлено.")


@app.get("/team/accept")
def team_accept(request: Request, token: str = ""):
    account = _current_account(request)
    if account is None:
        return RedirectResponse("/login?" + urlencode({"invite": token}), status_code=303)
    try:
        with control_db.connect() as conn:
            tenant_id = accounts.consume_membership_invitation(conn, token, int(account["id"]))
            tenant = accounts.get_tenant_by_id(conn, tenant_id)
    except ValueError as exc:
        return _account_page(request, account, error=str(exc))
    if tenant is None:
        return _account_page(request, account, error="Кабинет из приглашения не найден.")
    return RedirectResponse(config.tenant_url(tenant["slug"]), status_code=303)


@app.post("/account/password")
def account_password_submit(request: Request, current_password: str = Form(...), new_password: str = Form(...), new_password_confirm: str = Form(...), csrf_token: str = Form("")):
    account = _current_account(request)
    if account is None:
        return RedirectResponse("/login", status_code=303)
    if not csrf.verify(request, csrf_token):
        return _account_page(request, account, error="Форма устарела, попробуйте ещё раз.")
    if new_password != new_password_confirm:
        return _account_page(request, account, error="Новые пароли не совпадают.")
    policy_error = _password_policy_error(new_password)
    if policy_error:
        return _account_page(request, account, error=policy_error[0])
    try:
        with control_db.connect() as conn:
            accounts.change_password(conn, int(account["id"]), current_password, new_password)
    except ValueError as exc:
        return _account_page(request, account, error=str(exc))
    response = RedirectResponse("/login?password_changed=1", status_code=303)
    _clear_session_cookie(response)
    return response


@app.post("/account/email")
def account_email_submit(request: Request, new_email: str = Form(...), current_password: str = Form(...), background_tasks: BackgroundTasks = None, csrf_token: str = Form("")):
    account = _current_account(request)
    if account is None:
        return RedirectResponse("/login", status_code=303)
    if not csrf.verify(request, csrf_token):
        return _account_page(request, account, error="Форма устарела, попробуйте ещё раз.")
    if not accounts.verify_password(current_password, account["password_hash"], account["password_salt"]):
        return _account_page(request, account, error="Текущий пароль указан неверно.")
    try:
        with control_db.connect() as conn:
            token = accounts.create_email_change_token(conn, int(account["id"]), new_email)
    except ValueError as exc:
        return _account_page(request, account, error=str(exc))
    confirm_url = f"{config.base_url()}/account/confirm-email?{urlencode({'token': token})}"
    background_tasks.add_task(mailer.send_email_change_email, new_email.strip().lower(), confirm_url, 60)
    return _account_page(request, account, message="Письмо с подтверждением отправлено на новый email.")


@app.get("/account/confirm-email")
def account_confirm_email(request: Request, token: str = ""):
    try:
        with control_db.connect() as conn:
            accounts.consume_email_change_token(conn, token)
    except ValueError as exc:
        return templates.TemplateResponse(request, "account_confirm.html", {"error": str(exc)}, status_code=400)
    response = RedirectResponse("/login?email_changed=1", status_code=303)
    _clear_session_cookie(response)
    return response


@app.get("/account/export")
def account_export(request: Request):
    account = _current_account(request)
    if account is None:
        return RedirectResponse("/login", status_code=303)
    with control_db.connect() as conn:
        tenant = accounts.get_tenant_for_account(conn, int(account["id"]))
    if tenant is None:
        return Response("Кабинет ещё не создан.", status_code=409, media_type="text/plain; charset=utf-8")
    import provisioning  # local import keeps app importable without Docker SDK

    upstream = provisioning.export_tenant(tenant["slug"])
    if upstream.status_code >= 400:
        upstream.close()
        return Response("Не удалось подготовить выгрузку данных.", status_code=502, media_type="text/plain; charset=utf-8")

    def stream():
        try:
            yield from upstream.iter_content(chunk_size=64 * 1024)
        finally:
            upstream.close()

    return StreamingResponse(
        stream(),
        media_type=upstream.headers.get("content-type", "application/gzip"),
        headers={
            "Content-Disposition": upstream.headers.get(
                "content-disposition", f'attachment; filename="marketshelper-{tenant["slug"]}.tar.gz"'
            )
        },
    )


@app.post("/account/delete")
def account_delete(
    request: Request,
    current_password: str = Form(...),
    confirmation: str = Form(...),
    csrf_token: str = Form(""),
):
    account = _current_account(request)
    if account is None:
        return RedirectResponse("/login", status_code=303)
    csrf_token_ok = csrf.verify(request, csrf_token)
    if not csrf_token_ok or confirmation.strip().upper() != "УДАЛИТЬ":
        csrf_value = csrf.get_or_create_token(request)
        return templates.TemplateResponse(
            request,
            "account.html",
            {"account": account, "csrf_token": csrf_value, "error": "Для удаления подтвердите действие и введите УДАЛИТЬ."},
            status_code=400,
        )
    if not accounts.verify_password(current_password, account["password_hash"], account["password_salt"]):
        csrf_value = csrf.get_or_create_token(request)
        return templates.TemplateResponse(
            request,
            "account.html",
            {"account": account, "csrf_token": csrf_value, "error": "Текущий пароль указан неверно."},
            status_code=400,
        )
    with control_db.connect() as conn:
        tenant = accounts.get_tenant_for_account(conn, int(account["id"]))
    if tenant is not None:
        import provisioning  # local import keeps app importable without Docker SDK

        try:
            provisioning.remove_tenant(tenant["slug"], purge_volume=True)
        except Exception:
            csrf_value = csrf.get_or_create_token(request)
            return templates.TemplateResponse(
                request,
                "account.html",
                {"account": account, "csrf_token": csrf_value, "error": "Не удалось удалить кабинет. Данные сохранены, повторите позже."},
                status_code=502,
            )
    with control_db.connect() as conn:
        accounts.delete_account(conn, int(account["id"]))
    response = RedirectResponse("/", status_code=303)
    _clear_session_cookie(response)
    return response


@app.get("/provisioning", response_class=HTMLResponse)
def provisioning_status(request: Request):
    account = _current_account(request)
    if account is None:
        return RedirectResponse("/login", status_code=303)
    with control_db.connect() as conn:
        tenant = accounts.get_accessible_tenant_for_account(conn, account["id"])
    if tenant is not None and tenant["status"] == "running" and accounts.tenant_has_access(conn, tenant):
        return RedirectResponse(f"{config.tenant_url(tenant['slug'])}?mh_goal=trial_started", status_code=303)
    status = tenant["status"] if tenant is not None else "provisioning"
    goals = ["trial_started"] if status == "trialing" else []
    return templates.TemplateResponse(request, "provisioning.html", {"status": status, "analytics_goals": goals})


def _redirect_for_account(account) -> RedirectResponse:
    with control_db.connect() as conn:
        tenant = accounts.get_accessible_tenant_for_account(conn, account["id"])
        can_access = accounts.tenant_has_access(conn, tenant)
    if tenant is not None and tenant["status"] == "running" and can_access:
        return RedirectResponse(f"{config.tenant_url(tenant['slug'])}?mh_goal=trial_started", status_code=303)
    return RedirectResponse("/provisioning", status_code=303)


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

    if account is None or not slug:
        return _redirect_to_login(login_url, forwarded_proto, forwarded_host, forwarded_uri)

    with control_db.connect() as conn:
        tenant = accounts.get_tenant_by_slug(conn, slug)
        membership = accounts.get_tenant_membership(conn, int(tenant["id"]), int(account["id"])) if tenant is not None else None
        owner_access = accounts.tenant_has_access(conn, tenant)
    if tenant is None or membership is None or tenant["status"] != "running" or not owner_access:
        # A session is scoped to an explicit tenant membership; the tenant
        # owner's subscription controls access for every invited member.
        return _redirect_to_login(login_url, forwarded_proto, forwarded_host, forwarded_uri)

    return Response(
        status_code=200,
        headers={
            "X-Tenant-Role": str(membership["role"]),
            "X-Tenant-Account": str(account["id"]),
        },
    )


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


@app.get("/_stcore/health")
def stcore_health():
    return {"status": "ok"}
