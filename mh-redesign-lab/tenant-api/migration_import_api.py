from typing import Literal
from uuid import UUID
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from db.migration_import import preview_import, confirm_import, import_history
router=APIRouter(prefix="/api/v1/migration-import",tags=["Перенос CSV"])
class Input(BaseModel):
    model_config=ConfigDict(extra="forbid")
    kind: Literal["products","materials","opening_materials","finished_lots","historical_documents"]
    csv_text: str = Field(min_length=1,max_length=1_000_000)
    mapping: dict[str,str]
class Confirm(Input):
    request_key: UUID
    preview_fingerprint: str = Field(pattern="^[a-f0-9]{64}$")
def invoke(function):
    try:return function()
    except ValueError as error:raise HTTPException(422,str(error)) from error
def primary(payload,request):
    if payload.kind in {'finished_lots','historical_documents'} and request.headers.get('x-mh-shop-id','0')!='0':raise HTTPException(409,'Ручной перенос готовых партий/истории доступен только основному магазину')
@router.post("/preview")
def preview(payload:Input,request:Request):
    primary(payload,request)
    return invoke(lambda:preview_import(payload.kind,payload.csv_text,payload.mapping))
@router.post("/confirm")
def confirm(payload:Confirm,request:Request):
    primary(payload,request)
    return invoke(lambda:confirm_import(payload.kind,payload.csv_text,payload.mapping,str(payload.request_key),payload.preview_fingerprint,request.headers.get("x-tenant-account")))
@router.get("/history")
def history():return {"items":import_history()}
