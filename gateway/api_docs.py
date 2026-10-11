"""Keep the framework's existing documentation routes under nonce CSP."""
import re

from fastapi import Request
from fastapi.openapi.docs import (
    get_redoc_html,
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)


def _trusted_framework_html(response, request):
    # These factories receive only fixed application metadata/server root_path.
    # Never apply this annotation to arbitrary HTML or user-provided content:
    # that would authorize injected scripts.
    nonce = request.state.csp_nonce
    assert re.fullmatch(r"[A-Za-z0-9_-]{32}", nonce)
    response.body = re.sub(
        rb"<script(?=[\s>])",
        b'<script nonce="' + nonce.encode("ascii") + b'"',
        response.body,
    )
    response.headers["content-length"] = str(len(response.body))
    return response


def install_documentation_routes(app):
    @app.get("/docs", include_in_schema=False)
    async def swagger(request: Request):
        root = request.scope.get("root_path", "").rstrip("/")
        response = get_swagger_ui_html(
            openapi_url=root + app.openapi_url,
            title=app.title + " - Swagger UI",
            oauth2_redirect_url=root + "/docs/oauth2-redirect",
        )
        return _trusted_framework_html(response, request)

    @app.get("/redoc", include_in_schema=False)
    async def redoc(request: Request):
        root = request.scope.get("root_path", "").rstrip("/")
        return _trusted_framework_html(get_redoc_html(
            openapi_url=root + app.openapi_url,
            title=app.title + " - ReDoc",
        ), request)

    @app.get("/docs/oauth2-redirect", include_in_schema=False)
    async def oauth2_redirect(request: Request):
        return _trusted_framework_html(get_swagger_ui_oauth2_redirect_html(), request)
