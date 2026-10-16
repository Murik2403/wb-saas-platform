"""JSON API for the tenant dashboard -- the frontend-migration counterpart to
app.py (Streamlit). Runs as its own process inside the same tenant container,
against the same per-tenant SQLite DB, reusing the exact same business-logic
modules (calculations.py, db/*, today_service.py, ...) app.py already uses.
See memory/project_marketshelper_frontend_migration.md for the migration plan
this is phase 1 of. Streamlit (app.py) is untouched and keeps running
independently -- this is additive, not a replacement, until the new frontend
covers all 12 sections.
"""

from __future__ import annotations

import threading
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from ads_service import compute_ads
from backup_tools import latest_backup
from control_service import (
    compute_control, cost_quality_csv, quality_csv, run_fifo_recheck,
)
from calculations import build_dashboard
from sentry_integration import initialize as initialize_sentry
from config import DB_PATH, TENANT_SLUG, get_token, load_settings
from db import init_db, last_sync, table_count
from db.manual_expenses import delete_manual_expense, read_manual_expenses, save_manual_expense
from overview_service import (
    DEFAULT_PERIOD, PERIOD_LABELS, compute_overview, previous_window, resolve_window,
)
from products_service import compute_products
from report_scheduler import REPORTS_DIR, deliver_report, generate_and_save
from reports import delivery
from reports.store import ReportStore
from reports_service import compute_reports, list_downloadable_runs, validate_new_schedule
from today_service import compute_today, json_safe
from agents_service import (
    AgentsError, compute_agents, refresh_recommendations, apply_candidate, reject_candidate
)

app = FastAPI(title="MARKETSHELPER tenant API")
initialize_sentry(service_name="tenant-api")

# Dormant legacy API: localhost development CORS remains explicit opt-in.
# Configured tenants must never grant cross-origin browser access here.
if not TENANT_SLUG and os.environ.get("MH_ENABLE_DEV_CORS", "").strip() == "1":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000", "http://127.0.0.1:3000",
            "http://localhost:3001", "http://127.0.0.1:3001",
        ],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )


class ManualExpensePayload(BaseModel):
    nm_id: int = Field(gt=0)
    article: str
    amount_rub: float = Field(ge=0)
    allocation_basis: str = "per_unit"
    source: str = "manual"
    period_start: date
    period_end: date
    note: str = ""


@app.get("/api/manual-expenses")
def get_manual_expenses(
    nm_id: int | None = Query(default=None, gt=0),
    start: date | None = None,
    end: date | None = None,
) -> dict:
    return {"items": read_manual_expenses(nm_id=nm_id, start=start, end=end)}


@app.post("/api/manual-expenses")
def post_manual_expense(payload: ManualExpensePayload) -> dict:
    try:
        expense_id = save_manual_expense(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": expense_id, "message": "Расход сохранён"}


@app.delete("/api/manual-expenses/{expense_id}")
def remove_manual_expense(expense_id: int) -> dict:
    if not delete_manual_expense(expense_id):
        raise HTTPException(status_code=404, detail="not_found")
    return {"message": "Расход удалён"}


@app.get("/healthz")
@app.get("/_stcore/health")
def healthz():
    return {"status": "ok"}


@app.on_event("startup")
def _startup() -> None:
    init_db()


def _build_ctx() -> dict:
    settings = load_settings()
    today_msk = datetime.now(ZoneInfo("Europe/Moscow")).date()
    sync_info = last_sync()
    backup_info = latest_backup()
    data = build_dashboard(today_msk, today_msk)
    return {
        "settings": settings,
        "token_exists": bool(get_token()),
        "sync_info": sync_info,
        "backup_info": backup_info,
        "period": "Сегодня",
        "start": today_msk,
        "end": today_msk,
        "data": data,
        "today_msk": today_msk,
    }


@app.get("/api/today")
def get_today() -> dict:
    if table_count("orders") == 0 and table_count("sales") == 0:
        raise HTTPException(status_code=409, detail="no_data_yet")
    ctx = _build_ctx()
    return json_safe(compute_today(ctx))


@app.get("/api/overview")
def get_overview(
    period: str = Query(DEFAULT_PERIOD, pattern="^(today|7d|30d|90d|custom)$"),
    start: date | None = None,
    end: date | None = None,
) -> dict:
    """The "Обзор" screen. `period` is the selector app.py shows above the page
    in Streamlit; `start`/`end` (ISO dates) are only read for period=custom."""
    if table_count("orders") == 0 and table_count("sales") == 0:
        raise HTTPException(status_code=409, detail="no_data_yet")

    today_msk = datetime.now(ZoneInfo("Europe/Moscow")).date()
    try:
        window_start, window_end = resolve_window(period, today_msk, start, end)
    except ValueError:
        raise HTTPException(status_code=422, detail="custom_period_requires_start_and_end")

    ctx = {
        "period": period,
        "period_label": PERIOD_LABELS.get(period, period),
        "start": window_start,
        "end": window_end,
        "data": build_dashboard(window_start, window_end),
        "today_msk": today_msk,
    }

    prev_ctx = None
    prev = previous_window(window_start, window_end)
    if prev is not None:
        prev_start, prev_end = prev
        prev_ctx = {
            "start": prev_start,
            "end": prev_end,
            "data": build_dashboard(prev_start, prev_end),
        }

    return json_safe(compute_overview(ctx, prev_ctx))


# --- Контроль (Control) ----------------------------------------------------
# Unlike every other page above, this one has mutations (FIFO recheck) and
# file downloads (quality/cost-quality CSVs). See control_service.py for the
# pure-computation layer and pages/control.py for the Streamlit source of truth.


@app.get("/api/control")
def get_control() -> dict:
    """The "Контроль" screen -- data quality KPIs, reliability table, and
    action items. Not period-scoped -- reflects current-moment state."""
    ctx = _build_ctx()
    return compute_control(ctx)


@app.post("/api/control/fifo-recheck")
def post_fifo_recheck() -> dict:
    """"Полная перепроверка FIFO": a full WB sync followed by the local sales
    FIFO recompute. Same pattern as pages/control.py's button -- can take tens
    of seconds, runs synchronously (frontend shows a loading state)."""
    try:
        message = run_fifo_recheck()
        return {"message": message}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/control/quality.csv")
def get_quality_csv() -> Response:
    """Download "Все проверки" table as CSV (UTF-8-BOM for Excel)."""
    from fastapi.responses import Response as FastAPIResponse
    ctx = _build_ctx()
    csv_bytes, filename = quality_csv(ctx)
    return FastAPIResponse(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/control/cost-quality.csv")
def get_cost_quality_csv() -> Response:
    """Download cost-coverage diagnostics as CSV (UTF-8-BOM for Excel)."""
    from fastapi.responses import Response as FastAPIResponse
    ctx = _build_ctx()
    csv_bytes, filename = cost_quality_csv(ctx)
    return FastAPIResponse(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/products")
def get_products(
    period: str = Query(DEFAULT_PERIOD, pattern="^(today|7d|30d|90d|custom)$"),
    start: date | None = None,
    end: date | None = None,
) -> dict:
    """The "Товары" screen -- per-article economics for the selected period.
    Same `period`/`start`/`end` contract as /api/overview (app.py's selector
    sits above this page in Streamlit too); no previous-period comparison,
    the page has no trend badges."""
    if table_count("orders") == 0 and table_count("sales") == 0:
        raise HTTPException(status_code=409, detail="no_data_yet")

    today_msk = datetime.now(ZoneInfo("Europe/Moscow")).date()
    try:
        window_start, window_end = resolve_window(period, today_msk, start, end)
    except ValueError:
        raise HTTPException(status_code=422, detail="custom_period_requires_start_and_end")

    ctx = {
        "period": period,
        "period_label": PERIOD_LABELS.get(period, period),
        "start": window_start,
        "end": window_end,
        "data": build_dashboard(window_start, window_end),
        "today_msk": today_msk,
    }
    return json_safe(compute_products(ctx))


@app.get("/api/ads")
def get_ads(
    period: str = Query(DEFAULT_PERIOD, pattern="^(today|7d|30d|90d|custom)$"),
    start: date | None = None,
    end: date | None = None,
) -> dict:
    """The "Реклама" screen -- ad KPIs + per-campaign stats for the selected
    period. Same `period`/`start`/`end` contract as /api/overview; no
    previous-period comparison, pages/ads.py draws no trend badges."""
    if table_count("orders") == 0 and table_count("sales") == 0:
        raise HTTPException(status_code=409, detail="no_data_yet")

    today_msk = datetime.now(ZoneInfo("Europe/Moscow")).date()
    try:
        window_start, window_end = resolve_window(period, today_msk, start, end)
    except ValueError:
        raise HTTPException(status_code=422, detail="custom_period_requires_start_and_end")

    ctx = {
        "period": period,
        "period_label": PERIOD_LABELS.get(period, period),
        "start": window_start,
        "end": window_end,
        "data": build_dashboard(window_start, window_end),
        "today_msk": today_msk,
    }
    return json_safe(compute_ads(ctx))


# --- Отчёты (Reports) ---------------------------------------------------
# Unlike every other page above, this one has mutations (create/delete a
# schedule, generate on demand, request a Telegram link code) and streams a
# PDF back for downloads -- see pages/reports_page.py and
# report_scheduler.generate_and_save/deliver_report for the Streamlit
# counterparts. Kept in this file, not a separate router module, because
# splitting a 6-endpoint page into its own module is more indirection than it
# earns; if a second CRUD page arrives, revisit.


class NewSchedulePayload(BaseModel):
    name: str
    metrics: list[str]
    schedule_type: str = Field(pattern="^(daily|weekly|monthly)$")
    schedule_time: str = Field(pattern="^\\d{2}:\\d{2}$")  # HH:MM, МСК-naive
    schedule_weekday: int | None = None
    schedule_day: int | None = None
    email_enabled: bool = False
    telegram_enabled: bool = False


@app.get("/api/reports")
def get_reports() -> dict:
    """Read model for the Reports screen -- Telegram link status (cached 30s
    like the Streamlit page's @st.cache_data ttl), schedule definitions,
    metric options, delivery configuration flags, and the last 20 successful
    runs with a downloadable PDF still on disk."""
    return compute_reports(ReportStore(DB_PATH))


@app.post("/api/reports")
def create_report(payload: NewSchedulePayload) -> dict:
    """Same validation pages/reports_page.py's form does inline (see
    reports_service.validate_new_schedule for the message copy)."""
    error = validate_new_schedule(
        name=payload.name, metrics=payload.metrics, schedule_type=payload.schedule_type,
        schedule_time=payload.schedule_time, schedule_weekday=payload.schedule_weekday,
        schedule_day=payload.schedule_day,
    )
    if error is not None:
        raise HTTPException(status_code=422, detail=error)

    store = ReportStore(DB_PATH)
    definition_id = store.add_definition(
        name=payload.name.strip(),
        metrics=payload.metrics,
        schedule_type=payload.schedule_type,
        schedule_time=payload.schedule_time,
        schedule_weekday=payload.schedule_weekday if payload.schedule_type == "weekly" else None,
        schedule_day=payload.schedule_day if payload.schedule_type == "monthly" else None,
        email_enabled=payload.email_enabled,
        telegram_enabled=payload.telegram_enabled,
    )
    return {"id": definition_id, "message": "Расписание сохранено"}


@app.delete("/api/reports/{definition_id}")
def delete_report(definition_id: int) -> dict:
    store = ReportStore(DB_PATH)
    if not any(d["id"] == definition_id for d in store.list_definitions()):
        raise HTTPException(status_code=404, detail="not_found")
    store.delete_definition(definition_id)
    return {"message": "Расписание удалено"}


@app.post("/api/reports/{definition_id}/generate")
def generate_report(definition_id: int) -> dict:
    """Same fire-and-forget delivery pattern pages/reports_page.py's
    "Сгенерировать сейчас" button uses -- deliver_report runs in a background
    thread so the HTTP response doesn't wait on Telegram's intermittently-slow
    network path (see reports/delivery.py). The PDF is already saved and
    downloadable regardless of whether delivery succeeds."""
    store = ReportStore(DB_PATH)
    definition = next((d for d in store.list_definitions() if d["id"] == definition_id), None)
    if definition is None:
        raise HTTPException(status_code=404, detail="not_found")

    from reports.pdf_budget import PdfBusy, interactive_pdf
    try:  # a request must not sit behind other tenants' reports for long (15 s) nor outlive the proxy timeout: ask the user to retry
        pdf_bytes, file_path = interactive_pdf(lambda: generate_and_save(store, definition), wait=15)
    except PdfBusy as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "30"}) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Не удалось сгенерировать: {exc}") from exc

    if definition.get("email_enabled") or definition.get("telegram_enabled"):
        threading.Thread(
            target=deliver_report, args=(definition, pdf_bytes, file_path.name), daemon=True,
        ).start()
        message = f"Отчёт «{definition['name']}» сгенерирован. Доставка на email/в Telegram идёт в фоне."
    else:
        message = f"Отчёт «{definition['name']}» сгенерирован"
    return {"message": message}


@app.post("/api/reports/telegram/link-code")
def request_telegram_link_code() -> dict:
    result = delivery.request_telegram_link_code()
    if result is None:
        raise HTTPException(status_code=502, detail="Не удалось получить код привязки")
    code, ttl_minutes = result
    return {"code": code, "ttl_minutes": ttl_minutes}


@app.get("/api/reports/runs/{run_id}/download")
def download_report(run_id: int) -> FileResponse:
    """Streams a generated PDF back for a run in the Reports screen's
    "Готовые файлы" list. Validates the run belongs to this tenant's store
    AND lives under REPORTS_DIR (guard against a definition_id being edited
    server-side to point at an arbitrary path -- reports are stored under
    reports/<definition_id>/<timestamp>.pdf, nothing outside REPORTS_DIR is
    ever a legitimate report file)."""
    store = ReportStore(DB_PATH)
    definitions = store.list_definitions()
    runs = list_downloadable_runs(store, definitions)
    run = next((r for r in runs if r["id"] == run_id), None)
    if run is None:
        raise HTTPException(status_code=404, detail="not_found")

    path = Path(run["file_path"]).resolve()
    try:
        path.relative_to(REPORTS_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="not_found") from exc

    return FileResponse(path, media_type="application/pdf", filename=path.name)


# --- Агенты (Agents) -------------------------------------------------------
# Pages/agents_page.py's three actions (refresh, apply, reject) wrapped as a
# REST surface the Next.js frontend consumes.  The read route carries the full
# page snapshot so the frontend makes one request on mount.  Two safety notes:
#   1. /apply does a REAL marketplace write (dry_run=False) — the frontend
#      shows a confirmation dialog before firing it (a safety improvement over
#      the Streamlit page, which has no confirm step).
#   2. /refresh hits the live WB/Ozon APIs (read-only price/campaign fetches
#      that write new agent_candidates rows locally).
# During automated testing we never fire /refresh or /apply; only the GET and
# code-review are safe.


@app.get("/api/agents")
def get_agents() -> dict:
    return compute_agents()


@app.post("/api/agents/refresh")
def post_agents_refresh() -> dict:
    try:
        return refresh_recommendations()
    except AgentsError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@app.post("/api/agents/candidates/{candidate_id}/apply")
def post_agents_apply(candidate_id: int) -> dict:
    try:
        return apply_candidate(candidate_id)
    except AgentsError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@app.post("/api/agents/candidates/{candidate_id}/reject")
def post_agents_reject(candidate_id: int) -> dict:
    try:
        return reject_candidate(candidate_id)
    except AgentsError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
