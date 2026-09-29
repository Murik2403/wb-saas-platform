"""Local incident evidence/workflow endpoints, owner/manager mutations only."""
import base64
import binascii
from datetime import date
from urllib.parse import quote
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, StrictInt
from db import incident_workflow as db

router=APIRouter(prefix='/api/v1/incident-workflow')
def actor(request):
    role=request.headers.get('x-tenant-role','')
    if role not in {'owner','manager'}:raise HTTPException(403,'Только владелец или управляющий может изменить инцидент.')
    account=request.headers.get('x-tenant-account','').strip()
    return ('account:'+account+' / '+role) if account else role

def call(function,*args):
    try:return function(*args)
    except (ValueError,TypeError) as exc:raise HTTPException(422,str(exc)) from exc

class Details(BaseModel):
    assignee:str=Field(default='',max_length=200)
    deadline:date|None=None
    outcome:str=Field(default='',max_length=4000)
    status:str|None=None
class Evidence(BaseModel):
    filename:str=Field(max_length=180)
    media_type:str=Field(max_length=100)
    content_base64:str=Field(max_length=2800000)
class Hold(BaseModel):
    layer_id:StrictInt=Field(gt=0)
    location:str
    units:StrictInt=Field(gt=0,le=10000000)
    request_key:str=Field(min_length=8,max_length=100)
    confirmed:bool=False
class Release(BaseModel):
    reason:str=Field(min_length=1,max_length=2000)
    confirmed:bool=False
@router.get('/{case_id}')
def workspace(case_id:int):return call(db.workspace,case_id)
@router.put('/{case_id}/details')
def details(case_id:int,payload:Details,request:Request):
    who=actor(request);call(db.save_details,case_id,payload.assignee,payload.deadline.isoformat() if payload.deadline else None,payload.outcome,who,payload.status)
    return {'saved':True}
@router.post('/{case_id}/evidence')
def evidence(case_id:int,payload:Evidence,request:Request):
    who=actor(request)
    try:content=base64.b64decode(payload.content_base64,validate=True)
    except (ValueError,binascii.Error):raise HTTPException(422,'Некорректное кодирование вложения.') from None
    return {'id':call(db.attach,case_id,payload.filename,payload.media_type,content,who)}
@router.get('/{case_id}/evidence/{evidence_id}')
def download(case_id:int,evidence_id:int):
    row=call(db.evidence,case_id,evidence_id)
    return Response(content=bytes(row['content']),media_type='application/octet-stream',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(row['filename'],safe=''),'X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'",'Cache-Control':'private, no-store'})
@router.post('/{case_id}/holds')
def hold(case_id:int,payload:Hold,request:Request):
    who=actor(request)
    if not payload.confirmed:raise HTTPException(422,'Подтвердите резерв конкретного FIFO-слоя.')
    return {'id':call(db.hold,case_id,payload.layer_id,payload.location,payload.units,payload.request_key,who)}
@router.post('/{case_id}/holds/{hold_id}/release')
def release(case_id:int,hold_id:int,payload:Release,request:Request):
    who=actor(request)
    if not payload.confirmed:raise HTTPException(422,'Подтвердите снятие карантина.')
    call(db.release,case_id,hold_id,payload.reason,who);return {'released':True}
