from datetime import date
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict
from db.cashflow import create_entry, change_status, cash_report

router = APIRouter(prefix="/api/v1/cashflow")

class CashInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    kind: Literal["movement", "opening"] = "movement"
    direction: Literal["inflow", "outflow"]
    status: Literal["planned", "actual"]
    planned_date: date
    actual_date: date | None = None
    amount: str = Field(max_length=40, pattern=r"^\d+(\.\d{1,4})?$")
    exchange_rate: str = Field(default="1", max_length=40, pattern=r"^\d+(\.\d{1,8})?$")
    currency: Literal["RUB", "CNY", "USD", "EUR"] = "RUB"
    category: Literal["marketplace_payout", "procurement", "salary", "tax", "rent", "advertising", "other", "opening"] = "other"
    reference: str = Field(default="", max_length=300)

class StatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["actual", "cancelled"]
    actual_date: date | None = None

def call(operation):
    try: return operation()
    except LookupError as error: raise HTTPException(404, str(error)) from error
    except (ValueError, ArithmeticError) as error: raise HTTPException(422, str(error)) from error

@router.get("")
def report(start: date, end: date):
    return call(lambda: cash_report(start,end))

@router.post("/entries")
def create(payload: CashInput, request: Request):
    return call(lambda: create_entry(payload.model_dump(mode="json"), request.headers.get("x-tenant-account")))

@router.patch("/entries/{entry_id}")
def update(entry_id: int, payload: StatusInput, request: Request):
    return call(lambda: change_status(entry_id,payload.status,payload.actual_date,request.headers.get("x-tenant-account")))
