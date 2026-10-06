"""Ozon insight routes: local reads and local plan writes, no upstream API calls."""
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

import ozon_insights as insights

router = APIRouter(prefix='/api/v1/ozon-insights', tags=['Аналитика Ozon'])
Namespace = Annotated[str, Query(min_length=1, max_length=80)]
Money = Annotated[StrictStr, Field(max_length=40)]


def _call(fn, *args, **kwargs):
    try: return fn(*args, **kwargs)
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/products')
def products(start_date: date, end_date: date, namespace: Namespace='main'):
    return _call(insights.products, start_date.isoformat(), end_date.isoformat(), namespace)


@router.get('/heatmap')
def heatmap(start_date: date, end_date: date, namespace: Namespace='main',
            metric: Literal['order_units','document_sales_units','document_revenue_rub']='order_units'):
    return _call(insights.heatmap, start_date.isoformat(), end_date.isoformat(), namespace, metric)


@router.get('/rnp')
def rnp(start_date: date, end_date: date, namespace: Namespace='main'):
    return _call(insights.rnp, start_date.isoformat(), end_date.isoformat(), namespace)


class Plan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    namespace: StrictStr = Field(default='main', min_length=1, max_length=80)
    sku: StrictStr = Field(pattern='^[1-9][0-9]{0,19}$')
    day: date
    units: Annotated[StrictInt, Field(ge=0,le=1000000)] | None


class Favorite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    namespace: StrictStr = Field(default='main', min_length=1, max_length=80)
    sku: StrictStr = Field(pattern='^[1-9][0-9]{0,19}$')
    favorite: StrictBool


def _write_allowed(request):
    if request.headers.get('x-tenant-role', '').lower() not in {'owner','manager'}:
        raise HTTPException(403, 'План доступен владельцу и менеджеру.')


@router.put('/rnp/plan')
def save_plan(payload: Plan, request: Request):
    _write_allowed(request)
    return _call(insights.save_plan, payload.namespace, payload.sku, payload.day.isoformat(), payload.units)


@router.put('/rnp/favorite')
def save_favorite(payload: Favorite, request: Request):
    _write_allowed(request)
    return _call(insights.save_favorite, payload.namespace, payload.sku, payload.favorite)


@router.get('/monthly')
def monthly(start_date: date, end_date: date, namespace: Namespace='main'):
    return _call(insights.monthly, start_date.isoformat(), end_date.isoformat(), namespace)


@router.get('/monthly.csv')
def monthly_csv(start_date: date, end_date: date, namespace: Namespace='main'):
    value = _call(insights.monthly_csv, start_date.isoformat(), end_date.isoformat(), namespace)
    return Response(value, media_type='text/csv', headers={
        'Content-Disposition':'attachment; filename=ozon-documentary-monthly.csv', 'X-Content-Type-Options':'nosniff'})


class TaxOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    month: StrictStr = Field(pattern='^[0-9]{4}-(0[1-9]|1[0-2])$')
    regime: Literal['ausn_income','ausn_income_expense','usn_income','usn_income_expense','osno_ip','osno_company']='ausn_income'
    period_mode: Literal['month','ytd'] | None = None
    rate: Money | None = None
    taxpayer_type: Literal['ip','company'] | None = None
    has_staff: StrictBool = False
    vat_mode: Literal['exempt','special5','special7','general0','general10','general20','general22'] = 'exempt'
    received: Money | None = None
    offset: Money | None = None
    compensation: Money | None = None
    other_income: Money | None = None
    refunds: Money | None = None
    expenses: Money | None = None
    loss: Money | None = None
    insurance: Money | None = None
    vat_output: Money | None = None
    vat_input: Money | None = None
    advances: Money | None = None
    other_ndfl_base: Money | None = None


class TaxInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start_date: date
    end_date: date
    namespace: StrictStr = Field(default='main', min_length=1, max_length=80)
    options: TaxOptions | None = None


@router.post('/tax-input')
def tax_input(payload: TaxInput):
    # Pure read calculation; no rate-setting mutation, posting or automatic cash-basis inference.
    options = payload.options.model_dump(exclude_none=True) if payload.options else None
    return _call(insights.tax_input, payload.start_date.isoformat(), payload.end_date.isoformat(), payload.namespace, options)
