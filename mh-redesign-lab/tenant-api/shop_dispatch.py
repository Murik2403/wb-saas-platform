"""Request-local routing to an isolated selected shop after role/origin gates."""
import base64
import re
from datetime import date,timedelta
from starlette.responses import JSONResponse,Response
from starlette.concurrency import run_in_threadpool
import shop_context
from shop_workspace_service import prepare
from db import shop_request_bridge as bridge,shared_warehouse as warehouse

async def dispatch(request,call_next):
    try:
        shop_id=shop_context.selector(request.headers)
        scope=shop_context.route_scope(request.method,request.url.path,request.url.query)
        if shop_id==0:
            response=await call_next(request)
            label=(scope or {}).get('scope','tenant_shared')
            response.headers.update(shop_context.response_headers(0,None,'tenant_shared' if label=='forbidden' else label))
            return response
        account,role=shop_context.actor(request.headers,True)
        if not scope:raise shop_context.ShopContextError('SHOP_ROUTE_UNSUPPORTED','Этот раздел ещё не поддерживает выбранный магазин',409)
        if scope['scope']=='forbidden':raise shop_context.ShopContextError('SHOP_ROUTE_FORBIDDEN','Эта операция недоступна для дополнительного WB-магазина',409)
        local=scope['scope']=='shop_local';write=request.method not in {'GET','HEAD','OPTIONS'}
        record=await run_in_threadpool(shop_context.authorize,shop_id,account,role,local and write)
        if not local:
            response=await call_next(request)
            record=await run_in_threadpool(shop_context.authorize,shop_id,account,role)
            response.headers.update(shop_context.response_headers(shop_id,record,'tenant_shared'))
            return response
        body=await bounded_body(request)
        envelope={'method':request.method,'path':request.url.path,'query':request.url.query,
                  'body':base64.b64encode(body).decode(),'content_type':request.headers.get('content-type','application/json')}
        revision=None;key=None
        if write:
            revision_header=request.headers.get('x-mh-shop-revision','')
            if not re.fullmatch(r'0|[1-9][0-9]{0,11}',revision_header):raise shop_context.ShopContextError('SHOP_REVISION_REQUIRED','Нужна версия выбранного магазина',409)
            revision=int(revision_header);key=request.headers.get('x-mh-request-key')
            cached=await run_in_threadpool(bridge.replay,shop_id,record['account_identity'],revision,key,envelope,account,role)
            if cached:
                result=cached['result']['response']
                record=await run_in_threadpool(shop_context.authorize,shop_id,account,role,True)
                return response_of(result,shop_id,record)
            if revision!=record['revision']:raise warehouse.WarehouseConflict('Версия магазина изменилась')
        end=date.today();start=end-timedelta(days=29)
        prepared=await run_in_threadpool(prepare,shop_id,'request',start.isoformat(),end.isoformat(),request=envelope,actor=account,role=role)
        result=prepared['response']
        if prepared['identity']!=record['account_identity'] or prepared['base_revision']!=record['revision']:
            raise warehouse.WarehouseConflict('Контекст магазина изменился при подготовке')
        if write and result['status']<400:
            committed=await run_in_threadpool(bridge.commit,shop_id,record['account_identity'],revision,key,envelope,account,role,
              snapshot=prepared['snapshot'],response=result,source_errors=prepared['source_errors'],synced=prepared.get('synced') is True)
            result=committed['result']['response']
        record=await run_in_threadpool(shop_context.authorize,shop_id,account,role)
        if not write and record['revision']!=prepared['base_revision']:
            raise warehouse.WarehouseConflict('Данные магазина изменились во время чтения')
        return response_of(result,shop_id,record)
    except shop_context.ShopContextError as exc:
        return JSONResponse(status_code=exc.status,content={'detail':{'code':exc.code,'message':exc.message}})
    except warehouse.WarehouseError as exc:
        return JSONResponse(status_code=409,content={'detail':{'code':exc.code,'message':str(exc)}})
    except ValueError:
        return JSONResponse(status_code=409,content={'detail':{'code':'SHOP_PREPARATION_FAILED','message':'Действие магазина не завершилось; прежние данные сохранены'}})

async def bounded_body(request,limit=1024*1024):
    body=bytearray()
    async for chunk in request.stream():
        if len(chunk)>limit-len(body):
            raise shop_context.ShopContextError('SHOP_REQUEST_TOO_LARGE','Запрос слишком большой',400)
        body.extend(chunk)
    return bytes(body)

def response_of(result,shop_id,record):
    headers=shop_context.response_headers(shop_id,record,'shop_local')
    disposition=result.get('content_disposition')
    retry=result.get('retry_after')
    if result['status']==503 and isinstance(retry,str) and re.fullmatch(r'[0-9]{1,3}',retry) and 1<=int(retry)<=300:
        headers['Retry-After']=retry
    if isinstance(disposition,str) and len(disposition)<=500 and '\r' not in disposition and '\n' not in disposition:
        headers['Content-Disposition']=disposition
    return Response(content=base64.b64decode(result['body']),status_code=result['status'],
      media_type=result['content_type'],headers=headers)
