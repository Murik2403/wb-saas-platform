from __future__ import annotations
from datetime import date,timedelta
from fastapi import APIRouter,HTTPException,Request,Query
from pydantic import BaseModel,Field,FiniteFloat
from db import shop_workspaces as shops
from shop_workspace_service import execute
from wb_api import WBAPI
from config import get_token
from time import monotonic

router=APIRouter(prefix='/api/v1/shop-workspaces',tags=['shop-workspaces'])
class ShopInput(BaseModel):
    name:str=Field(min_length=1,max_length=120)
    token:str=Field(min_length=1,max_length=10000)
class SyncInput(BaseModel):
    days:int=Field(default=30,ge=1,le=90)
class CostInput(BaseModel):
    nm_id:int=Field(gt=0)
    cost_per_wb_unit:FiniteFloat=Field(ge=0,le=100000000)
class ReconnectInput(BaseModel):
    token:str=Field(min_length=1,max_length=10000)

def call(shop_id,action,start,end,**kwargs):
    try:return execute(shop_id,action,start,end,**kwargs)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc)) from exc

@router.get('')
def listing(request:Request):
    role=request.headers.get('x-tenant-role','owner')
    return {'items':shops.listing(),'primary_shop':'Основной WB — существующий кабинет','marketplace':'wb','can_manage':role=='owner','can_write':role in {'owner','manager'}}

@router.post('')
def create(payload:ShopInput,request:Request):
    if request.headers.get('x-tenant-role','owner')!='owner':raise HTTPException(status_code=403,detail='Подключать магазины может только владелец')
    try:
        client=WBAPI(payload.token)
        if not client.test_statistics():raise ValueError('Подключение не прошло проверку')
        identity=shops.account_identity(payload.token)
        primary=get_token()
        if primary and shops.account_identity(primary)==identity:raise ValueError('Это основной магазин; его данные уже в кабинете')
        shop_id=shops.create(payload.name,payload.token,identity)
    except Exception as exc:
        if isinstance(exc,ValueError):raise HTTPException(status_code=400,detail=str(exc)) from exc
        raise HTTPException(status_code=400,detail='Подключение магазина не прошло проверку') from exc
    return {'id':shop_id}

@router.post('/{shop_id}/connection')
def reconnect(shop_id:int,payload:ReconnectInput,request:Request):
    if request.headers.get('x-tenant-role','owner')!='owner':raise HTTPException(status_code=403,detail='Обновлять подключение может только владелец')
    try:
        if not WBAPI(payload.token).test_statistics():raise ValueError('Подключение не прошло проверку')
        shops.reconnect(shop_id,payload.token,shops.account_identity(payload.token))
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc)) from exc
    except Exception as exc:raise HTTPException(status_code=400,detail='Подключение не прошло проверку') from exc
    return {'message':'Подключение обновлено; данные сохранены'}

@router.get('/consolidated/report')
def consolidated(start:date,end:date):
    if end<start or (end-start).days>366:raise HTTPException(status_code=400,detail='Некорректный период')
    from calculations import build_dashboard
    from today_service import json_safe
    primary=json_safe(build_dashboard(start,end).financial)
    from db.core import last_sync
    sync_status=last_sync()
    primary_complete=bool(sync_status and sync_status.get('status')=='success')
    rows=[{'id':0,'name':'Основной WB','financial':primary,'available':True,'sources_complete':primary_complete}]
    deadline=monotonic()+120
    for shop in shops.listing():
        if not shop['active']:continue
        try:
            remaining=deadline-monotonic()
            if remaining<1:raise ValueError('Consolidated timeout')
            result=execute(shop['id'],'report',start.isoformat(),end.isoformat(),_timeout=min(45,int(remaining)))
            rows.append({'id':shop['id'],'name':shop['name'],'financial':result['financial'],'available':bool(shop['synced_at']),'sources_complete':result['sources_complete']})
        except ValueError:
            rows.append({'id':shop['id'],'name':shop['name'],'available':False,'financial':None})
    fields=('sales','after_wb','cost','profit','sale_units','return_units','net_units','exact_fifo_cogs_rub','current_cost_estimate_rub','insufficient_cogs_units')
    total={k:sum(float((r['financial'] or {}).get(k) or 0) for r in rows if r['available']) for k in fields}
    return {'shops':rows,'total':total,'complete':all(r['available'] and r.get('sources_complete',True) and (r['financial'] or {}).get('financial_period_complete') and (r['financial'] or {}).get('cogs_complete') for r in rows),'basis':'WB financial operations; shop costs remain isolated; external expenses not included'}

@router.get('/{shop_id}/report')
def report(shop_id:int,start:date,end:date):return call(shop_id,'report',start.isoformat(),end.isoformat())

@router.post('/{shop_id}/sync')
def sync(shop_id:int,payload:SyncInput):
    end=date.today();start=end-timedelta(days=payload.days-1)
    return call(shop_id,'sync',start.isoformat(),end.isoformat(),days=payload.days)

@router.patch('/{shop_id}/cost')
def cost(shop_id:int,payload:CostInput,request:Request):
    end=date.today();start=end-timedelta(days=29)
    return call(shop_id,'cost',start.isoformat(),end.isoformat(),cost=payload.model_dump(),actor=request.headers.get('x-tenant-account'))

@router.post('/{shop_id}/deactivate')
def deactivate(shop_id:int,request:Request):
    if request.headers.get('x-tenant-role','owner')!='owner':raise HTTPException(status_code=403,detail='Отключать магазины может только владелец')
    try:shops.deactivate(shop_id)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc)) from exc
    return {'message':'Магазин отключён; данные сохранены'}
