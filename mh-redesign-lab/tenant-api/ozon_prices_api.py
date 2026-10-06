from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import Response
from pydantic import BaseModel,ConfigDict,Field,StrictBool,StrictStr
from threading import Lock
import ozon_price_operations as prices
router=APIRouter(prefix='/api/v1/ozon-prices',tags=['Цены Ozon'])
operation_lock=Lock()
class DraftItem(BaseModel):
    model_config=ConfigDict(extra='forbid')
    product_id:StrictStr
    price:StrictStr
class Draft(BaseModel):
    model_config=ConfigDict(extra='forbid')
    items:list[DraftItem]=Field(min_length=1,max_length=1000)
    source_fingerprint:StrictStr
class Confirmation(Draft):
    request_key:StrictStr
    confirmed:StrictBool

def write(request):
    if request.headers.get('x-tenant-role','').lower() not in {'owner','manager'}:raise HTTPException(403,'Доступно владельцу и менеджеру.')
    if getattr(prices.config,'IS_DEMO',False):raise HTTPException(403,'Демонстрационный кабинет не отправляет цены.')
def call(fn,*args):
    try:return fn(*args)
    except prices.PriceError as e:raise HTTPException(e.status,{'code':e.code,'message':str(e)}) from None

def guarded(fn,*args):
    if not operation_lock.acquire(blocking=False):raise HTTPException(409,'Операция с ценами уже выполняется.')
    try:return call(fn,*args)
    finally:operation_lock.release()
@router.get('')
def read():return call(prices.read)
@router.get('/export.csv')
def export():return Response(prices.export_csv(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="ozon-prices.csv"'})
@router.post('/refresh')
def refresh(request:Request):write(request);return guarded(prices.refresh)
@router.post('/preview')
def preview(payload:Draft,request:Request):write(request);return call(prices.preview,[i.model_dump() for i in payload.items],payload.source_fingerprint)
@router.post('/confirm')
def confirm(payload:Confirmation,request:Request):write(request);return guarded(prices.confirm,[i.model_dump() for i in payload.items],payload.source_fingerprint,payload.request_key,payload.confirmed,request.headers.get('x-tenant-account',''))
