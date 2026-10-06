"""Local read-only Ozon cabinet endpoints. Authorization is enforced by server middleware."""
from datetime import date
from typing import Literal
from fastapi import APIRouter, HTTPException
import ozon_cabinet as cabinet

router=APIRouter(prefix='/api/v1/ozon-cabinet',tags=['Кабинет Ozon'])

def _call(fn,start,end,**kwargs):
    try:return fn(start.isoformat(),end.isoformat(),**kwargs)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc

@router.get('/overview')
def overview(start_date:date,end_date:date):return _call(cabinet.overview,start_date,end_date)

@router.get('/stock')
def stock(start_date:date,end_date:date):return _call(cabinet.stock,start_date,end_date)

@router.get('/orders')
def orders(start_date:date,end_date:date,kind:Literal['fbs','fbo','all']='all'):return _call(cabinet.orders,start_date,end_date,kind=kind)

@router.get('/returns')
def returns(start_date:date,end_date:date):return _call(cabinet.returns,start_date,end_date)

@router.get('/control')
def control(start_date:date,end_date:date):return _call(cabinet.control,start_date,end_date)
