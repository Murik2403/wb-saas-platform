"""Preview and explicitly confirm tenant-local raw-material plans."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StrictInt

from db.core import connect
from db.supply_planning import build_supply_preview, confirm_supply_plan, load_supply_plan_snapshot

router = APIRouter(prefix="/api/v1/supply-planning", tags=["Планирование сырья"])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class VariantAllocation(Input):
    task_key: str = Field(min_length=1, max_length=200)
    chrt_id: Annotated[StrictInt, Field(gt=0)]
    units: FiniteFloat = Field(gt=0, le=10_000_000)


class PreviewInput(Input):
    start_date: date
    end_date: date
    variant_allocations: list[VariantAllocation] = Field(default_factory=list, max_length=1000)


class Edit(Input):
    material_key: str = Field(min_length=1, max_length=200)
    unit: str = Field(min_length=1, max_length=32)
    quantity: FiniteFloat = Field(gt=0, le=10_000_000)
    supplier_id: Annotated[StrictInt, Field(gt=0)] | None = None
    expected_date: date


class ConfirmInput(PreviewInput):
    confirmation_key: UUID
    preview_fingerprint: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")
    edits: list[Edit] = Field(min_length=1, max_length=500)


def _allocation_payload(payload: PreviewInput) -> list[dict]:
    return [a.model_dump(mode="json") for a in payload.variant_allocations]


def _read_preview(payload: PreviewInput, conn) -> dict:
    snapshot = load_supply_plan_snapshot(conn, payload.start_date.isoformat(), payload.end_date.isoformat())
    return build_supply_preview(snapshot, _allocation_payload(payload))


@router.post("/preview")
def preview(payload: PreviewInput):
    if payload.end_date < payload.start_date:
        raise HTTPException(422, detail="Начало периода позже окончания.")
    try:
        with connect() as conn:
            conn.execute("BEGIN")
            result = _read_preview(payload, conn)
        return result
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@router.post("/confirm")
def confirm(payload: ConfirmInput):
    if payload.end_date < payload.start_date:
        raise HTTPException(422, detail="Начало периода позже окончания.")
    body = payload.model_dump(mode="json")
    key = str(payload.confirmation_key)
    # The same canonical body remains replayable even if source data has since changed.
    request_hash = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    try:
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = _read_preview(payload, conn)
            result = confirm_supply_plan(conn, key, request_hash, payload.preview_fingerprint, current,
                                         [edit.model_dump(mode="json") for edit in payload.edits])
            result["order_numbers"] = [item["order_number"] for item in result.pop("orders")]
            return result
    except ValueError as exc:
        code = str(exc)
        if code in {"stale_preview", "idempotency_conflict"}:
            raise HTTPException(409, detail={"code": code, "message": "Повторите предпросмотр или используйте новый ключ подтверждения."}) from exc
        if code == "preview_blocked":
            raise HTTPException(422, detail={"code": code}) from exc
        raise HTTPException(422, detail=str(exc)) from exc
