"""Regression tests for fresh CSRF cookies on re-rendered auth forms."""
from __future__ import annotations

from fastapi import Request

import app
from logic import csrf


def _request_without_cookies() -> Request:
    return Request({
        "type": "http",
        "headers": [],
        "method": "POST",
        "path": "/login",
        "client": ("198.51.100.10", 12345),
    })


def _has_csrf_cookie(response) -> bool:
    return any(cookie.startswith(f"{csrf.CSRF_COOKIE_NAME}=") for cookie in response.headers.getlist("set-cookie"))


def test_login_csrf_failure_refreshes_cookie_for_the_re_rendered_form():
    app.auth_rate_limiter.clear()
    response = app.login_submit(_request_without_cookies(), "seller@example.test", "password1234", "", "")
    assert response.status_code == 400
    assert _has_csrf_cookie(response)


def test_registration_csrf_failure_refreshes_cookie_for_the_re_rendered_form():
    app.auth_rate_limiter.clear()
    response = app.register_submit(_request_without_cookies(), "seller@example.test", "password1234", None, "")
    assert response.status_code == 400
    assert _has_csrf_cookie(response)
