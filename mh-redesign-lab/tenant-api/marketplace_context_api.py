"""Authenticated marketplace boundary: independent Ozon seller grants."""
import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

import shop_context
from config import IS_DEMO
from db.core import connect
from production_permissions import ROLES, write_allowed

router = APIRouter(prefix="/api/v1/marketplace-context", tags=["marketplace-context"])
SHARED_PREFIXES = ("/api/v1/manufacturing-costs", "/api/v1/cashflow", "/api/v1/cash-accounts", "/api/v1/production-workspace/workcenters")
NEUTRAL_PATHS = {"/api/v1/session", "/api/v1/health", "/healthz", "/_stcore/health"}
OZON_PREFIXES = ("/api/v1/ozon-accounting", "/api/v1/ozon-cabinet", "/api/v1/ozon-insights", "/api/v1/ozon-prices")
OZON_SETTINGS = "/api/v1/settings/ozon"
CONTEXT_SESSION = "/api/v1/marketplace-context/session"
ACCESS_PATH = "/api/v1/marketplace-context/ozon/access"


def _prefix(path, prefix):
    return path == prefix or path.startswith(prefix + "/")


def _ozon_path(path):
    return any(_prefix(path, prefix) for prefix in OZON_PREFIXES) or path == OZON_SETTINGS


def _shared_route(method, path, query=""):
    if not any(_prefix(path, prefix) for prefix in SHARED_PREFIXES):
        return None
    route = shop_context.route_scope(method, path, query)
    return route if route and route["scope"] == "tenant_shared" else None


def _actor(headers):
    # Public demo has no forward-auth identity. Its context is always read-only,
    # regardless of caller-supplied owner/manager headers.
    if IS_DEMO:
        return "demo", "viewer"
    account, role = shop_context.actor(headers, True)
    if role not in ROLES:
        raise shop_context.ShopContextError("MARKETPLACE_ROLE_INVALID", "Роль пользователя не подтверждена")
    return account, role


def _schema(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS ozon_cabinet_access(account TEXT PRIMARY KEY,can_write INTEGER NOT NULL CHECK(can_write IN (0,1)),active INTEGER NOT NULL CHECK(active IN (0,1)),actor TEXT NOT NULL,updated_at TEXT NOT NULL)")


def _authorize(headers):
    if shop_context.selector(headers) != 0 or headers.get("x-mh-shop-revision") is not None:
        raise shop_context.ShopContextError("OZON_PRIMARY_CONTEXT_REQUIRED", "Кабинет Ozon не связан с дополнительным WB-магазином", 409)
    account, role = _actor(headers)
    if IS_DEMO:
        return account, role, False
    if role == "integration_reader":
        raise shop_context.ShopContextError("OZON_ACCESS_DENIED", "Интеграционной роли недоступен кабинет Ozon")
    shop_context.authorize(0, account, role)
    if role == "owner":
        return account, role, True
    with connect() as conn:
        _schema(conn)
        grant = conn.execute("SELECT can_write FROM ozon_cabinet_access WHERE account=? AND active=1", (account,)).fetchone()
    if grant is None:
        raise shop_context.ShopContextError("OZON_ACCESS_REQUIRED", "Владелец должен отдельно предоставить доступ к кабинету Ozon")
    return account, role, bool(grant["can_write"])


def _session(headers, marketplace):
    if marketplace == "ozon":
        _, role, grant_write = _authorize(headers)
    else:
        account, role = _actor(headers)
        shop_context.authorize(shop_context.selector(headers), account, role)
        grant_write = role in {"owner", "manager"}
    shared = [{"method": r["method"], "path": r["path"]} for r in shop_context.ROUTES
              if _shared_route(r["method"], r["path"]) and write_allowed(role, r["method"], r["path"])
              and (r["method"] in {"GET", "HEAD", "OPTIONS"} or grant_write and not IS_DEMO)]
    return {"marketplace": marketplace, "role": role, "can_write": grant_write and role in {"owner", "manager"} and not IS_DEMO,
            "is_demo": bool(IS_DEMO), "authorized": True, "shared_routes": shared}


@router.get("/session")
def session(request: Request, marketplace: Literal["wb", "ozon"] = "wb"):
    try:
        given = request.headers.get("x-mh-marketplace")
        if given is not None and given != marketplace:
            raise shop_context.ShopContextError("MARKETPLACE_SELECTOR_MISMATCH", "Маркетплейс запроса и выбранный кабинет не совпадают", 400)
        return _session(request.headers, marketplace)
    except shop_context.ShopContextError as exc:
        raise HTTPException(exc.status, {"code": exc.code, "message": exc.message}) from exc


class Grant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account: str = Field(min_length=1, max_length=200)
    can_write: StrictBool
    active: StrictBool = True


def _owner(headers):
    account, role = _actor(headers)
    if role != "owner":
        raise HTTPException(403, "Доступ к кабинету Ozon выдаёт только владелец")
    return account


@router.get("/ozon/access")
def access_list(request: Request):
    _owner(request.headers)
    with connect() as conn:
        _schema(conn)
        rows = [dict(r) for r in conn.execute("SELECT account,can_write,active FROM ozon_cabinet_access ORDER BY account LIMIT 1000")]
    return {"items": [{**r, "can_write": bool(r["can_write"]), "active": bool(r["active"])} for r in rows], "can_manage": True}


@router.put("/ozon/access")
def grant_access(payload: Grant, request: Request):
    actor = _owner(request.headers)
    if IS_DEMO:
        raise HTTPException(403, "Демо доступно только для просмотра")
    account = payload.account.strip()
    if not account or any(ord(c) < 32 for c in account):
        raise HTTPException(422, "Укажите идентификатор пользователя кабинета")
    with connect() as conn:
        _schema(conn)
        conn.execute("INSERT INTO ozon_cabinet_access VALUES(?,?,?,?,?) ON CONFLICT(account) DO UPDATE SET can_write=excluded.can_write,active=excluded.active,actor=excluded.actor,updated_at=excluded.updated_at", (account, int(payload.can_write), int(payload.active), actor, datetime.now(timezone.utc).isoformat()))
    return {"saved": True}


async def _body_context(request, selected, primary_only=False):
    if request.method in {"GET", "HEAD", "OPTIONS"} or request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
        return
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 2 * 1024 * 1024:
            raise shop_context.ShopContextError("MARKETPLACE_BODY_TOO_LARGE", "Сократите размер запроса", 413)
        body.extend(chunk)
    request._body = bytes(body)
    try:
        decoded = json.loads(body) if body else {}
    except (ValueError, UnicodeError):
        raise shop_context.ShopContextError("MARKETPLACE_BODY_INVALID", "Не распознан JSON запроса", 400) from None
    if isinstance(decoded, dict) and ("marketplace" in decoded and decoded["marketplace"] != selected or primary_only and "shop_id" in decoded and (type(decoded["shop_id"]) is not int or decoded["shop_id"] != 0)):
        raise shop_context.ShopContextError("MARKETPLACE_SELECTOR_MISMATCH", "Контекст данных не совпадает с выбранным кабинетом", 409)


async def marketplace_guard(request):
    """Return a failure before any wrong-marketplace handler runs."""
    try:
        path, method = request.url.path, request.method
        selected = request.headers.get("x-mh-marketplace")
        if selected is not None and selected not in {"wb", "ozon"}:
            raise shop_context.ShopContextError("MARKETPLACE_SELECTOR_INVALID", "Маркетплейс выбран некорректно", 400)
        if path == CONTEXT_SESSION:
            values = request.query_params.getlist("marketplace")
            if len(values) > 1 or values and values[0] not in {"wb", "ozon"} or selected is not None and selected != (values[0] if values else "wb"):
                raise shop_context.ShopContextError("MARKETPLACE_SELECTOR_MISMATCH", "Маркетплейс запроса не совпадает с кабинетом", 400)
            return None  # Session endpoint performs its own authenticated ACL.
        if selected == "wb" and _ozon_path(path):
            raise shop_context.ShopContextError("MARKETPLACE_ROUTE_MISMATCH", "Данные Ozon недоступны в кабинете WB", 409)
        if selected != "ozon" and not _ozon_path(path) and path != ACCESS_PATH:
            if selected == "wb":
                values = request.query_params.getlist("marketplace")
                if len(values) > 1 or values and values[0] != "wb":
                    raise shop_context.ShopContextError("MARKETPLACE_SELECTOR_MISMATCH", "Маркетплейс URL не совпадает с выбранным", 400)
                await _body_context(request, "wb")
            return None  # Backward-compatible legacy WB requests.
        _, role, grant_write = await run_in_threadpool(_authorize, request.headers)
        shared = _shared_route(method, path, request.url.query)
        if not (_ozon_path(path) or path in NEUTRAL_PATHS or path == ACCESS_PATH or shared):
            raise shop_context.ShopContextError("MARKETPLACE_ROUTE_UNSUPPORTED", "Этот раздел не относится к кабинету Ozon", 409)
        if path in NEUTRAL_PATHS and method not in {"GET", "HEAD", "OPTIONS"}:
            raise shop_context.ShopContextError("MARKETPLACE_ROUTE_UNSUPPORTED", "Служебный маршрут доступен только для чтения", 409)
        if method not in {"GET", "HEAD", "OPTIONS"} and (IS_DEMO or not grant_write or (not shared and role not in {"owner", "manager"}) or not write_allowed(role, method, path)):
            raise shop_context.ShopContextError("OZON_WRITE_DENIED", "Изменение недоступно для этой роли или разрешения Ozon")
        query_marketplace = request.query_params.getlist("marketplace")
        if len(query_marketplace) > 1 or query_marketplace and query_marketplace[0] != "ozon":
            raise shop_context.ShopContextError("MARKETPLACE_SELECTOR_MISMATCH", "Маркетплейс URL не совпадает с выбранным", 400)
        query_shop = request.query_params.getlist("shop_id")
        if len(query_shop) > 1 or query_shop and query_shop[0] != "0":
            raise shop_context.ShopContextError("OZON_PRIMARY_CONTEXT_REQUIRED", "Ozon требует основной контекст кабинета", 409)
        # Bounded cached bytes preserve the downstream request body.
        await _body_context(request, "ozon", primary_only=True)
        return None
    except shop_context.ShopContextError as exc:
        return JSONResponse(status_code=exc.status, content={"detail": {"code": exc.code, "message": exc.message}})
