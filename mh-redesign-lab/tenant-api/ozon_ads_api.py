"""Local Ozon ad reports and explicitly requested bounded Performance reads."""
from datetime import date
from threading import Lock
from typing import Annotated
from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import Response
from pydantic import BaseModel,ConfigDict,Field,StrictStr
import ozon_ads_analytics as ads

router=APIRouter(prefix='/api/v1/ozon-accounting/advertising',tags=['Реклама Ozon'])
operation_lock=Lock()
client_cache=None

class Credentials(BaseModel):
    model_config=ConfigDict(extra='forbid')
    client_id:StrictStr=Field(min_length=1,max_length=2048)
    client_secret:StrictStr=Field(min_length=1,max_length=2048)
class Report(BaseModel):
    model_config=ConfigDict(extra='forbid')
    start_date:date
    end_date:date
    campaign_ids:list[Annotated[StrictStr,Field(pattern='^[1-9][0-9]{0,19}$')]]=Field(min_length=1,max_length=10)

def allow_write(request,owner=False):
    roles={'owner'} if owner else {'owner','manager'}
    if request.headers.get('x-tenant-role','').lower() not in roles:raise HTTPException(403,'Действие доступно владельцу.' if owner else 'Действие доступно владельцу и менеджеру.')
def failure(error):
    if isinstance(error,ads.PerformanceError):return HTTPException(429 if error.code=='rate_limit' else 422,{'code':error.code,'message':str(error),'retryable':error.retryable})
    return HTTPException(422,str(error))
def connected_client():
    global client_cache
    value=ads.credentials()
    if not value:raise HTTPException(409,'Подключите отдельный Performance API в блоке рекламы Ozon.')
    fingerprint=(str(ads.credential_path().resolve()),ads.credential_fingerprint(value))
    if client_cache is None or client_cache[0]!=fingerprint:
        clear_client()
        client_cache=(fingerprint,ads.PerformanceClient(value['client_id'],value['client_secret']))
    return client_cache[1],value
def clear_client():
    global client_cache
    if client_cache is not None:
        session=getattr(client_cache[1],'session',None)
        if session is not None:session.close()
    client_cache=None
def lock():
    if not operation_lock.acquire(blocking=False):raise HTTPException(409,'Проверка Performance API уже выполняется.')

@router.get('/credentials')
def credential_state():
    try:return {'configured':bool(ads.credentials()),'credential_type':'ozon-performance','seller_credentials_reused':False}
    except (ValueError,RuntimeError):return {'configured':False,'credential_type':'ozon-performance','error_code':'credential_storage','seller_credentials_reused':False}
@router.put('/credentials')
def configure(payload:Credentials,request:Request):
    allow_write(request,owner=True);lock()
    try:
        result=ads.save_credentials(payload.client_id,payload.client_secret);clear_client();return result
    except (ValueError,RuntimeError) as error:
        if isinstance(error,RuntimeError):raise HTTPException(409,'Не удалось сохранить защищённый ключ Performance API.') from None
        raise failure(error) from None
    finally:operation_lock.release()
@router.delete('/credentials')
def disconnect(request:Request):
    allow_write(request,owner=True);lock()
    try:ads.delete_credentials();clear_client();return {'configured':False}
    finally:operation_lock.release()
@router.get('')
def read(start_date:date,end_date:date):
    try:return ads.analytics(start_date.isoformat(),end_date.isoformat())
    except ValueError as error:raise failure(error) from None
@router.get('/export.csv')
def export(start_date:date,end_date:date):
    try:return Response(ads.export_csv(start_date.isoformat(),end_date.isoformat()),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="ozon-advertising.csv"'})
    except ValueError as error:raise failure(error) from None
@router.post('/reports')
def create(payload:Report,request:Request):
    allow_write(request)
    try:ads.period(payload.start_date.isoformat(),payload.end_date.isoformat())
    except ValueError as error:raise failure(error) from None
    lock()
    try:
        client,value=connected_client()
        return ads.start_report(client,value,payload.start_date.isoformat(),payload.end_date.isoformat(),payload.campaign_ids)
    except ValueError as error:raise failure(error) from None
    finally:operation_lock.release()
@router.get('/reports/{identifier}')
def job(identifier:str):
    try:return ads.public_job(ads.report_job(identifier))
    except ValueError as error:raise HTTPException(404,str(error)) from None
@router.post('/reports/{identifier}/poll')
def poll(identifier:str,request:Request):
    allow_write(request);lock()
    try:
        client,value=connected_client()
        return ads.poll_report(client,value,identifier)
    except ValueError as error:raise failure(error) from None
    finally:operation_lock.release()
