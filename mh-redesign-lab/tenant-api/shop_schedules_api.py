from fastapi import APIRouter,Request,HTTPException
from pydantic import BaseModel,ConfigDict,StrictBool,StrictInt,Field
import shop_context
from db import shop_schedules as schedules,shared_warehouse as warehouse
from db.core import connect

router=APIRouter(prefix='/api/v1/shop-schedules',tags=['shop-schedules'])

class ConfigureInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled:StrictBool
    expected_generation:StrictInt=Field(ge=0)
    interval_minutes:StrictInt=Field(ge=15,le=10080)
    period_days:StrictInt=Field(ge=1,le=90)
    metrics:list[str]=Field(max_length=8)

def actor(request):
    try:
        account,role=shop_context.actor(request.headers,True)
        schedules.account_id(account)
        return account,role
    except (ValueError,shop_context.ShopContextError) as exc:
        raise HTTPException(403,detail={'code':'SCHEDULE_SCOPE_REQUIRED','message':'Нужны подтверждённые права участника'}) from exc

def error(exc):
    return HTTPException(403 if isinstance(exc,warehouse.WarehouseScopeError) else 409,
                         detail={'code':'SCHEDULE_CONFLICT','message':str(exc)})

def selected(request,shop_id):
    if shop_context.selector(request.headers)!=shop_id:
        raise ValueError('Сначала явно выберите этот магазин')

@router.get('/{shop_id}')
def listing(shop_id:int,request:Request):
    account,role=actor(request)
    try:
        selected(request,shop_id)
        if shop_id<=0:raise ValueError('Расписание доступно дополнительному магазину')
        value=schedules.listing(shop_id,account,role)
        with connect() as conn:
            try:schedules.target(conn,shop_id,account,True);allowed=True
            except (ValueError,warehouse.WarehouseScopeError):allowed=False
        value['can_configure']=role=='owner'
        value['can_enable']=role=='owner' and allowed
        return value
    except (ValueError,warehouse.WarehouseError) as exc:raise error(exc) from exc

@router.put('/{shop_id}/{kind}')
def configure(shop_id:int,kind:str,body:ConfigureInput,request:Request):
    account,role=actor(request)
    try:
        selected(request,shop_id)
        return schedules.configure(shop_id,kind,account,role,request.headers.get('x-mh-request-key'),**body.model_dump())
    except (ValueError,warehouse.WarehouseError) as exc:raise error(exc) from exc
