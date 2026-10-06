"""Local version-checked Ozon financial drafts; no marketplace writes."""
from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from db.ozon_normalization import preview, confirm_link, documents

router = APIRouter(prefix="/api/v1/ozon-accounting/normalization", tags=["Учёт Ozon"])


class DocumentLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: date
    end_date: date
    namespace: str = Field(default="main", min_length=1, max_length=80)
    expected_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_occurrence_key: str = Field(min_length=1, max_length=150)
    document_event_ids: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(min_length=1, max_length=5000)


@router.get("/preview")
def get_preview(start_date: date, end_date: date, namespace: str = "main"):
    try:
        return preview(start_date.isoformat(), end_date.isoformat(), namespace)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/link")
def link(payload: DocumentLink, request: Request):
    if request.headers.get("x-tenant-role", "").lower() not in {"owner", "manager"}:
        raise HTTPException(403, "Связь документов доступна владельцу и менеджеру.")
    try:
        return confirm_link(payload.start_date.isoformat(), payload.end_date.isoformat(), payload.namespace,
                            payload.expected_fingerprint, payload.source_occurrence_key,
                            payload.document_event_ids, request.headers.get("x-tenant-account") or "tenant-owner-or-manager")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/documents")
def get_documents(start_date: date, end_date: date, namespace: str = "main"):
    try:
        return documents(start_date.isoformat(), end_date.isoformat(), namespace)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
