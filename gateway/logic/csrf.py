"""Double-submit-cookie CSRF defense-in-depth.

The session cookie already carries SameSite=Lax (see app.py's
_set_session_cookie()), which already stops the classic cross-site form
POST attack in every evergreen browser: a forged cross-site POST simply
arrives with no session cookie attached, so the handler sees an
unauthenticated request and redirects to /login instead of acting.

This is the OWASP-recommended belt-and-suspenders layer on top of that
rather than a reaction to a live exploit: a random per-visitor token set
as an ordinary cookie and echoed back by the server into a hidden form
field on every GET that renders a protected form. The server -- not
client-side JS -- reads the cookie value in ensure_token() and injects it
into the template, so httponly doesn't interfere with the pattern. A
forged cross-site POST has no way to read that cookie value to also put
it in the hidden field, so the two won't match even in a browser that,
for whatever reason, didn't enforce SameSite on the session cookie.

Deliberately not applied to /logout: it's submitted from the tenant
container's own subdomain (see tenant-app/app.py's sidebar), a different
origin that has no access to this cookie at all -- and the worst case of
a forged logout is mild annoyance, not data or money at risk, unlike
/billing/cancel or /billing/checkout.
"""
from __future__ import annotations

import hmac
import secrets

from fastapi import Request, Response

import config

CSRF_COOKIE_NAME = "wb_saas_csrf"
_TOKEN_BYTES = 32


def get_or_create_token(request: Request) -> str:
    """The token value to render into a hidden input -- needed before the
    template (and therefore the Response object the cookie gets set on)
    exists, hence the split from set_cookie() below. Reuses the existing
    cookie's value across a visitor's whole session instead of rotating it
    per-page, same as most double-submit-cookie implementations (the token
    isn't single-use; it just has to be something a cross-origin attacker
    can't read)."""
    return request.cookies.get(CSRF_COOKIE_NAME) or secrets.token_urlsafe(_TOKEN_BYTES)


def set_cookie(response: Response, token: str) -> None:
    """Call once the Response exists (after get_or_create_token() supplied
    the value the template needed). A no-op cost-wise if the cookie was
    already present -- re-setting it just refreshes it, which is fine."""
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=config.COOKIE_SECURE,
        path="/",
    )


def verify(request: Request, submitted_token: str) -> bool:
    """Constant-time comparison -- the token isn't secret in the sense a
    password is, but there's no reason to leak timing information either."""
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    if not cookie_token or not submitted_token:
        return False
    return hmac.compare_digest(cookie_token, submitted_token)
