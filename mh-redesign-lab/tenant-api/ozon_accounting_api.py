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


class DocumentImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace: str = Field(min_length=1,max_length=80)
    csv_text: str = Field(min_length=1,max_length=1048576)
    expected_fingerprint: str | None = Field(default=None,max_length=64)


class DocumentLot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace: str = Field(min_length=1,max_length=80)
    external_lot_id: str = Field(min_length=1,max_length=120)
    sku: str = Field(pattern="^[1-9][0-9]{0,19}$")
    received_at: str
    quantity_units: Annotated[StrictInt, Field(gt=0,le=1000000)]
    unit_cost_rub: str
    document_ref: str = Field(min_length=1,max_length=200)
    reason: str = Field(min_length=1,max_length=500)
    request_key: str


def document_actor(request):
    return request.headers.get("x-tenant-account") or "tenant-owner-or-manager"


@router.get("/pnl")
def document_pnl(start_date: date,end_date: date,namespace: str="main"):
    from db.ozon_pnl import pnl
    try:
        checked_period(start_date.isoformat(),end_date.isoformat())
        return pnl(start_date.isoformat(),end_date.isoformat(),namespace)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc


@router.get("/movements/template")
def document_template():
    from db.ozon_pnl import COLUMNS
    from fastapi.responses import Response
    return Response(",".join(COLUMNS)+"\n",media_type="text/csv",headers={"Content-Disposition":"attachment; filename=ozon-movements.csv","X-Content-Type-Options":"nosniff"})


@router.post("/movements/preview")
def document_preview(payload: DocumentImport,request: Request):
    allow_write(request)
    from db.ozon_pnl import preview_import
    try:return preview_import(payload.csv_text,payload.namespace)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc


@router.post("/movements/confirm")
def document_confirm(payload: DocumentImport,request: Request):
    allow_write(request)
    from db.ozon_pnl import confirm_import
    try:return confirm_import(payload.csv_text,payload.namespace,payload.expected_fingerprint,document_actor(request))
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc


@router.post("/cost-lots")
def document_cost_lot(payload: DocumentLot,request: Request):
    allow_write(request)
    from db.ozon_pnl import add_cost_lot
    try:return add_cost_lot(**payload.model_dump(),actor=document_actor(request))
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
