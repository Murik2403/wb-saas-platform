"""Tenant-scoped receipt of verified first-value events."""
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

import config
from internal_auth import verify_credential
from logic import accounts, db as control_db, growth

router = APIRouter()


class FirstValueEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    event_name: Literal["wb_connected", "first_sync_succeeded", "first_useful_report", "first_production_result"]
    event_key: Literal["wb_connected", "first_sync_succeeded", "first_useful_report", "first_production_result"]
    occurred_at: datetime


@router.post("/internal/growth/event", status_code=204)
def receive_first_value(payload: FirstValueEvent, request: Request):
    if not verify_credential(config.INTERNAL_API_SECRET, payload.slug, request.headers.get("x-internal-secret", "")):
        raise HTTPException(403, "forbidden")
    if payload.slug == "demo":
        raise HTTPException(403, "demo events are excluded")
    now = datetime.now(timezone.utc)
    when = payload.occurred_at
    if when.tzinfo is None or when > now + timedelta(minutes=5) or when < now - timedelta(days=90):
        raise HTTPException(422, "invalid event time")
    if payload.event_key != payload.event_name:
        raise HTTPException(422, "event key mismatch")
    with control_db.connect() as conn:
        tenant = accounts.get_tenant_by_slug(conn, payload.slug)
        if tenant is None:
            raise HTTPException(404, "unknown tenant")
        owner = int(tenant["account_id"])
        if payload.event_name == "first_useful_report":
            growth.record_event(conn, owner, "first_report_viewed", occurred_at=when)
            growth.record_activation(conn, owner, "financial", occurred_at=when)
        elif payload.event_name == "first_production_result":
            growth.record_activation(conn, owner, "production", occurred_at=when)
        else:
            growth.record_event(conn, owner, payload.event_name, occurred_at=when)
    return Response(status_code=204)
