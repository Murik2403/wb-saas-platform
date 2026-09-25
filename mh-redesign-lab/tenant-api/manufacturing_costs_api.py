"""Validated adapter for tenant-local production accounting."""
from datetime import date
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, FiniteFloat, ConfigDict
from db import manufacturing as store
from db.core import read_table
from today_service import json_safe

router = APIRouter(prefix="/api/v1/manufacturing-costs", tags=["Производственные затраты"])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Material(Input):
    component_type: Literal["raw_material", "semi_finished"] = "raw_material"
    material_name: str | None = Field(default=None, min_length=1, max_length=250)
    nm_id: int | None = Field(default=None, gt=0)
    chrt_id: int | None = Field(default=None, ge=0)
    quantity_per_unit: FiniteFloat = Field(gt=0)
    unit: str | None = Field(default=None, min_length=1, max_length=16)


class Operation(Input):
    name: str = Field(min_length=1, max_length=250)
    rate_per_unit: FiniteFloat = Field(ge=0)
    equipment_id: int | None = Field(default=None, gt=0)
    equipment_usage_per_unit: FiniteFloat = Field(default=0, ge=0)


class Bom(Input):
    nm_id: int = Field(gt=0)
    chrt_id: int = Field(default=0, ge=0)
    materials: list[Material] = Field(min_length=1, max_length=100)
    operations: list[Operation] = Field(default_factory=list, max_length=100)
    packaging_per_unit: FiniteFloat = Field(default=0, ge=0)
    other_per_unit: FiniteFloat = Field(default=0, ge=0)
    note: str = Field(default="", max_length=2000)


class Equipment(Input):
    name: str = Field(min_length=1, max_length=250)
    acquisition_cost_rub: FiniteFloat = Field(gt=0)
    salvage_value_rub: FiniteFloat = Field(default=0, ge=0)
    resource_quantity: FiniteFloat = Field(gt=0)
    resource_unit: Literal["units", "hours"]


class Piecework(Input):
    source_key: str = Field(min_length=1, max_length=200)
    nm_id: int = Field(gt=0)
    chrt_id: int = Field(default=0, ge=0)
    operation_name: str = Field(min_length=1, max_length=250)
    worker: str = Field(min_length=1, max_length=250)
    quantity: FiniteFloat = Field(gt=0)
    rate_per_unit: FiniteFloat = Field(ge=0)
    work_date: date
    event_key: str = Field(min_length=1, max_length=200)


class Defect(Input):
    layer_id: int = Field(gt=0)
    quantity: FiniteFloat = Field(gt=0)
    event_key: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)
    movement_date: date


class Release(Input):
    nm_id: int = Field(gt=0)
    chrt_id: int = Field(default=0, ge=0)
    quantity: int = Field(gt=0)
    production_date: date
    source_key: str = Field(min_length=1, max_length=200)
    bom_version_id: int | None = Field(default=None, gt=0)
    supplier_article: str = Field(default="", max_length=250)
    product_name: str = Field(default="", max_length=500)


def invoke(function, payload):
    try:
        return function(**payload.model_dump(mode="json"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/boms")
def save_bom(payload: Bom):
    return invoke(store.save_bom_version, payload)


@router.post("/equipment")
def save_equipment(payload: Equipment):
    return invoke(store.save_equipment, payload)


@router.post("/piecework")
def save_piecework(payload: Piecework):
    return invoke(store.post_piecework, payload)


@router.post("/defects")
def save_defect(payload: Defect):
    return invoke(store.write_off_material_defect, payload)


@router.post("/releases")
def save_release(payload: Release):
    return invoke(store.post_manufacturing_release, payload)


@router.get("")
def workspace(request: Request):
    from production_permissions import write_allowed
    from config import IS_DEMO
    role = "viewer" if IS_DEMO else request.headers.get("x-tenant-role", "owner").lower()
    allowed_actions = [name for name in ["boms", "equipment", "piecework", "defects", "releases"] if write_allowed(role, "POST", f"/api/v1/manufacturing-costs/{name}")]
    if role in {"owner", "manager"}:
        allowed_actions.append("reverse")
    import json
    catalog = read_table("products_catalog")
    products = []
    if not catalog.empty and {"nm_id", "product_name"}.issubset(catalog.columns):
        for row in catalog.to_dict("records"):
            try:
                raw = json.loads(row.get("raw_json") or "{}")
            except (TypeError, ValueError):
                raw = {}
            if not isinstance(raw, dict):
                raw = {}
            sizes = raw.get("sizes")
            variants = []
            if isinstance(sizes, list):
                for size in sizes:
                    if not isinstance(size, dict):
                        continue
                    try:
                        chrt_id = int(size.get("chrtID", size.get("chrtId", size.get("chrt_id", 0))) or 0)
                    except (TypeError, ValueError):
                        continue
                    if chrt_id <= 0:
                        continue
                    sku_values = size.get("skus") if isinstance(size.get("skus"), list) else []
                    barcode = ", ".join(str(value) for value in sku_values if value)
                    label = str(size.get("techSize") or size.get("wbSize") or "").strip()
                    variants.append({"chrt_id": chrt_id, "variant_label": label, "barcodes": barcode})
            common = {"nm_id": int(row["nm_id"]), "product_name": str(row.get("product_name") or ""),
                      "supplier_article": str(row.get("supplier_article") or raw.get("vendorCode") or "")}
            if variants:
                for variant in variants:
                    products.append({**common, **variant})
            else:
                products.append({**common, "chrt_id": 0, "variant_label": "", "barcodes": ""})
        products = list({(p["nm_id"], p["chrt_id"]): p for p in products}.values())
    return json_safe({"allowed_actions": allowed_actions, "materials": store.list_material_catalog(), "material_layers": store.list_material_layers(), "products": products,
                      "boms": store.list_bom_versions(), "equipment": store.list_equipment(), "piecework": store.list_piecework(),
                      "defects": store.list_material_defects(), "releases": store.list_manufacturing_releases()})


@router.post("/movements/{movement_id}/reverse")
def reverse(movement_id: int):
    from db.core import connect
    from db.inventory_movements import undo_inventory_movement
    with connect() as conn:
        row = conn.execute("SELECT id FROM manufacturing_releases WHERE movement_id=? UNION ALL SELECT id FROM manufacturing_defects WHERE movement_id=?", (movement_id, movement_id)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Производственная проводка не найдена")
    try:
        result = undo_inventory_movement(movement_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result.get("errors"):
        raise HTTPException(status_code=422, detail="; ".join(result["errors"]))
    if result.get("ok") is False:
        raise HTTPException(status_code=422, detail=result.get("message", "Отмена не выполнена"))
    return json_safe(result)
