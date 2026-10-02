"""FBS-заказы WB: чтение из локальной SQLite, обновление только по явной кнопке.

GET-маршруты не обращаются к WB. POST /refresh (owner/manager по глобальному role middleware)
делает ограниченный по страницам и частоте запуск tenant-app/fbs_orders.py и не затрагивает основную синхронизацию.
"""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

import fbs_orders as fbs
from config import get_token
from db import core
from today_service import json_safe
from wb_api import WBAPI

router = APIRouter(prefix="/api/v1/fbs", tags=["fbs"])


def _has_scope() -> bool | None:
    """True/False when the token advertises its categories (local JWT decode, no WB call); None when unknown."""
    token = get_token()
    if not token:
        return None
    info = WBAPI(token).token_scope_info()
    return fbs.MARKETPLACE_SCOPE in info["scopes"] if info.get("known") else None


def _days(period: str | None, days: int | None) -> int:
    if period is not None:
        if period not in fbs.PERIOD_DAYS:
            raise HTTPException(422, detail="Период: today, 7d, 14d или 30d")
        return fbs.PERIOD_DAYS[period]
    return days or fbs.DEFAULT_DAYS


@router.get("/orders")
def get_orders(status: str | None = Query(None), overdue: bool = False, warehouse: int | None = None,
               days: int = Query(fbs.DEFAULT_DAYS, ge=1, le=fbs.MAX_DAYS), limit: int = Query(50, ge=1, le=200),
               offset: int = Query(0, ge=0, le=100000)) -> dict:
    if status is not None and status not in fbs.STATE_LABELS:
        raise HTTPException(422, detail="Неизвестный статус")
    with core.connect() as conn:
        fbs.init(conn)
        return json_safe(fbs.list_orders(conn, status=status, overdue=overdue, warehouse=warehouse, days=days, limit=limit, offset=offset))


@router.get("/analytics")
def get_analytics(period: str | None = Query(None)) -> dict:
    with core.connect() as conn:
        fbs.init(conn)
        return json_safe(fbs.analytics(conn, days=_days(period, None)))


@router.get("/state")
def get_state() -> dict:
    with core.connect() as conn:
        return json_safe(fbs.read_state(conn, has_scope=_has_scope()))


@router.post("/refresh")
def post_refresh(days: int = Query(fbs.DEFAULT_DAYS, ge=1, le=fbs.MAX_DAYS)):
    token = get_token()
    if not token:
        raise HTTPException(409, detail="WB token не подключён")
    scope = _has_scope()
    with core.connect() as conn:
        try:
            return json_safe(fbs.refresh(conn, WBAPI(token, timeout=15), days=days, has_scope=scope))
        except fbs.TooSoon as exc:
            body = {"detail": f"Обновление возможно раз в {fbs.MIN_INTERVAL_SECONDS // 60} мин. Повторите через {exc.retry_after} с.",
                    "state": fbs.read_state(conn, has_scope=scope)}
            return JSONResponse(body, status_code=429, headers={"Retry-After": str(exc.retry_after)})
