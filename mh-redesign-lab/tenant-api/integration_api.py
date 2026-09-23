"""Versioned, paginated read API. Gateway issues a scoped integration_reader role."""
from fastapi import APIRouter, Query
from db.core import connect
from today_service import json_safe

router = APIRouter(prefix="/api/v1/integration", tags=["Integration read API"])


def page(table, columns, limit, offset):
    # Table and columns are hardcoded by routes, never taken from requests.
    with connect() as conn:
        rows = conn.execute(f"SELECT {columns} FROM {table} ORDER BY 1 LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return json_safe({"items": [dict(row) for row in rows], "offset": offset, "limit": limit})


@router.get("/products")
def products(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    return page("products_catalog", "nm_id,supplier_article,product_name", limit, offset)


@router.get("/materials")
def materials(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    return page("material_inventory_color", "material_key,material_name,unit,full_rolls,partial_meters,roll_length,balance_known,tracking_mode", limit, offset)


@router.get("/releases")
def releases(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    return page("manufacturing_releases", "id,source_key,nm_id,quantity,production_date,bom_version_id,material_cost_rub,labor_cost_rub,depreciation_cost_rub,packaging_cost_rub,other_cost_rub,total_cost_rub,unit_cost_rub,status", limit, offset)
