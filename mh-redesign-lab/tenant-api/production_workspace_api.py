"""React access to the tenant's existing WIP ledger and production capacity.

Mount on the main app to inherit its authentication and demo-write middleware.
This module never initializes a database or starts background jobs.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from db.core import connect
from db.production import get_production_capacity, save_production_capacity
from db.inventory_movements import undo_inventory_movement
from db.wip import (issue_wip_material, complete_wip_blank_batch,
                    post_manual_wip_packaging, read_wip_blank_batches,
                    read_wip_blank_summary, read_wip_blank_allocations, wip_module_status,
                    validate_catalog_identity)
from growth_activation import record_growth_event
from growth_delivery import deliver_pending_growth_events
from today_service import json_safe
import config

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
    nm_id: PositiveInt
    chrt_id: NonnegativeInt
    task_key: str | None = Field(default=None, min_length=1, max_length=200)
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
    chrt_id: NonnegativeInt
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


def _record_first_production_result() -> None:
    """Queue the first finished-goods result without affecting inventory writes."""
    try:
        record_growth_event(
            config.DB_PATH,
            event_name="first_production_result",
            occurred_at=datetime.now(timezone.utc).isoformat(),
        )
        try:
            deliver_pending_growth_events(config.DB_PATH)
        except Exception:
            # The durable outbox is retried on later delivery attempts.
            pass
    except Exception:
        # Growth tracking must never fail an already committed stock receipt.
        return


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
def workspace(request: Request, start: date | None = None, days: int = Query(14, ge=1, le=90)) -> dict:
    start = start or date.today()
    end = start + timedelta(days=days - 1)
    capacity = _capacity()
    with connect() as conn:
        materials = [dict(row) for row in conn.execute(
            "SELECT *, full_rolls*roll_length+partial_meters AS available_meters "
            "FROM material_inventory_color ORDER BY material_name")]
        products = [dict(row) for row in conn.execute(
            "SELECT p.nm_id,p.supplier_article,p.material_name,p.blank_type,p.pack_size,"
            "COALESCE(c.product_name,'') AS product_name,COALESCE(c.raw_json,'{}') AS raw_json,c.nm_id AS catalog_nm_id FROM production_settings p "
            "LEFT JOIN products_catalog c ON c.nm_id=p.nm_id WHERE p.enabled=1 ORDER BY p.nm_id")]
        tasks = [dict(row) for row in conn.execute(
            "SELECT t.*,p.pack_size FROM execution_tasks t LEFT JOIN production_settings p ON p.nm_id=t.nm_id "
            "WHERE LOWER(TRIM(t.task_type)) IN ('production','производство') "
            "AND (t.task_date BETWEEN ? AND ? OR t.task_date IS NULL OR t.task_date='') "
            "ORDER BY t.task_date,t.task_key", (start.isoformat(), end.isoformat()))]
        products = [dict(p) for p in products]
    product_options = []
    for product in products:
        if product.get("catalog_nm_id") is None:
            continue
        try:
            raw = json.loads(product.get("raw_json") or "")
        except (TypeError, ValueError):
            continue
        sizes = raw.get("sizes") if isinstance(raw, dict) else None
        if not isinstance(sizes, list):
            continue
        variants = []
        valid_sizes = True
        for size in sizes:
            if not isinstance(size, dict):
                valid_sizes = False
                break
            try:
                chrt = int(size.get("chrtID", size.get("chrtId", size.get("chrt_id", 0))) or 0)
            except (TypeError, ValueError):
                valid_sizes = False
                break
            if chrt <= 0:
                valid_sizes = False
                break
            variants.append({"chrt_id": chrt, "variant_label": str(size.get("techSize") or size.get("wbSize") or "").strip()})
        if not valid_sizes:
            continue
        if not variants:
            variants = [{"chrt_id": 0, "variant_label": "Базовый товар"}]
        for variant in variants:
            product_options.append({**{k: v for k, v in product.items() if k not in {"raw_json", "catalog_nm_id"}}, **variant})
    products = product_options
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
    product_labels = {(int(p["nm_id"]), int(p["chrt_id"])): f"{p.get('product_name') or p.get('supplier_article') or p['nm_id']} · {p.get('variant_label') or 'базовый'}"
                      for p in products}
    batches = [{**b, "unit": units.get(b["material_key"], "неизвестно"),
                "product_label": (product_labels.get((int(b["nm_id"] or 0), int(b["chrt_id"] or 0)))
                                  or (f"Товар {int(b['nm_id'])} · вариант #{int(b['chrt_id'])}" if int(b["nm_id"] or 0) > 0 else "Старые данные без привязки"))}
               for b in read_wip_blank_batches().to_dict("records")]
    summary = [{**b, "unit": units.get(b["material_key"], "неизвестно")} for b in read_wip_blank_summary().to_dict("records")]
    return json_safe({"read_only": bool(config.IS_DEMO) or request.headers.get("x-tenant-role", "").lower() == "viewer",
                      "wip": {"status": wip_module_status(), "batches": batches,
                               "summary": summary, "allocations": read_wip_blank_allocations(100).to_dict("records")},
                      "materials": materials, "products": products, "capacity": capacity, "calendar": calendar,
                      "undated_tasks": [t for t in tasks if not t["task_date"]]})


@router.post("/wip/issue")
def issue(payload: IssueInput) -> dict:
    key = f"react-wip-issue:{payload.request_id}"
    with connect() as conn:
        previous = conn.execute("SELECT * FROM wip_blank_batches WHERE batch_key=?", (key,)).fetchone()
        if previous:
            if (str(previous["material_name"]) != payload.material_name or str(previous["blank_type"]) != payload.blank_type
                    or str(previous["batch_date"]) != payload.batch_date.isoformat()
                    or abs(float(previous["issued_meters"] or 0) - round(payload.meters_used, 3)) > 0.0001
                    or int(previous["nm_id"] or 0) != payload.nm_id or int(previous["chrt_id"] or 0) != payload.chrt_id):
                raise HTTPException(409, detail="Этот идентификатор уже использован для другой выдачи")
            return json_safe({"posted": 0, "skipped": 1, "errors": [], "meters": 0.0, "cost_rub": 0.0, "batch_id": int(previous["id"])})
        material = conn.execute("SELECT * FROM material_inventory_color WHERE material_key=?", (payload.material_name.casefold(),)).fetchone()
        setting = conn.execute("SELECT 1 FROM production_settings WHERE nm_id=? AND enabled=1", (payload.nm_id,)).fetchone()
        try:
            identity = validate_catalog_identity(conn, payload.nm_id, payload.chrt_id, require_catalog=True)
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        if payload.task_key:
            task = conn.execute("SELECT nm_id,chrt_id FROM execution_tasks WHERE task_key=?", (payload.task_key,)).fetchone()
            if task is None:
                raise HTTPException(422, detail="Задание производства не найдено.")
            if (int(task["nm_id"] or 0), int(task["chrt_id"] or 0)) != (payload.nm_id, payload.chrt_id):
                raise HTTPException(409, detail="Товар или вариант не совпадает с сохранённым заданием.")
    if material is None:
        raise HTTPException(422, detail="Для НЗП выберите существующий материал из справочника")
    if setting is None:
        raise HTTPException(422, detail="Товар не включён в собственное производство.")
    return _result(issue_wip_material(payload.batch_date, payload.material_name, payload.blank_type,
                                      payload.meters_used, payload.note, source_key=key,
                                      nm_id=identity["nm_id"], chrt_id=identity["chrt_id"]))


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
        try:
            if previous is None:
                validate_catalog_identity(conn, payload.nm_id, payload.chrt_id, require_catalog=True)
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc
    if previous and (previous["nm_id"] != payload.nm_id or previous["chrt_id"] != payload.chrt_id or previous["quantity"] != payload.qty_sets or previous["movement_date"] != payload.packaging_date.isoformat()):
        raise HTTPException(409, detail="Этот идентификатор уже использован для другой комплектации")
    result = _result(post_manual_wip_packaging(
        payload.nm_id, payload.qty_sets, payload.packaging_date, payload.note,
        source_key=key, chrt_id=payload.chrt_id,
    ))
    if result.get("posted") or result.get("skipped"):
        _record_first_production_result()
    return result


@router.put("/capacity")
def save_capacity(payload: CapacityInput) -> dict:
    save_production_capacity(**payload.model_dump())
    return {"capacity": _capacity()}
