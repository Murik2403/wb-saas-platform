"""Explicit central warehouse operations, never a secondary physical database."""
from datetime import date
from uuid import UUID
from contextlib import contextmanager
from typing import Literal
from fastapi import APIRouter,Request,HTTPException
from pydantic import BaseModel,ConfigDict,Field,StrictInt,StrictBool
from starlette.concurrency import run_in_threadpool
import shop_context
import shared_warehouse_setup as setup
from db.core import connect
from db import shared_warehouse as warehouse

router=APIRouter(prefix='/api/v1/shared-warehouse',tags=['Общий склад'])

class Input(BaseModel):
    model_config=ConfigDict(extra='forbid')
class SKU(Input):
    nm_id:StrictInt=Field(gt=0,le=2**53-1)
    chrt_id:StrictInt=Field(ge=0,le=2**53-1)
class Item(SKU):name:str=Field(min_length=1,max_length=120)
class Mapping(SKU):item_id:UUID
class MappingConfirm(Mapping):fingerprint:str=Field(pattern=r'^[0-9a-f]{64}$')
class Quantity(Input):
    quantity:StrictInt=Field(gt=0,le=10**9)
    document_date:date
class Reserve(SKU,Quantity):pass
class ReserveConfirm(Reserve):
    source_ref:str=Field(min_length=1,max_length=120)
    fingerprint:str=Field(pattern=r'^[0-9a-f]{64}$')
class Document(Input):
    source_ref:str=Field(min_length=1,max_length=120)
    document_date:date
    revision:StrictInt=Field(gt=0,le=10**9)
class Transition(Document):kind:Literal['commit','receipt','release']
class SaleConfirm(Quantity):
    source_ref:str=Field(min_length=1,max_length=120)
    fingerprint:str=Field(pattern=r'^[0-9a-f]{64}$')
class Allocation(Input):
    layer_id:StrictInt=Field(gt=0,le=2**53-1)
    units:StrictInt=Field(gt=0,le=10**9)
class Return(Document,Quantity):
    sale_request_key:str=Field(min_length=1,max_length=120)
    allocation_plan:list[Allocation]|None=Field(default=None,max_length=1000)
    allocation_proof:str|None=Field(default=None,max_length=500)
    disposition:Literal['available','quarantine']='available'
class Hold(Document,Quantity):
    reservation_id:UUID
    layer_id:StrictInt=Field(gt=0,le=2**53-1)
    location:Literal['inbound','wb']
    proof:str=Field(min_length=1,max_length=500)
class HoldRelease(Document):reason:str=Field(min_length=1,max_length=500)
class FinancialBinding(Input):
    rrd_id:StrictInt=Field(gt=0,le=2**53-1)
    document_key:UUID
    manual:StrictBool=False
    source_proof:str|None=Field(default=None,max_length=500)
class FinancialBindingConfirm(FinancialBinding):
    fingerprint:str=Field(pattern=r'^[0-9a-f]{64}$')

def actor(request):
    try:
        account,role=shop_context.actor(request.headers,True)
        return setup.actor(account,role),role
    except (shop_context.ShopContextError,warehouse.WarehouseError) as exc:
        raise HTTPException(403,detail='Требуется подтверждённая сессия этого кабинета') from exc
def target(request,shop_id):
    if shop_context.selector(request.headers)!=shop_id:
        raise HTTPException(409,detail='Откройте выбранный магазин перед действием')
    return actor(request)
def key(request):
    value=request.headers.get('x-mh-request-key','')
    try:
        parsed=UUID(value)
        if str(parsed)!=value:raise ValueError()
    except ValueError:raise HTTPException(409,detail='Нужен уникальный ключ подтверждения действия')
    return value
def checked(function,*args,**kwargs):
    try:return function(*args,**kwargs)
    except warehouse.WarehouseError as exc:raise HTTPException(409,detail={'code':exc.code,'message':str(exc)}) from exc
    except ValueError as exc:raise HTTPException(400,detail='Проверьте данные и вариант товара') from exc

@contextmanager
def transaction(shop_id,account,role,write=True):
    setup.initialize()
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        setup.access(conn,shop_id,account,role,write)
        yield conn

def reservation(conn,identifier,shop_id):
    row=conn.execute('SELECT shop_id FROM warehouse_reservations WHERE id=?',(str(identifier),)).fetchone()
    if not row or row['shop_id']!=shop_id:raise warehouse.WarehouseScopeError('Документ другого магазина недоступен')

@router.get('/{shop_id}/workspace')
def workspace(shop_id:int,request:Request):
    return checked(setup.workspace,shop_id,*target(request,shop_id))

@router.get('/{shop_id}/financial-source')
def financial_source(shop_id:int,request:Request):
    import shared_warehouse_finance as finance
    return checked(finance.rows,shop_id,*target(request,shop_id))

@router.post('/{shop_id}/financial-bindings/preview')
def financial_preview(shop_id:int,body:FinancialBinding,request:Request):
    import shared_warehouse_finance as finance
    return checked(finance.preview,shop_id,body.rrd_id,str(body.document_key),body.manual,body.source_proof,*target(request,shop_id))

@router.post('/{shop_id}/financial-bindings/confirm')
def financial_confirm(shop_id:int,body:FinancialBindingConfirm,request:Request):
    import shared_warehouse_finance as finance
    return checked(finance.confirm,shop_id,body.rrd_id,str(body.document_key),body.manual,body.source_proof,body.fingerprint,*target(request,shop_id),key(request))

@router.post('/primary-binding')
async def binding(body:Input,request:Request):
    if shop_context.selector(request.headers)!=0:raise HTTPException(409,'Откройте основной магазин')
    return await run_in_threadpool(checked,setup.bind_primary,*actor(request))

@router.post('/items')
def item(body:Item,request:Request):
    if shop_context.selector(request.headers)!=0:raise HTTPException(409,'Настройте производственный каталог в основном магазине')
    account,role=actor(request)
    if role!='owner':raise HTTPException(403,'Соответствия настраивает владелец')
    return checked(setup.register_item,body.nm_id,body.chrt_id,body.name,account,role,key(request))

@router.post('/{shop_id}/mappings/preview')
def mapping_preview(shop_id:int,body:Mapping,request:Request):
    return checked(setup.mapping_preview,shop_id,body.nm_id,body.chrt_id,str(body.item_id),*target(request,shop_id))

@router.post('/{shop_id}/mappings/confirm')
def mapping_confirm(shop_id:int,body:MappingConfirm,request:Request):
    return checked(setup.mapping_confirm,shop_id,body.nm_id,body.chrt_id,str(body.item_id),body.fingerprint,*target(request,shop_id),key(request))

def reserve_operation(shop_id,body,account,role,request_key=None):
    with transaction(shop_id,account,role,request_key is not None) as conn:
        if request_key is None:
            result=warehouse._preview(conn,shop_id,body.nm_id,body.chrt_id,body.quantity,body.document_date.isoformat(),account,role)
            return dict(result,fingerprint=warehouse.digest(result))
        return warehouse.reserve(shop_id,body.nm_id,body.chrt_id,body.quantity,body.document_date.isoformat(),body.source_ref,request_key,body.fingerprint,account,role,_conn=conn)

@router.post('/{shop_id}/reserve/preview')
def reserve_preview(shop_id:int,body:Reserve,request:Request):
    return checked(reserve_operation,shop_id,body,*target(request,shop_id))

@router.post('/{shop_id}/reserve/confirm')
def reserve_confirm(shop_id:int,body:ReserveConfirm,request:Request):
    return checked(reserve_operation,shop_id,body,*target(request,shop_id),key(request))

def lifecycle(shop_id,identifier,kind,body,account,role,request_key=None):
    with transaction(shop_id,account,role,kind!='sale_preview') as conn:
        reservation(conn,identifier,shop_id)
        if kind=='transition':return warehouse.transition(str(identifier),body.kind,body.source_ref,request_key,body.document_date.isoformat(),body.revision,account,role,_conn=conn)
        if kind=='sale_preview':
            result=warehouse._sale_preview(conn,warehouse._reservation(conn,str(identifier),account,role,False),body.quantity,body.document_date.isoformat())
            return dict(result,fingerprint=warehouse.digest(result))
        return warehouse.sell(str(identifier),body.quantity,body.document_date.isoformat(),body.source_ref,request_key,body.fingerprint,account,role,_conn=conn)

@router.post('/{shop_id}/reservations/{reservation_id}/transition')
def transition(shop_id:int,reservation_id:UUID,body:Transition,request:Request):
    return checked(lifecycle,shop_id,reservation_id,'transition',body,*target(request,shop_id),key(request))

@router.post('/{shop_id}/reservations/{reservation_id}/sale/preview')
def sale_preview(shop_id:int,reservation_id:UUID,body:Quantity,request:Request):
    return checked(lifecycle,shop_id,reservation_id,'sale_preview',body,*target(request,shop_id))

@router.post('/{shop_id}/reservations/{reservation_id}/sale/confirm')
def sale_confirm(shop_id:int,reservation_id:UUID,body:SaleConfirm,request:Request):
    return checked(lifecycle,shop_id,reservation_id,'sale',body,*target(request,shop_id),key(request))

def return_operation(shop_id,body,account,role,request_key,preview=False):
    with transaction(shop_id,account,role,not preview) as conn:
        original=conn.execute("SELECT reservation_id,shop_id FROM warehouse_documents WHERE request_key=? AND kind='sale'",(body.sale_request_key,)).fetchone()
        if not original or original['shop_id']!=shop_id:raise warehouse.WarehouseScopeError('Нужен исходный документ продажи выбранного магазина')
        result=warehouse.return_sale(body.sale_request_key,body.quantity,body.document_date.isoformat(),body.source_ref,request_key,body.revision,account,role,
          allocation_plan=None if body.allocation_plan is None else [row.model_dump() for row in body.allocation_plan],allocation_proof=body.allocation_proof,disposition=body.disposition,_conn=conn)
        if preview:conn.rollback()
        return result

@router.post('/{shop_id}/returns/preview')
def return_preview(shop_id:int,body:Return,request:Request):
    from uuid import uuid4
    return checked(return_operation,shop_id,body,*target(request,shop_id),str(uuid4()),True)

@router.post('/{shop_id}/returns/confirm')
def return_confirm(shop_id:int,body:Return,request:Request):
    return checked(return_operation,shop_id,body,*target(request,shop_id),key(request))

def hold_operation(shop_id,body,account,role,request_key):
    with transaction(shop_id,account,role) as conn:
        reservation(conn,body.reservation_id,shop_id)
        return warehouse.quarantine_owned(str(body.reservation_id),body.layer_id,body.location,body.quantity,body.document_date.isoformat(),body.source_ref,request_key,body.revision,body.proof,account,role,_conn=conn)

@router.post('/{shop_id}/quarantine/confirm')
def quarantine(shop_id:int,body:Hold,request:Request):
    return checked(hold_operation,shop_id,body,*target(request,shop_id),key(request))

def release_operation(shop_id,hold_id,body,account,role,request_key):
    with transaction(shop_id,account,role) as conn:
        original=conn.execute('SELECT shop_id FROM warehouse_owned_holds WHERE id=?',(str(hold_id),)).fetchone()
        if not original or original['shop_id']!=shop_id:raise warehouse.WarehouseScopeError('Карантин другого магазина недоступен')
        return warehouse.release_owned_hold(str(hold_id),body.document_date.isoformat(),body.source_ref,request_key,body.revision,body.reason,account,role,_conn=conn)

@router.post('/{shop_id}/holds/{hold_id}/release')
def release(shop_id:int,hold_id:UUID,body:HoldRelease,request:Request):
    return checked(release_operation,shop_id,hold_id,body,*target(request,shop_id),key(request))


class ImportedBasis(Input):
    nm_id:StrictInt=Field(gt=0,le=2**53-1)
    reason:str=Field(min_length=3,max_length=500)
class ImportedBasisConfirm(ImportedBasis):
    fingerprint:str=Field(pattern=r'^[0-9a-f]{64}$')

@router.post('/imported-basis/preview')
def imported_basis_preview(body:ImportedBasis,request:Request):
    if shop_context.selector(request.headers)!=0:raise HTTPException(409,'Откройте основной магазин для передачи готовых партий')
    import imported_shared_basis as transfer
    return checked(transfer.preview,body.nm_id,body.reason,*actor(request))

@router.post('/imported-basis/confirm')
def imported_basis_confirm(body:ImportedBasisConfirm,request:Request):
    if shop_context.selector(request.headers)!=0:raise HTTPException(409,'Откройте основной магазин для передачи готовых партий')
    import imported_shared_basis as transfer
    return checked(transfer.confirm,body.nm_id,body.reason,body.fingerprint,*actor(request),key(request))
