"""Explicit, tenant-local accounting reads from configured Ozon credentials."""
from datetime import date
from threading import Lock
from typing import Annotated, Literal
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from config import get_ozon_credentials
from db.ozon_accounting import import_snapshot, workspace, save_cost_mapping
from ozon_accounting_client import OzonAccountingClient, checked_period

router = APIRouter(prefix="/api/v1/ozon-accounting", tags=["Учёт Ozon"])
sync_lock = Lock()


class Period(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: date
    end_date: date


class Cost(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(pattern="^[1-9][0-9]{0,19}$")
    mode: Literal["manual", "wb_estimate"]
    unit_cost: str | None = Field(default=None,max_length=40)
    nm_id: Annotated[StrictInt, Field(gt=0)] | None = None
    chrt_id: Annotated[StrictInt, Field(ge=0)] = 0


def allow_write(request):
    if request.headers.get("x-tenant-role", "").lower() not in {"owner", "manager"}:
        raise HTTPException(403, "Изменения доступны владельцу и менеджеру.")


@router.get("/workspace")
def read_workspace(start_date: date, end_date: date):
    try:
        checked_period(start_date.isoformat(), end_date.isoformat())
        result = workspace(start_date.isoformat(), end_date.isoformat())
        result["configured"] = bool(get_ozon_credentials())
        return result
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc


@router.post("/sync")
def sync(payload: Period, request: Request):
    allow_write(request)
    try:
        checked_period(payload.start_date.isoformat(), payload.end_date.isoformat())
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    credentials = get_ozon_credentials()
    if not credentials:
        raise HTTPException(409,"Подключите Ozon в настройках кабинета.")
    if not sync_lock.acquire(blocking=False):
        raise HTTPException(409,"Загрузка Ozon уже выполняется.")
    try:
        client_id, api_key = (credentials["client_id"],credentials["api_key"]) if isinstance(credentials,dict) else credentials
        client = OzonAccountingClient(client_id,api_key)
        snapshot = client.snapshot(payload.start_date.isoformat(),payload.end_date.isoformat())
        imported = import_snapshot(snapshot)
        return {**imported,"workspace": workspace(payload.start_date.isoformat(),payload.end_date.isoformat())}
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    finally:
        sync_lock.release()


@router.put("/cost")
def save_cost(payload: Cost, request: Request):
    allow_write(request)
    try:
        save_cost_mapping(payload.sku,payload.mode,payload.unit_cost,payload.nm_id,payload.chrt_id)
        return {"saved": True}
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
