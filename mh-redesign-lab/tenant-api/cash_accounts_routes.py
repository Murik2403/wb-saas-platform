from datetime import date
from typing import Literal
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, ConfigDict
from cashflow_routes import CashInput, call
from db import cash_accounts, cash_statement

router=APIRouter(prefix='/api/v1/cash-accounts')
class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')
class AccountInput(Strict):
    request_key: str=Field(min_length=8,max_length=100,pattern=r'^[a-zA-Z0-9_-]+$')
    name: str=Field(min_length=1,max_length=100)
    currency: Literal['RUB','CNY','USD','EUR']
class ArchiveInput(Strict):
    revision: int=Field(ge=1)
    active: bool
class TransferInput(Strict):
    request_key: str=Field(min_length=8,max_length=80,pattern=r'^[a-zA-Z0-9_-]+$')
    from_account_id: int=Field(ge=2)
    to_account_id: int=Field(ge=2)
    from_amount: str=Field(max_length=40,pattern=r'^\d+(\.\d{1,2})?$')
    to_amount: str=Field(max_length=40,pattern=r'^\d+(\.\d{1,2})?$')
    from_rate: str=Field(max_length=40,pattern=r'^\d+(\.\d{1,8})?$')
    to_rate: str=Field(max_length=40,pattern=r'^\d+(\.\d{1,8})?$')
    status: Literal['planned','actual']
    planned_date: date
    actual_date: date|None=None
    reference: str=Field(default='',max_length=300)
class StatementInput(Strict):
    account_id: int=Field(ge=2)
    csv: str=Field(min_length=1,max_length=1048576)
class ConfirmInput(StatementInput):
    fingerprint: str=Field(pattern=r'^[a-f0-9]{64}$')
    request_key: str=Field(min_length=8,max_length=100,pattern=r'^[a-zA-Z0-9_-]+$')
    confirmed: Literal[True]

@router.get('')
def report(start:date,end:date): return call(lambda:cash_accounts.report(start,end))
@router.post('')
def create(payload:AccountInput,request:Request): return call(lambda:cash_accounts.create_account(payload.name,payload.currency,request.headers.get('x-tenant-account'),payload.request_key))
@router.patch('/{account_id}')
def archive(account_id:int,payload:ArchiveInput): return call(lambda:cash_accounts.archive_account(account_id,payload.revision,payload.active))
@router.post('/{account_id}/entries')
def entry(account_id:int,payload:CashInput,request:Request): return call(lambda:cash_accounts.create_entry(account_id,payload.model_dump(mode='json'),request.headers.get('x-tenant-account')))
@router.post('/transfers')
def transfer(payload:TransferInput,request:Request): return call(lambda:cash_accounts.transfer(payload.model_dump(mode='json'),request.headers.get('x-tenant-account')))
@router.post('/statements/preview')
def preview(payload:StatementInput): return call(lambda:cash_statement.preview(payload.account_id,payload.csv))
@router.post('/statements/confirm')
def confirm(payload:ConfirmInput,request:Request): return call(lambda:cash_statement.confirm(payload.account_id,payload.csv,payload.fingerprint,payload.request_key,payload.confirmed,request.headers.get('x-tenant-account')))
