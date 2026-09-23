"""React access to the tenant's existing WIP ledger and production capacity.

Mount on the main app to inherit its authentication and demo-write middleware.
This module never initializes a database or starts background jobs.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from db.core import connect
from db.production import get_production_capacity, save_production_capacity
from db.inventory_movements import undo_inventory_movement
from db.wip import (issue_wip_material, complete_wip_blank_batch,
                    post_manual_wip_packaging, read_wip_blank_batches,
                    read_wip_blank_summary, read_wip_blank_allocations, wip_module_status)
from today_service import json_safe

router = APIRouter(prefix="/api/v1/production-workspace", tags=["production-workspace"])
PositiveInt = Annotated[StrictInt, Field(gt=0, le=10000000)]
NonnegativeInt = Annotated[StrictInt, Field(ge=0, le=10000000)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class IssueInput(Input):
    request_id: UUID
    batch_date: date
    material_name: str = Field(min_length=1, max_length=200)
    blank_type: str = Field(min_length=1, max_length=200)
    meters_used: float = Field(gt=0.0005, le=10000000, allow_inf_nan=False)
    note: str = Field(default="", max_length=2000)


class CompleteInput(Input):
    produced_units: PositiveInt
    scrap_units: NonnegativeInt = 0
    note: str = Field(default="", max_length=2000)


class ReverseInput(Input):
    stage: Literal["issue", "completion"]


class PackageInput(Input):
    request_id: UUID
    nm_id: PositiveInt
    qty_sets: PositiveInt
    packaging_date: date
    note: str = Field(default="", max_length=2000)


class CapacityInput(Input):
    capacity_known: StrictBool
    pieces_per_day: NonnegativeInt
    workdays: list[Annotated[StrictInt, Field(ge=0, le=6)]] = Field(min_length=1, max_length=7)
    horizon_days: Annotated[StrictInt, Field(ge=1, le=90)]
    fulfillment_lead_days: Annotated[StrictInt, Field(ge=0, le=365)] = 0
    emergency_cover_days: Annotated[StrictInt, Field(ge=1, le=365)] = 7
    expedited_fbo_lead_days: Annotated[StrictInt, Field(ge=0, le=365)] = 3
    fbs_lead_days: Annotated[StrictInt, Field(ge=0, le=365)] = 0
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def valid_capacity(self):
        if self.capacity_known and self.pieces_per_day == 0:
            raise ValueError("Укажите мощность больше нуля или отметьте её как неизвестную")
        if len(set(self.workdays)) != len(self.workdays):
            raise ValueError("Рабочие дни не должны повторяться")
        return self


def _result(result: dict) -> dict:
    if result.get("errors"):
        raise HTTPException(422, detail="; ".join(map(str, result["errors"])))
    return json_safe(result)


def _batch(batch_id: int) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM wip_blank_batches WHERE id=?", (batch_id,)).fetchone()
    if row is None:
        raise HTTPException(404, detail="Партия НЗП не найдена")
    return dict(row)


def _capacity() -> dict:
    capacity = get_production_capacity()
    capacity["capacity_known"] = bool(capacity["capacity_known"])
    capacity["workdays"] = [int(x) for x in str(capacity["workdays"]).split(",") if x.strip()]
    return capacity


@router.get("")
def workspace(start: date | None = None, days: int = Query(14, ge=1, le=90)) -> dict:
    start = start or date.today()
    end = start + timedelta(days=days - 1)
    capacity = _capacity()
    with connect() as conn:
        materials = [dict(row) for row in conn.execute(
            "SELECT *, full_rolls*roll_length+partial_meters AS available_meters "
            "FROM material_inventory_color ORDER BY material_name")]
        products = [dict(row) for row in conn.execute(
            "SELECT p.nm_id,p.supplier_article,p.material_name,p.blank_type,p.pack_size,"
            "COALESCE(c.product_name,'') AS product_name FROM production_settings p "
            "LEFT JOIN products_catalog c ON c.nm_id=p.nm_id WHERE p.enabled=1 ORDER BY p.nm_id")]
        tasks = [dict(row) for row in conn.execute(
            "SELECT t.*,p.pack_size FROM execution_tasks t LEFT JOIN production_settings p ON p.nm_id=t.nm_id "
            "WHERE LOWER(TRIM(t.task_type)) IN ('production','производство') "
            "AND (t.task_date BETWEEN ? AND ? OR t.task_date IS NULL OR t.task_date='') "
            "ORDER BY t.task_date,t.task_key", (start.isoformat(), end.isoformat()))]
    calendar = []
    for offset in range(days):
        current = start + timedelta(days=offset)
        rows = [t for t in tasks if t["task_date"] == current.isoformat() and str(t["status"]).casefold() not in {"отменено", "отменён", "отменен", "cancelled"}]
        # Execution task quantities are kits; capacity is physical pieces/day.
        missing_pack = any(not t["pack_size"] for t in rows)
        planned = None if missing_pack else sum(max(0, t["planned_units"] or 0) * t["pack_size"] for t in rows)
        actual = None if missing_pack else sum(max(0, t["actual_units"] or 0) * t["pack_size"] for t in rows)
        available = (capacity["pieces_per_day"] if current.weekday() in capacity["workdays"] else 0) if capacity["capacity_known"] else None
        calendar.append({"date": current.isoformat(), "planned_pieces": planned, "actual_pieces": actual,
                         "capacity_pieces": available, "overload_pieces": max(0, planned - available) if planned is not None and available is not None else None,
                         "tasks": rows, "missing_pack_size": missing_pack})
    # Legacy *_meters columns store base-unit quantities. Never convert kg/pcs
    # into metres: the material catalogue owns the unit of its FIFO quantity.
    units = {m["material_key"]: str(m.get("unit") or "м") for m in materials}
    batches = [{**b, "unit": units.get(b["material_key"], "неизвестно")} for b in read_wip_blank_batches().to_dict("records")]
    summary = [{**b, "unit": units.get(b["material_key"], "неизвестно")} for b in read_wip_blank_summary().to_dict("records")]
    return json_safe({"wip": {"status": wip_module_status(), "batches": batches,
                               "summary": summary, "allocations": read_wip_blank_allocations(100).to_dict("records")},
                      "materials": materials, "products": products, "capacity": capacity, "calendar": calendar,
                      "undated_tasks": [t for t in tasks if not t["task_date"]]})


@router.post("/wip/issue")
def issue(payload: IssueInput) -> dict:
    key = f"react-wip-issue:{payload.request_id}"
    with connect() as conn:
        previous = conn.execute("SELECT * FROM wip_blank_batches WHERE batch_key=?", (key,)).fetchone()
        material = conn.execute("SELECT * FROM material_inventory_color WHERE material_key=?", (payload.material_name.casefold(),)).fetchone()
    if previous and (previous["material_name"] != payload.material_name or previous["blank_type"] != payload.blank_type
                     or previous["batch_date"] != payload.batch_date.isoformat() or abs(previous["issued_meters"] - round(payload.meters_used, 3)) > 0.0001):
        raise HTTPException(409, detail="Этот идентификатор уже использован для другой выдачи")
    if material is None:
        raise HTTPException(422, detail="Для НЗП выберите существующий материал из справочника")
    return _result(issue_wip_material(payload.batch_date, payload.material_name, payload.blank_type,
                                      payload.meters_used, payload.note, source_key=key))


@router.post("/wip/{batch_id}/complete")
def complete(batch_id: int, payload: CompleteInput) -> dict:
    batch = _batch(batch_id)
    if batch["completion_movement_id"] and (batch["produced_units"] != payload.produced_units or batch["scrap_units"] != payload.scrap_units):
        raise HTTPException(409, detail="Партия уже закрыта с другим количеством; сначала отмените закрытие")
    return _result(complete_wip_blank_batch(batch_id, payload.produced_units, payload.scrap_units, payload.note))


@router.post("/wip/{batch_id}/reverse")
def reverse(batch_id: int, payload: ReverseInput) -> dict:
    batch = _batch(batch_id)
    movement_id = batch[f"{payload.stage}_movement_id"]
    if not movement_id:
        raise HTTPException(409, detail="Операция ещё не проводилась или уже отменена")
    result = undo_inventory_movement(movement_id)
    if not result.get("ok"):
        raise HTTPException(409, detail=result.get("message", "Не удалось отменить операцию"))
    return result


@router.post("/wip/package")
def package(payload: PackageInput) -> dict:
    key = f"react-wip-package:{payload.request_id}"
    with connect() as conn:
        previous = conn.execute("SELECT * FROM inventory_movements WHERE source_task_key=? AND movement_type='production_receipt_wip' AND reversed_at IS NULL", (key,)).fetchone()
    if previous and (previous["nm_id"] != payload.nm_id or previous["quantity"] != payload.qty_sets or previous["movement_date"] != payload.packaging_date.isoformat()):
        raise HTTPException(409, detail="Этот идентификатор уже использован для другой комплектации")
    return _result(post_manual_wip_packaging(payload.nm_id, payload.qty_sets, payload.packaging_date, payload.note, source_key=key))


@router.put("/capacity")
def save_capacity(payload: CapacityInput) -> dict:
    save_production_capacity(**payload.model_dump())
    return {"capacity": _capacity()}
