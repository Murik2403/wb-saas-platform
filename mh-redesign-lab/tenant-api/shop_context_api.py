from fastapi import APIRouter,Request,HTTPException
from pydantic import BaseModel,Field,StrictBool
import shop_context
from db import shared_warehouse as warehouse
from production_permissions import write_allowed

router=APIRouter(prefix='/api/v1/shop-context',tags=['shop-context'])

def failure(exc):
    raise HTTPException(status_code=exc.status,detail={'code':exc.code,'message':exc.message}) from exc

@router.get('/session')
def session(request:Request):
    try:return shop_context.session(request.headers,write_allowed)
    except shop_context.ShopContextError as exc:failure(exc)

class GrantInput(BaseModel):
    account:str=Field(min_length=1,max_length=200)
    can_write:StrictBool

@router.post('/{shop_id}/access')
def grant(shop_id:int,payload:GrantInput,request:Request):
    try:
        account,role=shop_context.actor(request.headers,True)
        if role!='owner':raise shop_context.ShopContextError('SHOP_ACCESS_DENIED','Доступ выдаёт только владелец')
        shop_context.authorize(shop_id,account,role)
        warehouse.grant(shop_id,payload.account,payload.can_write,role)
    except shop_context.ShopContextError as exc:failure(exc)
    except warehouse.WarehouseError as exc:raise HTTPException(status_code=400,detail=str(exc)) from exc
    return {'shop_id':shop_id,'account':payload.account,'can_write':payload.can_write}
