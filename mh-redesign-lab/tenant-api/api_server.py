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

from datetime import date, datetime, timedelta
import json
import os
from contextlib import asynccontextmanager
import sqlite3
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, FiniteFloat

from ads_service import compute_ads
from backup_tools import create_backup, latest_backup, list_backups
from calculations import build_dashboard
from config import DB_PATH, IS_DEMO, get_ozon_credentials, get_token, load_settings, save_settings
from db import (
    apply_finished_goods_reconciliation,
    close_production_shift,
    fifo_reconciliation_status,
    finished_goods_fifo_guard_status,
    get_production_capacity,
    init_db,
    last_sync,
    preview_finished_goods_reconciliation,
    read_fifo_reconciliation_lines,
    read_fifo_reconciliation_runs,
    read_procurement_orders,
    read_procurement_items,
    read_procurement_movements,
    read_procurement_payments,
    record_procurement_payment,
    read_suppliers,
    save_supplier,
    save_material_inventory,
    initialize_material_fifo,
    read_material_fifo_summary,
    get_fifo_opening_rate,
    set_fifo_opening_rate,
    read_table,
    table_count,
    wb_fifo_reconciliation_context,
    update_procurement_order,
    save_costs,
)
from overview_service import (
    DEFAULT_PERIOD, PERIOD_LABELS, compute_overview, previous_window, resolve_window,
)
from products_service import compute_products
from today_service import compute_today, json_safe
from ui_helpers import build_data_quality_overview
from reports.store import ReportStore
from reports.metrics import METRIC_LABELS
from reports import delivery
from db.procurement import create_procurement_order, post_procurement_receipt
from db.production import save_execution_tasks
from db.wb_incidents import apply_wb_incident_loss, create_wb_incident_case, read_wb_incident_cases, record_wb_incident_compensation, wb_incident_financial_summary
from reports.pdf_builder import build_report_pdf
from agents.store import AgentStore
from agents import ad_agent, price_agent
from agents.marketplaces.wb_client import WBAgentClient
from agents.marketplaces.ozon_client import OzonAgentClient
from agents.rules import AdRules, PriceRules
from agent_sync import wb_cost_lookup
from dashboard_cache import DashboardResponseCache
from sync import sync_all, sync_ads_only
from sync_lock import is_sync_running, sync_lock
from sync_runner import SyncRunner
from request_security import write_origin_allowed
from action_plan_executor import ActionPlanExecutor


class ActionPlanCreate(BaseModel):
    source: str = "dashboard"
    source_action_id: str | None = None
    title: str
    sku: str = ""
    due_at: str | None = None


class ActionPlanPatch(BaseModel):
    status: str | None = None
    assignee: str | None = None
    due_at: str | None = None
    priority: str | None = None


class QuickReportInput(BaseModel):
    name: str = "Операционный отчёт"
    days: int = 30
    metrics: list[str] | None = None


class ProcurementItemInput(BaseModel):
    nm_id: int = 0
    supplier_article: str = ""
    product_name: str = ""
    quantity: FiniteFloat
    unit_price: FiniteFloat = 0
    note: str = ""


class ProcurementCreateInput(BaseModel):
    supplier_name: str
    procurement_type: str = "Товар"
    status: str = "Запланировано"
    order_date: date | None = None
    payment_due_date: date | None = None
    expected_date: date | None = None
    currency: str = "RUB"
    exchange_rate: FiniteFloat = 1.0
    note: str = "Создано из нового dashboard"
    items: list[ProcurementItemInput]


class ProductionTaskInput(BaseModel):
    task_key: str
    task_type: str = "Производство"
    task_date: date | None = None
    stage: str = ""
    nm_id: int = 0
    supplier_article: str = ""
    product_name: str = ""
    planned_units: int
    status: str = "Не начато"
    note: str = ""


class FifoReconciliationApplyInput(BaseModel):
    confirmed: bool = False
    note: str = ""


class SettingsUpdateInput(BaseModel):
    sync_interval_minutes: int | None = None
    initial_history_days: int | None = None
    agent_interval_minutes: int | None = None


class ProductCostInput(BaseModel):
    nm_id: int
    supplier_article: str = ""
    cost_per_wb_unit: FiniteFloat
    note: str = ""


class ProductCostsUpdateInput(BaseModel):
    items: list[ProductCostInput]


class ProductionNormInput(BaseModel):
    nm_id: int
    material_per_unit: FiniteFloat
    min_batch: int
    pack_size: int
    enabled: bool = True


class ProductionNormsUpdateInput(BaseModel):
    items: list[ProductionNormInput]


class MaterialInventoryInput(BaseModel):
    material_name: str
    balance_known: bool = False
    unit: str = "м"
    tracking_mode: str = "packaged"
    full_rolls: int = 0
    partial_meters: FiniteFloat = 0
    roll_length: FiniteFloat = 25.5
    opening_rate_rub: FiniteFloat = 0
    note: str = ""


class MaterialInventoryUpdateInput(BaseModel):
    items: list[MaterialInventoryInput]


class MaterialFifoInitializeInput(BaseModel):
    opening_rate_rub_m: FiniteFloat | None = None


class ProcurementStatusInput(BaseModel):
    status: str


class ProcurementPaymentInput(BaseModel):
    amount: FiniteFloat
    payment_date: date | None = None
    method: str = "Банк"
    note: str = ""


class SupplierInput(BaseModel):
    id: int | None = None
    name: str
    contact_person: str = ""
    phone: str = ""
    messenger: str = ""
    email: str = ""
    country: str = ""
    default_currency: str = "RUB"
    payment_terms_days: int = 3
    lead_time_days: int = 7
    active: bool = True
    note: str = ""


class ReportDefinitionInput(BaseModel):
    name: str
    metrics: list[str]
    schedule_type: str
    schedule_time: str
    schedule_weekday: int | None = None
    schedule_day: int | None = None
    email_enabled: bool = False
    telegram_enabled: bool = False


class ProductionShiftInput(BaseModel):
    tasks: list[ProductionTaskInput]


class ProductionExecutionTaskInput(BaseModel):
    task_key: str
    task_type: str = "production"
    task_date: date | None = None
    stage: str = ""
    nm_id: int = 0
    supplier_article: str = ""
    product_name: str = ""
    planned_units: int = 0
    actual_units: int = 0
    status: str = "Не начато"
    note: str = ""


class ProductionExecutionInput(BaseModel):
    tasks: list[ProductionExecutionTaskInput]


class IncidentCaseInput(BaseModel):
    incident_name: str
    incident_date: date
    warehouse_label: str = ""
    document_ref: str = ""
    note: str = ""


class IncidentCompensationInput(BaseModel):
    amount_rub: FiniteFloat
    compensation_date: date
    source_ref: str
    note: str = ""


class IncidentLossInput(BaseModel):
    nm_id: int
    units: int
    note: str = ""
    confirmed: bool = False


class ReceiptItemInput(BaseModel):
    item_id: int = 0
    receive_now: FiniteFloat


class ReceiptInput(BaseModel):
    items: list[ReceiptItemInput]


class AgentDecisionInput(BaseModel):
    outcome: str


def _init_redesign_schema() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS redesign_action_plan (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL DEFAULT 'dashboard',
                source_action_id TEXT,
                title TEXT NOT NULL,
                sku TEXT NOT NULL DEFAULT '',
                due_at TEXT,
                status TEXT NOT NULL DEFAULT 'created',
                assignee TEXT,
                priority TEXT NOT NULL DEFAULT 'medium',
                history TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(redesign_action_plan)").fetchall()}
        for name, definition in (
            ("executor", "TEXT"), ("result", "TEXT"), ("started_at", "TEXT"), ("completed_at", "TEXT"),
        ):
            if name not in columns:
                conn.execute(f"ALTER TABLE redesign_action_plan ADD COLUMN {name} {definition}")
        # Older versions placed the FIFO shortcut into the AI queue as the
        # meaningless task "Задача: SKU". FIFO is opened directly in the UI;
        # mark that legacy record completed so it cannot remain a false alert.
        conn.execute(
            """UPDATE redesign_action_plan
               SET status='done', result='Задача перенесена в прямое открытие FIFO-сверки.',
                   completed_at=COALESCE(completed_at, updated_at), updated_at=updated_at
             WHERE source_action_id='Задача:SKU' AND title='Задача' AND sku='SKU' AND status='blocked'"""
        )


def _plan_row(row: sqlite3.Row) -> dict:
    item = dict(row)
    try:
        item["history"] = json.loads(item.get("history") or "[]")
    except (TypeError, ValueError):
        item["history"] = []
    return item


def _inspect_campaign_for_plan(campaign_id: str) -> dict | None:
    """Return saved analytics for a campaign; this does not call or alter WB."""
    today_msk = datetime.now(ZoneInfo("Europe/Moscow")).date()
    start, end = resolve_window(DEFAULT_PERIOD, today_msk, None, None)
    payload = compute_ads({
        "period": DEFAULT_PERIOD,
        "period_label": PERIOD_LABELS[DEFAULT_PERIOD],
        "start": start,
        "end": end,
        "data": build_dashboard(start, end),
        "today_msk": today_msk,
    })
    for campaign in payload.get("campaigns", []):
        campaign_value = str(campaign.get("campaign", ""))
        if campaign_id in re.findall(r"\d+", campaign_value):
            return {
                "spend": campaign.get("spend", "—"),
                "drr": campaign.get("drr", "—"),
                "revenue": campaign.get("ad_revenue", "—"),
                "orders": campaign.get("orders", "—"),
            }
    return None


def _execute_plan_operation(title: str, sku: str) -> tuple[str, str] | None:
    """Execute plan actions whose backend operation has no extra form input."""
    normalized = f"{title} {sku}".lower()
    if "копи" in normalized or "резервн" in normalized:
        try:
            path = create_backup(kind="manual")
        except (OSError, RuntimeError) as exc:
            return "blocked", f"Резервная копия не создана: {exc}"
        return "done", f"Резервная копия {path.name} создана и проверена."
    if "fifo" in normalized:
        # Opening the FIFO dialog is a read-only operation. The actual apply
        # remains behind its explicit confirmation button in the dashboard.
        return "done", "Сверка FIFO открыта для проверки; проведение требует подтверждения."
    return None


def _execute_action_plan_item(item_id: int) -> dict:
    _init_redesign_schema()
    return ActionPlanExecutor(DB_PATH, _inspect_campaign_for_plan, _execute_plan_operation).run(item_id)


def _execute_pending_action_plan_items() -> None:
    """Resume tasks that were queued while the tenant API was offline."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id FROM redesign_action_plan WHERE status='created' ORDER BY created_at ASC"
        ).fetchall()
    for row in rows:
        try:
            _execute_action_plan_item(int(row[0]))
        except Exception:
            # A malformed historical task must not prevent this tenant from starting.
            continue


def _records(frame) -> list[dict]:
    """Serialize a DataFrame without leaking pandas/numpy objects to JSON."""
    if frame is None or frame.empty:
        return []
    return json_safe(frame.to_dict(orient="records"))


def _unique_agent_candidates(rows: list[dict]) -> list[dict]:
    """Keep the newest pending proposal for each actionable target."""
    unique: dict[tuple[str, str, str, str], dict] = {}
    for row in rows:
        key = (str(row.get("marketplace", "")), str(row.get("agent", "")),
               str(row.get("target_id", "")), str(row.get("action", "")))
        unique.setdefault(key, row)
    return list(unique.values())


def _close_stale_agent_duplicates(store: AgentStore, candidate: dict) -> None:
    key = (candidate.get("marketplace"), candidate.get("agent"),
           candidate.get("target_id"), candidate.get("action"))
    for row in store.list_candidates(status="pending"):
        row_key = (row.get("marketplace"), row.get("agent"), row.get("target_id"), row.get("action"))
        if int(row.get("id", 0)) != int(candidate.get("id", 0)) and row_key == key:
            store.record_decision(int(row["id"]), decided_by="system", outcome="rejected")


def _today_dashboard() -> tuple[dict, object]:
    ctx = _build_ctx()
    return compute_today(ctx), ctx["data"]


def _revenue_trend_days(days: int = 7) -> list[dict]:
    """Expose the already-calculated daily revenue series for the Today chart."""
    end = datetime.now(ZoneInfo("Europe/Moscow")).date()
    start = end - timedelta(days=max(1, int(days)) - 1)
    daily = build_dashboard(start, end).daily
    if daily is None or daily.empty:
        return []
    rows: list[dict] = []
    for _, row in daily.iterrows():
        value = pd.to_numeric(row.get("revenue", 0), errors="coerce")
        rows.append({
            "date": str(row.get("day", "")),
            "revenue_rub": round(float(value) if pd.notna(value) else 0.0, 2),
        })
    return rows


def _revenue_trend_hours(data) -> list[dict]:
    """Expose the current day's hourly revenue series for the Today chart."""
    hourly = getattr(data, "hourly", None)
    if hourly is None or hourly.empty:
        return []
    rows: list[dict] = []
    for _, row in hourly.iterrows():
        value = pd.to_numeric(row.get("revenue", 0), errors="coerce")
        rows.append({
            "hour": int(row.get("hour", len(rows)) or 0),
            "revenue_rub": round(float(value) if pd.notna(value) else 0.0, 2),
        })
    return rows


def _product_inventory(data, *, prioritize_known_stock: bool = False) -> list[dict]:
    if data.products.empty:
        return []
    products = data.products
    if prioritize_known_stock:
        # The Today endpoint uses a one-day calculation for its KPIs. Keep
        # that request fast, but put SKUs with an actual WB quantity first so
        # the dashboard does not look empty while a forecast is unavailable.
        products = products.copy()
        products["_known_stock"] = pd.to_numeric(products.get("Остаток"), errors="coerce").notna()
        products["_stock_sort"] = pd.to_numeric(products.get("Остаток"), errors="coerce").fillna(-1)
        products = products.sort_values(["_known_stock", "_stock_sort"], ascending=[False, False], kind="stable")
    rows: list[dict] = []
    for _, product in products.head(12).iterrows():
        quantity = pd.to_numeric(product.get("Остаток"), errors="coerce")
        stock_days = pd.to_numeric(product.get("Запас, дней"), errors="coerce")
        if pd.isna(stock_days):
            signal, tone = ("Нет данных об остатке" if pd.isna(quantity) else "Недостаточно продаж для прогноза"), "info"
            stock_value: float | None = None
        elif float(stock_days) <= 3:
            signal, tone = "Критичный запас", "danger"
            stock_value = round(float(stock_days), 1)
        elif float(stock_days) <= 7:
            signal, tone = "Запас под контролем", "warn"
            stock_value = round(float(stock_days), 1)
        else:
            signal, tone = "Запас в норме", "good"
            stock_value = round(float(stock_days), 1)
        rows.append({
            "sku": str(product.get("Артикул продавца", "") or "SKU"),
            "name": str(product.get("Товар", "") or "Товар без названия"),
            "stock_days": stock_value,
            "stock": None if pd.isna(quantity) else float(quantity),
            "signal": signal,
            "tone": tone,
        })
    return rows


def _stock_coverage(data) -> list[dict]:
    """Expose the products with the shortest known WB coverage."""
    if data.products.empty:
        return []
    rows: list[dict] = []
    candidates = data.products.copy()
    candidates["_days"] = pd.to_numeric(candidates.get("Запас, дней"), errors="coerce")
    candidates = candidates.dropna(subset=["_days"]).sort_values("_days").head(4)
    for _, product in candidates.iterrows():
        days = float(product["_days"])
        stock = float(pd.to_numeric(product.get("Остаток", 0), errors="coerce") or 0)
        tone = "danger" if days <= 3 else "warn" if days <= 7 else "good"
        rows.append({
            "label": str(product.get("Артикул продавца") or "SKU"),
            "detail": str(product.get("Товар") or "Товар WB"),
            "value": f"{stock:.0f} шт. · {days:.1f} дн.",
            "width": min(100, max(4, round(days / 21 * 100))),
            "tone": tone,
        })
    return rows


def _production_panels() -> tuple[list[dict], list[dict]]:
    """Summarise configured production settings and physical material balances."""
    settings = read_table("production_settings")
    materials = read_table("material_inventory_color")
    enabled = int(pd.to_numeric(settings.get("enabled", 0), errors="coerce").fillna(0).astype(int).sum()) if not settings.empty else 0
    known = materials[pd.to_numeric(materials.get("balance_known", 0), errors="coerce").fillna(0).astype(int) == 1] if not materials.empty else materials
    stages = [
        {"label": "Нормы", "value": f"{enabled} SKU настроено", "status": "done" if enabled else "pending"},
        {"label": "Сырьё", "value": f"{len(known)}/{len(materials)} цветов учтено", "status": "done" if len(known) == len(materials) and len(materials) else "active"},
        {"label": "Смена", "value": "нет открытых заданий", "status": "pending"},
    ]
    material_rows: list[dict] = []
    for _, row in known.iterrows():
        rolls = float(pd.to_numeric(row.get("full_rolls", 0), errors="coerce") or 0)
        length = float(pd.to_numeric(row.get("roll_length", 0), errors="coerce") or 0)
        partial = float(pd.to_numeric(row.get("partial_meters", 0), errors="coerce") or 0)
        meters = rolls * length + partial
        material_rows.append({
            "name": str(row.get("material_name") or row.get("material_key") or "Материал"),
            "need": f"{rolls:.0f} рул. по {length:.1f} м",
            "stock": f"{meters:.1f} м",
            "status": "остаток подтверждён",
            "tone": "good",
        })
    return stages, material_rows


def _today_metrics(today: dict) -> list[dict]:
    return [
        {
            "key": item.get("key"),
            "label": item.get("label"),
            "value": item.get("value"),
            "note": item.get("note", ""),
            "tone": item.get("tone", "default"),
        }
        for item in today.get("kpis", [])
    ]

from local_jobs import LocalJobs
local_jobs = LocalJobs()


@asynccontextmanager
async def lifespan(app):
    with sync_lock(DB_PATH.with_suffix(".api.lock")):
        _startup()
        if os.environ.get("MH_LOCAL_JOBS") == "1" and not IS_DEMO:
            local_jobs.start()
        try:
            yield
        finally:
            local_jobs.stop()


app = FastAPI(title="MARKETSHELPER tenant API", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[host.strip() for host in os.environ.get("MH_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if host.strip()])
dashboard_response_cache = DashboardResponseCache()
context_response_cache = DashboardResponseCache()
sync_runner = SyncRunner()


@app.middleware("http")
async def invalidate_after_write(request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        headers = getattr(request, "headers", {})
        if not write_origin_allowed(headers, headers.get("host", ""), getattr(getattr(request, "url", None), "scheme", "http")):
            return JSONResponse(status_code=403, content={"detail": "Запрос с постороннего сайта запрещён"})
    if IS_DEMO and request.method not in {"GET", "HEAD", "OPTIONS"}:
        return JSONResponse(status_code=403, content={"detail": "Демо доступно только для просмотра"})
    response = await call_next(request)
    if request.method in {"POST", "PATCH", "PUT", "DELETE"} and response.status_code < 400:
        dashboard_response_cache.clear()
        context_response_cache.clear()
    return response

# Local-dev only: the Next.js dev server runs on a different port -- 3000 by
# default, but whatever's free (see .claude/launch.json; this machine already
# has something else on 3000, so the frontend runs on 3001). In production
# this API sits behind the same Traefik/tenant-subdomain setup as the rest of
# the stack (see gateway/provisioning.py) and does not need a wildcard CORS
# policy -- tighten this before that cutover.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:3001", "http://127.0.0.1:3001",
    ],
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["*"],
)


def _startup() -> None:
    init_db()
    _init_redesign_schema()
    _execute_pending_action_plan_items()


@app.get("/api/v1/health")
def health():
    return {"service": "marketshelper-tenant", "version": 2}


@app.get("/api/v1/session")
def get_dashboard_session() -> dict:
    """Return the lightweight identity used by the dashboard shell.

    Authentication is handled by the gateway/Traefik middleware before a
    request reaches the tenant container. The tenant API still needs this
    route so the frontend identity lookup does not become a missing page.
    """
    slug = os.getenv("WB_SAAS_TENANT_SLUG", "marketshelper-live")
    tenant_name = os.getenv("WB_SAAS_TENANT_NAME", slug)
    return {
        "user": {"id": slug, "name": "Владелец", "role": "owner"},
        "tenant": {"id": slug, "name": tenant_name},
        "mode": "tenant",
    }


@app.get("/api/v1/jobs")
def jobs_status():
    return {"enabled": os.environ.get("MH_LOCAL_JOBS") == "1" and not IS_DEMO, "jobs": local_jobs.status()}


def _build_ctx() -> dict:
    from copy import deepcopy
    today = datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
    return deepcopy(context_response_cache.get_or_build(today, _build_ctx_uncached))


def _build_ctx_uncached() -> dict:
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


def _dashboard_response(page: str, builder):
    today = datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
    return dashboard_response_cache.get_or_build(f"{page}:{today}", builder)


def _run_tenant_sync() -> None:
    try:
        sync_all()
    finally:
        dashboard_response_cache.clear()
        context_response_cache.clear()


@app.post("/api/v1/sync")
def start_tenant_sync() -> dict:
    """Start the existing WB import once; concurrent imports are unsafe."""
    if not get_token():
        raise HTTPException(status_code=409, detail="WB token не подключён")
    if is_sync_running():
        raise HTTPException(status_code=409, detail="Синхронизация уже выполняется")
    if not sync_runner.start(_run_tenant_sync):
        raise HTTPException(status_code=409, detail="Синхронизация уже выполняется")
    return {"status": "started"}


@app.get("/api/v1/sync/status")
def get_tenant_sync_status() -> dict:
    record = last_sync() or {}
    status = "running" if sync_runner.is_running or is_sync_running() else str(record.get("status") or "idle")
    return json_safe({"status": status, "started_at": record.get("started_at"), "finished_at": record.get("finished_at"), "details": record.get("details")})


@app.post("/api/v1/sync/ads")
def start_ads_sync():
    if not get_token() or is_sync_running():
        raise HTTPException(status_code=409, detail="WB не подключён или синхронизация уже выполняется")
    def worker():
        try:
            sync_ads_only()
        finally:
            dashboard_response_cache.clear()
            context_response_cache.clear()
    if not sync_runner.start(worker):
        raise HTTPException(status_code=409, detail="Синхронизация уже выполняется")
    return {"status": "started"}


@app.get("/api/v1/sync/sections")
def sync_sections():
    import re
    sections = {key: None for key in ("products", "orders", "sales", "financial_rows", "stocks", "ads")}
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT finished_at, details FROM sync_log WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1000").fetchall()
    for finished, details in rows:
        text = details or ""
        for key in sections:
            if sections[key] is None and re.search(r"(?:^|,\s*)" + key + r":\s*\d+(?=,|$)", text):
                if key == "ads" and ("ads_error:" in text or "ads_skipped:" in text):
                    continue
                sections[key] = finished
    return {"sections": sections}


@app.get("/api/v1/sync/history")
def get_tenant_sync_history() -> dict:
    """Return the existing tenant sync log without exposing any API secrets."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, started_at, finished_at, status, details FROM sync_log ORDER BY id DESC LIMIT 14"
        ).fetchall()
    return json_safe({"runs": [dict(row) for row in rows]})


@app.get("/api/v1/data-status")
def get_data_status() -> dict:
    """Connection presence and data age, independent of the selected page."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT finished_at, status, details FROM sync_log WHERE status IN ('success', 'partial') AND finished_at IS NOT NULL AND COALESCE(details, '') NOT LIKE 'scope: ads%' ORDER BY id DESC LIMIT 1").fetchone()
        missing_costs = conn.execute("SELECT COUNT(*) FROM costs WHERE cost_per_wb_unit > 0").fetchone()[0] == 0 if conn.execute("SELECT 1 FROM sqlite_master WHERE name='costs'").fetchone() else True
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='products_catalog'").fetchone() and conn.execute("SELECT 1 FROM sqlite_master WHERE name='costs'").fetchone():
            missing_costs = missing_costs or conn.execute("SELECT COUNT(*) FROM products_catalog p WHERE NOT EXISTS (SELECT 1 FROM costs c WHERE c.nm_id=p.nm_id AND c.cost_per_wb_unit>0)").fetchone()[0] > 0
        has_production_settings = conn.execute("SELECT 1 FROM sqlite_master WHERE name='production_settings'").fetchone() is not None
        has_material_inventory_table = conn.execute("SELECT 1 FROM sqlite_master WHERE name='material_inventory_color'").fetchone() is not None
        has_enabled_production = has_production_settings and conn.execute("SELECT 1 FROM production_settings WHERE enabled=1 LIMIT 1").fetchone() is not None
        has_material_inventory = has_material_inventory_table and conn.execute("SELECT 1 FROM material_inventory_color WHERE balance_known=1 LIMIT 1").fetchone() is not None
        missing_material_inventory = bool(has_enabled_production and not has_material_inventory)
    updated_at = None
    lag_minutes = None
    if row:
        try:
            updated = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=ZoneInfo("Europe/Moscow"))
            updated_at = updated.isoformat()
            lag_minutes = max(0, int((datetime.now(ZoneInfo("Europe/Moscow")) - updated).total_seconds() // 60))
        except (ValueError, TypeError):
            pass
    partial = bool(row and row[1] == "partial")
    if partial and row[2] and ("ads_error:" in row[2] or "ads_skipped:" in row[2]):
        import re
        other_failure = any(marker in row[2] for marker in ("products_error:", "financial_error:", "fifo_error:")) or re.search(r"fifo_errors:\s*[1-9]", row[2])
        ads_updated = sync_sections()["sections"]["ads"]
        if not other_failure and ads_updated and ads_updated >= row[0]:
            partial = False
    stale = partial or lag_minutes is None or lag_minutes > max(90, int(load_settings().get("sync_interval_minutes", 30)) * 3)
    return {"connected": bool(get_token()), "state": "stale" if stale else "fresh", "partial": partial, "details": row[2] if partial else None, "source": "wb-api", "updated_at": updated_at, "lag_minutes": lag_minutes, "missing_costs": missing_costs, "missing_material_inventory": missing_material_inventory}


@app.get("/api/today")
def get_today() -> dict:
    if table_count("orders") == 0 and table_count("sales") == 0:
        raise HTTPException(status_code=409, detail="no_data_yet")

    def build() -> dict:
        ctx = _build_ctx()
        return json_safe(compute_today(ctx))

    return _dashboard_response("legacy-today", build)


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

    def build() -> dict:
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

    return _dashboard_response(f"overview:{period}:{window_start}:{window_end}", build)


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

    def build() -> dict:
        ctx = {
            "period": period,
            "period_label": PERIOD_LABELS.get(period, period),
            "start": window_start,
            "end": window_end,
            "data": build_dashboard(window_start, window_end),
            "today_msk": today_msk,
        }
        return json_safe(compute_products(ctx))

    return _dashboard_response(f"products:{period}:{window_start}:{window_end}", build)


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

    def build() -> dict:
        ctx = {
            "period": period,
            "period_label": PERIOD_LABELS.get(period, period),
            "start": window_start,
            "end": window_end,
            "data": build_dashboard(window_start, window_end),
            "today_msk": today_msk,
        }
        return json_safe(compute_ads(ctx))

    return _dashboard_response(f"ads:{period}:{window_start}:{window_end}", build)


@app.get("/api/v1/dashboard/today")
def get_dashboard_today() -> dict:
    """Compatibility payload for the redesigned dashboard."""
    def build() -> dict:
        today, data = _today_dashboard()
        trend_week = _revenue_trend_days(7)
        return json_safe({
            **today,
            "metrics": _today_metrics(today),
            "actions": today.get("signals", []),
            "inventory_watch": _product_inventory(data, prioritize_known_stock=True),
            "revenue_trend": trend_week,
            "revenue_trend_day": _revenue_trend_hours(data),
            "revenue_trend_week": trend_week,
        })

    return _dashboard_response("dashboard-today", build)


@app.get("/api/v1/action-plan/items")
def list_action_plan_items() -> dict:
    _init_redesign_schema()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM redesign_action_plan ORDER BY created_at DESC").fetchall()
    return {"items": json_safe([_plan_row(row) for row in rows])}


@app.post("/api/v1/action-plan/items")
def create_action_plan_item(payload: ActionPlanCreate) -> dict:
    _init_redesign_schema()
    now = datetime.now(ZoneInfo("Europe/Moscow")).replace(tzinfo=None).isoformat(timespec="seconds")
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        # A dashboard signal has a stable source_action_id. Reusing it must open
        # the existing task, rather than silently placing a duplicate in the plan.
        if payload.source_action_id:
            existing = conn.execute(
                """SELECT * FROM redesign_action_plan
                   WHERE source_action_id = ?
                   ORDER BY created_at DESC, id DESC
                   LIMIT 1""",
                (payload.source_action_id,),
            ).fetchone()
            if existing is not None:
                item = _plan_row(existing)
                return json_safe({"id": str(item["id"]), "status": item["status"], "item": item})
        cursor = conn.execute(
            """INSERT INTO redesign_action_plan
               (source, source_action_id, title, sku, due_at, status, priority, history, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'created', 'medium', ?, ?, ?)""",
            (payload.source, payload.source_action_id, payload.title, payload.sku, payload.due_at,
             json.dumps([{"status": "created", "at": now}], ensure_ascii=False), now, now),
        )
        item_id = cursor.lastrowid
    item = _execute_action_plan_item(int(item_id))
    return json_safe({"id": str(item_id), "status": item["status"], "item": _plan_row(item)})


@app.patch("/api/v1/action-plan/items/{item_id}")
def update_action_plan_item(item_id: int, payload: ActionPlanPatch) -> dict:
    _init_redesign_schema()
    allowed_statuses = {"created", "in_progress", "done", "cancelled", "blocked"}
    allowed_priorities = {"low", "medium", "high", "critical"}
    changes = payload.model_dump(exclude_unset=True)
    if "status" in changes and changes["status"] not in allowed_statuses:
        raise HTTPException(status_code=422, detail="invalid_status")
    if "priority" in changes and changes["priority"] not in allowed_priorities:
        raise HTTPException(status_code=422, detail="invalid_priority")
    if not changes:
        raise HTTPException(status_code=422, detail="empty_patch")

    now = datetime.now(ZoneInfo("Europe/Moscow")).replace(tzinfo=None).isoformat(timespec="seconds")
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        current = conn.execute("SELECT * FROM redesign_action_plan WHERE id = ?", (item_id,)).fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="action_plan_item_not_found")
        history = json.loads(current["history"] or "[]")
        if "status" in changes and changes["status"] != current["status"]:
            history.append({"status": changes["status"], "at": now})
        assignments = [f"{key} = ?" for key in changes]
        values = list(changes.values())
        if "status" in changes:
            assignments.append("history = ?")
            values.append(json.dumps(history, ensure_ascii=False))
        assignments.append("updated_at = ?")
        values.extend([now, item_id])
        conn.execute(f"UPDATE redesign_action_plan SET {', '.join(assignments)} WHERE id = ?", values)
        updated = conn.execute("SELECT * FROM redesign_action_plan WHERE id = ?", (item_id,)).fetchone()
    return json_safe(_plan_row(updated))


@app.post("/api/v1/operations/procurement")
def create_procurement_operation(payload: ProcurementCreateInput) -> dict:
    """Create a real procurement order using the existing tenant DB rules."""
    if not payload.supplier_name.strip():
        raise HTTPException(status_code=422, detail="Укажите поставщика")
    try:
        order_id = create_procurement_order(
            procurement_type=payload.procurement_type,
            supplier_name=payload.supplier_name.strip(),
            status=payload.status,
            order_date=payload.order_date or date.today(),
            payment_due_date=payload.payment_due_date,
            expected_date=payload.expected_date,
            note=payload.note,
            items=pd.DataFrame([item.model_dump() for item in payload.items]),
            source_key=f"redesign:procurement:{datetime.now().isoformat(timespec='seconds')}",
            currency=payload.currency,
            exchange_rate=payload.exchange_rate,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": order_id, "status": payload.status}


@app.get("/api/v1/operations/procurement")
def list_procurement_operations() -> dict:
    """Operational journal for real procurement orders, receipts and payments."""
    return json_safe({
        "orders": _records(read_procurement_orders()),
        "items": _records(read_procurement_items()),
        "payments": _records(read_procurement_payments()),
        "movements": _records(read_procurement_movements()),
    })


@app.patch("/api/v1/operations/procurement/{order_id}/status")
def update_procurement_status(order_id: int, payload: ProcurementStatusInput) -> dict:
    orders = read_procurement_orders()
    row = orders.loc[orders["id"].eq(order_id)] if not orders.empty else pd.DataFrame()
    if row.empty:
        raise HTTPException(status_code=404, detail="Закупка не найдена")
    order = row.iloc[0]
    try:
        update_procurement_order(order_id, str(order.get("supplier_name") or ""), payload.status, order.get("order_date"), order.get("payment_due_date"), order.get("expected_date"), str(order.get("note") or ""), str(order.get("currency") or "RUB"), float(order.get("exchange_rate") or 1))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": order_id, "status": payload.status}


@app.post("/api/v1/operations/procurement/{order_id}/payments")
def create_procurement_payment(order_id: int, payload: ProcurementPaymentInput) -> dict:
    try:
        payment_id = record_procurement_payment(order_id, payload.amount, payload.payment_date or date.today(), payload.method, payload.note)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": payment_id, "status": "applied"}


@app.post("/api/v1/operations/production/shift")
def create_production_shift(payload: ProductionShiftInput) -> dict:
    """Persist an explicitly confirmed shift without inventing a product or quantity."""
    if not payload.tasks:
        raise HTTPException(status_code=422, detail="Добавьте хотя бы одну задачу смены")
    if any(task.planned_units <= 0 for task in payload.tasks):
        raise HTTPException(status_code=422, detail="Плановое количество должно быть больше нуля")
    try:
        save_execution_tasks(pd.DataFrame([task.model_dump() for task in payload.tasks]))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"saved": len(payload.tasks), "status": "Не начато"}


@app.get("/api/v1/operations/production/execution")
def get_production_execution() -> dict:
    tasks = read_table("execution_tasks")
    if not tasks.empty:
        tasks = tasks.loc[tasks["task_type"].astype(str).str.lower().eq("production")].copy()
    return json_safe({"tasks": _records(tasks)})


@app.post("/api/v1/operations/production/execution")
def save_production_execution(payload: ProductionExecutionInput) -> dict:
    if not payload.tasks:
        raise HTTPException(status_code=422, detail="Нет строк смены для сохранения")
    if any(task.actual_units < 0 or task.actual_units > task.planned_units for task in payload.tasks):
        raise HTTPException(status_code=422, detail="Факт должен быть от нуля до планового количества")
    try:
        save_execution_tasks(pd.DataFrame([task.model_dump() for task in payload.tasks]))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"saved": len(payload.tasks), "status": "saved"}


@app.post("/api/v1/operations/production/execution/{task_date}/close")
def close_production_execution(task_date: date) -> dict:
    result = close_production_shift(task_date)
    if result.get("errors"):
        raise HTTPException(status_code=422, detail="; ".join(str(error) for error in result["errors"]))
    return json_safe(result)


@app.post("/api/v1/operations/supply/{order_id}/receipt")
def post_supply_receipt(order_id: int, payload: ReceiptInput) -> dict:
    """Post an explicit receipt into FIFO inventory and update the order status."""
    if not payload.items:
        raise HTTPException(status_code=422, detail="Добавьте полученные позиции")
    items = [item.model_dump() for item in payload.items]
    if all(int(item.get("item_id", 0)) <= 0 for item in items):
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT id FROM procurement_items WHERE order_id=? AND quantity > posted_quantity ORDER BY id LIMIT 1",
                (order_id,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=422, detail="В поставке нет неполученных позиций")
        items[0]["item_id"] = int(row[0])
    result = post_procurement_receipt(order_id, pd.DataFrame(items))
    if result.get("errors") and not result.get("posted"):
        raise HTTPException(status_code=422, detail="; ".join(result["errors"]))
    return result


@app.get("/api/v1/operations/reports/pdf")
def download_report_pdf(
    name: str = Query("Операционный отчёт"),
    days: int = Query(30, ge=1, le=365),
) -> Response:
    """Build a real PDF from the tenant report pipeline."""
    end = date.today()
    start = end - timedelta(days=days - 1)
    try:
        pdf = build_report_pdf(name, ["sales_orders", "ads", "stocks"], start, end)
    except (TypeError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="marketshelper-report.pdf"'},
    )


def _report_definition(store: ReportStore, definition_id: int) -> dict:
    definition = next((row for row in store.list_definitions() if int(row["id"]) == definition_id), None)
    if definition is None:
        raise HTTPException(status_code=404, detail="Расписание отчёта не найдено")
    return definition


@app.get("/api/v1/operations/reports")
def list_report_operations() -> dict:
    store = ReportStore(DB_PATH)
    runs = store.list_runs(limit=50)
    reports_root = (DB_PATH.parent / "reports").resolve()
    for run in runs:
        path = Path(str(run.get("file_path") or "")).resolve() if run.get("file_path") else None
        run["file_available"] = bool(path and path.is_file() and reports_root in path.parents)
        try:
            run["metric_count"] = len(json.loads(run.get("definition_metrics") or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            run["metric_count"] = 0
    return json_safe({"definitions": store.list_definitions(enabled_only=True), "runs": runs, "metric_labels": METRIC_LABELS})


@app.post("/api/v1/operations/reports/definitions")
def create_report_definition(payload: ReportDefinitionInput) -> dict:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Укажите название отчёта")
    if not payload.metrics or any(code not in METRIC_LABELS for code in payload.metrics):
        raise HTTPException(status_code=422, detail="Выберите допустимые метрики отчёта")
    if payload.schedule_type not in {"daily", "weekly", "monthly"}:
        raise HTTPException(status_code=422, detail="Неизвестная периодичность")
    try:
        hour, minute = (int(part) for part in payload.schedule_time.split(":"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Время должно быть в формате ЧЧ:ММ") from None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise HTTPException(status_code=422, detail="Укажите корректное время")
    if payload.schedule_type == "weekly" and payload.schedule_weekday not in range(7):
        raise HTTPException(status_code=422, detail="Выберите день недели")
    if payload.schedule_type == "monthly" and (payload.schedule_day is None or not 1 <= payload.schedule_day <= 28):
        raise HTTPException(status_code=422, detail="Выберите число месяца от 1 до 28")
    store = ReportStore(DB_PATH)
    definition_id = store.add_definition(
        name=name, metrics=payload.metrics, schedule_type=payload.schedule_type,
        schedule_time=f"{hour:02d}:{minute:02d}", schedule_weekday=payload.schedule_weekday,
        schedule_day=payload.schedule_day,
        email_enabled=payload.email_enabled,
        telegram_enabled=payload.telegram_enabled,
    )
    return {"id": definition_id, "status": "created"}


@app.get("/api/v1/operations/reports/delivery")
def report_delivery_status() -> dict:
    return delivery.get_delivery_status()


@app.post("/api/v1/operations/reports/telegram/link-code")
def report_telegram_link_code() -> dict:
    result = delivery.request_telegram_link_code()
    if result is None:
        raise HTTPException(status_code=502, detail="Не удалось получить код привязки Telegram")
    code, ttl_minutes = result
    status = delivery.get_delivery_status()
    bot_username = str(status.get("telegram_bot_username") or "").lstrip("@")
    deep_link = f"https://t.me/{bot_username}?start=link_{code}" if bot_username else ""
    return {"code": code, "ttl_minutes": ttl_minutes, "deep_link": deep_link, "bot_username": bot_username}


@app.post("/api/v1/operations/reports/definitions/{definition_id}/generate")
def generate_report_definition(definition_id: int) -> dict:
    store = ReportStore(DB_PATH)
    definition = _report_definition(store, definition_id)
    run_id = store.enqueue_run(
        definition_id,
        delivery_requested=bool(definition.get("email_enabled") or definition.get("telegram_enabled")),
    )
    return {"id": definition_id, "run_id": run_id, "status": "queued"}


@app.post("/api/v1/operations/reports/quick")
def generate_quick_report(payload: QuickReportInput) -> dict:
    """Generate an ad-hoc PDF and persist it in the same report archive."""
    name = payload.name.strip() or "Операционный отчёт"
    days = max(1, min(int(payload.days), 365))
    metrics = payload.metrics or ["sales_orders", "ads", "stocks"]
    if not metrics or any(code not in METRIC_LABELS for code in metrics):
        raise HTTPException(status_code=422, detail="Выберите допустимые разделы отчёта")
    store = ReportStore(DB_PATH)
    definition_id = store.add_definition(
        name=name, metrics=metrics,
        schedule_type="daily", schedule_time="00:00",
    )
    store.set_enabled(definition_id, False)
    run_id = store.enqueue_run(definition_id, period_days=days)
    return {"id": definition_id, "run_id": run_id, "status": "queued"}


@app.delete("/api/v1/operations/reports/definitions/{definition_id}")
def delete_report_definition(definition_id: int) -> dict:
    store = ReportStore(DB_PATH)
    _report_definition(store, definition_id)
    store.delete_definition(definition_id)
    return {"id": definition_id, "status": "deleted"}


@app.get("/api/v1/operations/reports/runs/{run_id}/download")
def download_saved_report(run_id: int) -> Response:
    store = ReportStore(DB_PATH)
    run = next((row for row in store.list_runs(limit=200) if int(row["id"]) == run_id), None)
    if run is None or run.get("status") != "ok" or not run.get("file_path"):
        raise HTTPException(status_code=404, detail="Готовый файл отчёта не найден")
    path = Path(str(run["file_path"])).resolve()
    reports_root = (DB_PATH.parent / "reports").resolve()
    if not path.is_file() or reports_root not in path.parents:
        raise HTTPException(status_code=404, detail="Файл отчёта недоступен")
    return Response(content=path.read_bytes(), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{path.name}"'})


@app.get("/api/v1/operations/incidents")
def list_wb_incidents() -> dict:
    return json_safe({"cases": _records(read_wb_incident_cases(100)), "summary": wb_incident_financial_summary()})


@app.post("/api/v1/operations/incidents")
def create_wb_incident(payload: IncidentCaseInput) -> dict:
    try:
        case_id = create_wb_incident_case(payload.incident_name, payload.incident_date.isoformat(), payload.warehouse_label, payload.document_ref, payload.note)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": case_id, "status": "open"}


@app.post("/api/v1/operations/incidents/{case_id}/compensations")
def add_wb_incident_compensation(case_id: int, payload: IncidentCompensationInput) -> dict:
    try:
        compensation_id = record_wb_incident_compensation(case_id, payload.amount_rub, payload.compensation_date.isoformat(), payload.source_ref, payload.note)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": compensation_id, "status": "applied"}


@app.post("/api/v1/operations/incidents/{case_id}/loss")
def apply_incident_loss(case_id: int, payload: IncidentLossInput) -> dict:
    if not payload.confirmed:
        raise HTTPException(status_code=422, detail="Подтвердите проведение утраты WB")
    try:
        return json_safe(apply_wb_incident_loss(case_id, payload.nm_id, payload.units, payload.note))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/dashboard/stock")
def get_dashboard_stock() -> dict:
    def build() -> dict:
        today, data = _today_dashboard()
        procurement = today.get("tabs", {}).get("procurement", {})
        forecast_end = datetime.now(ZoneInfo("Europe/Moscow")).date()
        forecast_data = build_dashboard(forecast_end - timedelta(days=29), forecast_end)
        products = forecast_data.products
        stock_total = int(round(float(data.kpi.get("stock", 0) or 0)))
        coverage = _stock_coverage(forecast_data)
        metrics = [
            {"key": "stock", "label": "Остаток на WB", "value": stock_total, "note": "текущий снимок"},
            {"key": "products", "label": "Карточки", "value": len(products), "note": "в расчёте"},
            {"key": "critical", "label": "Критичный запас", "value": sum(1 for row in coverage if row["tone"] == "danger"), "note": "требуют внимания"},
        ]
        return json_safe({
            "sync": {"state": "stale" if today.get("sync_stale") else "fresh", "source": "wb-api"},
            "metrics": metrics,
            "inventory_watch": _product_inventory(forecast_data),
            "reorder_plan": procurement.get("products_to_buy", []),
            "coverage": coverage,
            "rules": [
                {"id": "critical", "title": "Критичный запас", "description": f"{sum(1 for row in coverage if row['tone'] == 'danger')} SKU имеют запас до 3 дней."},
                {"id": "target", "title": "Целевой запас", "description": "План пополнения рассчитывается до покрытия 21 дня продаж."},
                {"id": "inbound", "title": "Товары в пути", "description": "В пути учитывается отдельно и не входит в доступный остаток WB."},
            ],
            "hero": {
                "primary": f"{len(procurement.get('products_to_buy', []))} SKU в плане пополнения",
                "primaryNote": "Рассчитано по текущему запасу и скорости продаж.",
                "secondary": str(stock_total),
                "secondaryNote": "ед. на складах WB",
                "tone": "warn" if procurement.get("products_to_buy") else "info",
            },
        })

    return _dashboard_response("dashboard-stock", build)


@app.get("/api/v1/fifo/reconciliation")
def get_fifo_reconciliation() -> dict:
    """Read-only FIFO diagnostic and its local-reconciliation guard state."""
    preview = preview_finished_goods_reconciliation()
    sync_state = last_sync() or {}
    guard = finished_goods_fifo_guard_status(
        str(sync_state.get("finished_at") or sync_state.get("started_at") or "")
    )
    return json_safe({
        "preview": _records(preview),
        "status": fifo_reconciliation_status(),
        "guard": guard,
        "wb_context": wb_fifo_reconciliation_context(),
        "runs": _records(read_fifo_reconciliation_runs(30)),
    })


@app.get("/api/v1/fifo/reconciliation/runs/{run_id}")
def get_fifo_reconciliation_run(run_id: int) -> dict:
    return json_safe({"items": _records(read_fifo_reconciliation_lines(run_id, 500))})


@app.post("/api/v1/fifo/reconciliation/apply")
def apply_fifo_reconciliation(payload: FifoReconciliationApplyInput) -> dict:
    """Apply only the safe local rows; WB rows remain excluded in db logic."""
    if not payload.confirmed:
        raise HTTPException(status_code=422, detail="Подтвердите безопасные локальные строки")

    sync_state = last_sync() or {}
    guard = finished_goods_fifo_guard_status(
        str(sync_state.get("finished_at") or sync_state.get("started_at") or "")
    )
    if not bool(guard.get("manual_reconciliation_allowed", False)):
        raise HTTPException(status_code=409, detail=str(guard.get("reason") or "Безопасная локальная сверка пока заблокирована"))

    return json_safe(apply_finished_goods_reconciliation(payload.note.strip()))


@app.get("/api/v1/dashboard/ads")
def get_dashboard_ads(
    period: str = Query(DEFAULT_PERIOD, pattern="^(today|7d|30d|90d|custom)$"),
    start: date | None = None,
    end: date | None = None,
) -> dict:
    """Ad dashboard with the same shared period contract as the legacy app."""
    if table_count("orders") == 0 and table_count("sales") == 0:
        raise HTTPException(status_code=409, detail="no_data_yet")

    today_msk = datetime.now(ZoneInfo("Europe/Moscow")).date()
    try:
        window_start, window_end = resolve_window(period, today_msk, start, end)
    except ValueError:
        raise HTTPException(status_code=422, detail="custom_period_requires_start_and_end")

    def build() -> dict:
        ctx = {
            "period": period,
            "period_label": PERIOD_LABELS.get(period, period),
            "start": window_start,
            "end": window_end,
            "data": build_dashboard(window_start, window_end),
            "today_msk": today_msk,
        }
        payload = compute_ads(ctx)
        payload["metrics"] = payload.get("kpis", [])
        return json_safe(payload)

    return _dashboard_response(f"dashboard-ads:{period}:{window_start}:{window_end}", build)


@app.get("/api/v1/dashboard/production")
def get_dashboard_production() -> dict:
    def build() -> dict:
        today, _ = _today_dashboard()
        shift = today.get("tabs", {}).get("shift", {})
        material = today.get("tabs", {}).get("material", {})
        tasks = _records(read_table("execution_tasks"))
        settings = _records(read_table("production_settings"))
        saved_tasks = shift.get("tasks", []) or tasks
        recommendations = shift.get("recommendations", []) or []
        visible_tasks = saved_tasks or recommendations
        planned = shift.get("planned", 0) or sum(int(task.get("planned", 0) or 0) for task in recommendations)
        covered = shift.get("material_covered", 0) if saved_tasks else sum(
            int(task.get("planned", 0) or 0)
            for task in recommendations
            if task.get("status") != "Заблокировано сырьём"
        )
        stages, materials = _production_panels()
        return json_safe({"metrics": [
            {"key": "planned", "label": "План смены" if saved_tasks else "К запуску", "value": planned, "note": "комплектов"},
            {"key": "covered", "label": "Материал подтверждён", "value": covered, "note": "комплектов"},
            {"key": "configured", "label": "Настроено SKU", "value": len(settings), "note": "параметры производства"},
        ], "stages": stages, "tasks": visible_tasks, "materials": material.get("rows", []) or materials})

    return _dashboard_response("dashboard-production", build)


@app.get("/api/v1/dashboard/supply")
def get_dashboard_supply() -> dict:
    def build() -> dict:
        today, _ = _today_dashboard()
        procurement = today.get("tabs", {}).get("procurement", {})
        orders = procurement.get("active_orders", []) or _records(read_procurement_orders())
        suppliers = read_suppliers(active_only=True)
        return json_safe({"metrics": [
            {"key": "active", "label": "Активные закупки", "value": len(orders), "note": "не закрыты"},
            {"key": "to_buy", "label": "Товары к заказу", "value": len(procurement.get("products_to_buy", [])), "note": "расчёт WB"},
        ], "shipments": orders, "suppliers": _records(suppliers)})

    return _dashboard_response("dashboard-supply", build)


@app.get("/api/v1/operations/suppliers")
def get_suppliers() -> dict:
    return json_safe({"items": _records(read_suppliers(active_only=False))})


@app.post("/api/v1/operations/suppliers")
def upsert_supplier(payload: SupplierInput) -> dict:
    try:
        data = payload.model_dump()
        data["supplier_id"] = data.pop("id")
        supplier_id = save_supplier(**data)
    except (TypeError, ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row = next((item for item in _records(read_suppliers()) if int(item.get("id", 0)) == supplier_id), None)
    return json_safe({"id": supplier_id, "status": "saved", "item": row})


@app.get("/api/v1/dashboard/assistant")
def get_dashboard_assistant() -> dict:
    settings = load_settings()
    store = AgentStore(DB_PATH)
    candidates = _unique_agent_candidates(store.list_candidates(status="pending"))
    history = [row for row in store.list_candidates(status=None) if row.get("status") != "pending"][:30]
    return json_safe({"metrics": [{"key": "token", "label": "WB API", "value": "подключён" if get_token() else "не подключён", "note": "состояние токена"},
                                   {"key": "candidates", "label": "Рекомендации AI-агентов", "value": len(candidates), "note": "ждут решения"}],
                      "automations": [{"title": f"{row['marketplace'].upper()} · {row['agent']} · {row['action']}",
                                       "detail": f"{row['target_name'] or row['target_id']}: {row['reason']}",
                                       "status": "ждёт решения", "tone": "warn"} for row in candidates[:8]],
                      "candidates": candidates[:50],
                      "history": history,
                      "settings": {"ui_mode": settings.get("ui_mode", "expert")}})


@app.get("/api/v1/control/data-quality")
def get_data_quality_control() -> dict:
    settings = load_settings()
    sync_info = last_sync() or {}
    quality, summary, reliability, _cost_quality = build_data_quality_overview(
        settings, bool(get_token()), sync_info, latest_backup()
    )
    guard = finished_goods_fifo_guard_status(
        str(sync_info.get("finished_at") or sync_info.get("started_at") or "")
    )
    return json_safe({"summary": summary, "checks": _records(quality), "reliability": _records(reliability), "fifo_guard": guard})


@app.get("/api/v1/operations/backups")
def list_tenant_backups() -> dict:
    return json_safe({"backups": list_backups(limit=14), "last_sync": last_sync() or {}})


@app.post("/api/v1/operations/backups")
def create_tenant_backup() -> dict:
    try:
        path = create_backup(kind="manual")
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return json_safe({"name": path.name, "status": "created", "latest": latest_backup()})


@app.get("/api/v1/settings")
def get_redesign_settings() -> dict:
    settings = load_settings()
    return {"sync_interval_minutes": int(settings.get("sync_interval_minutes", 30)), "initial_history_days": int(settings.get("initial_history_days", 90)), "agent_interval_minutes": int(settings.get("agent_interval_minutes", 60)), "wb_connected": bool(get_token()), "ozon_connected": bool(get_ozon_credentials())}


@app.patch("/api/v1/settings")
def update_redesign_settings(payload: SettingsUpdateInput) -> dict:
    settings = load_settings()
    changes = payload.model_dump(exclude_none=True)
    ranges = {"sync_interval_minutes": (15, 1440), "initial_history_days": (7, 90), "agent_interval_minutes": (15, 1440)}
    for key, value in changes.items():
        low, high = ranges[key]
        if value < low or value > high:
            raise HTTPException(status_code=422, detail=f"{key}: допустимо от {low} до {high}")
        settings[key] = value
    save_settings(settings)
    return get_redesign_settings()


@app.get("/api/v1/settings/material-inventory")
def get_material_inventory_settings() -> dict:
    """Return physical raw-material balances together with their FIFO layers."""
    inventory = read_table("material_inventory_color")
    columns = [column for column in (
        "material_key", "material_name", "balance_known", "full_rolls",
        "partial_meters", "roll_length", "unit", "tracking_mode",
        "opening_rate_rub", "note", "updated_at",
    ) if column in inventory.columns]
    return json_safe({
        "items": _records(inventory[columns] if columns else inventory),
        "fifo": _records(read_material_fifo_summary()),
        "opening_rate_rub_m": get_fifo_opening_rate(),
    })


@app.patch("/api/v1/settings/material-inventory")
def update_material_inventory(payload: MaterialInventoryUpdateInput) -> dict:
    if not payload.items:
        raise HTTPException(status_code=422, detail="Нужна хотя бы одна строка сырья")
    if len(payload.items) > 200:
        raise HTTPException(status_code=422, detail="За один раз можно сохранить до 200 материалов")
    seen: set[str] = set()
    rows: list[dict[str, object]] = []
    for item in payload.items:
        name = item.material_name.strip()
        key = name.casefold()
        if not name:
            raise HTTPException(status_code=422, detail="Название материала не может быть пустым")
        if key in seen:
            raise HTTPException(status_code=422, detail=f"Материал «{name}» передан дважды")
        if item.tracking_mode not in {"packaged", "quantity"}:
            raise HTTPException(status_code=422, detail="Способ учёта должен быть packaged или quantity")
        if item.full_rolls < 0 or item.partial_meters < 0 or item.roll_length <= 0 or item.opening_rate_rub < 0:
            raise HTTPException(status_code=422, detail="Остатки, размер упаковки и ставки не могут быть отрицательными")
        seen.add(key)
        rows.append(item.model_dump())
    try:
        save_material_inventory(pd.DataFrame(rows))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return json_safe({"saved": len(rows), **get_material_inventory_settings()})


@app.post("/api/v1/fifo/materials/initialize")
def initialize_material_fifo_layers(payload: MaterialFifoInitializeInput) -> dict:
    """Create opening layers for confirmed physical raw-material balances."""
    rate = payload.opening_rate_rub_m if payload.opening_rate_rub_m is not None else get_fifo_opening_rate()
    try:
        result = initialize_material_fifo(rate)
        set_fifo_opening_rate(rate)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return json_safe({"result": result, "fifo": _records(read_material_fifo_summary()), "opening_rate_rub_m": rate})


@app.get("/api/v1/settings/product-costs")
def get_product_costs() -> dict:
    """Return the same per-WB-unit cost rows used by the legacy settings page."""
    catalog = read_table("products_catalog")
    costs = read_table("costs")
    catalog_columns = [column for column in ("nm_id", "supplier_article", "product_name") if column in catalog.columns]
    cost_columns = [column for column in ("nm_id", "supplier_article", "cost_per_wb_unit", "note", "updated_at") if column in costs.columns]

    if catalog_columns and not catalog.empty:
        rows = catalog[catalog_columns].copy()
        if "nm_id" not in rows.columns:
            rows = pd.DataFrame()
    elif cost_columns:
        rows = costs[cost_columns].copy()
    else:
        rows = pd.DataFrame(columns=["nm_id", "supplier_article", "product_name"])

    if not catalog.empty and not rows.empty and cost_columns:
        cost_rows = costs[cost_columns].copy()
        duplicate_columns = [column for column in ("supplier_article",) if column in rows.columns and column in cost_rows.columns]
        cost_rows = cost_rows.drop(columns=duplicate_columns)
        rows = rows.merge(cost_rows, on="nm_id", how="left")
    elif not cost_columns:
        rows["cost_per_wb_unit"] = 0.0
        rows["note"] = ""

    for column, default in {"supplier_article": "", "product_name": "", "cost_per_wb_unit": 0.0, "note": "", "updated_at": ""}.items():
        if column not in rows.columns:
            rows[column] = default
    rows["nm_id"] = pd.to_numeric(rows["nm_id"], errors="coerce").fillna(0).astype(int)
    rows = rows[rows["nm_id"] > 0].drop_duplicates("nm_id")
    rows["cost_per_wb_unit"] = pd.to_numeric(rows["cost_per_wb_unit"], errors="coerce").fillna(0.0)
    rows = rows.sort_values(["product_name", "supplier_article", "nm_id"], kind="stable")
    return json_safe({"items": _records(rows[["nm_id", "supplier_article", "product_name", "cost_per_wb_unit", "note", "updated_at"]])})


@app.patch("/api/v1/settings/product-costs")
def update_product_costs(payload: ProductCostsUpdateInput) -> dict:
    if not payload.items:
        raise HTTPException(status_code=422, detail="Нужна хотя бы одна строка себестоимости")
    if len(payload.items) > 500:
        raise HTTPException(status_code=422, detail="За один раз можно сохранить до 500 строк")
    rows: list[dict[str, object]] = []
    seen: set[int] = set()
    for item in payload.items:
        if item.nm_id <= 0:
            raise HTTPException(status_code=422, detail="Артикул WB должен быть положительным")
        if item.nm_id in seen:
            raise HTTPException(status_code=422, detail=f"Артикул WB {item.nm_id} передан дважды")
        if item.cost_per_wb_unit < 0 or item.cost_per_wb_unit > 10_000_000:
            raise HTTPException(status_code=422, detail="Себестоимость должна быть от 0 до 10 000 000 ₽")
        seen.add(item.nm_id)
        rows.append({
            "nm_id": item.nm_id,
            "supplier_article": item.supplier_article.strip(),
            "cost_per_wb_unit": item.cost_per_wb_unit,
            "note": item.note.strip(),
        })
    save_costs(pd.DataFrame(rows))
    return {"saved": len(rows)}


@app.get("/api/v1/settings/production-norms")
def get_production_norms() -> dict:
    """Return the per-kit material norms used by the production plan."""
    catalog = read_table("products_catalog")
    settings = read_table("production_settings")
    if settings.empty:
        return {"items": []}

    columns = [
        column for column in (
            "nm_id", "supplier_article", "enabled", "material_per_unit",
            "min_batch", "pack_size", "blank_type",
        ) if column in settings.columns
    ]
    rows = settings[columns].copy()
    if "nm_id" not in rows.columns:
        return {"items": []}
    if not catalog.empty and {"nm_id", "product_name"}.issubset(catalog.columns):
        names = catalog[["nm_id", "product_name"]].drop_duplicates("nm_id")
        rows = rows.merge(names, on="nm_id", how="left")
    for column, default in {
        "supplier_article": "", "product_name": "", "enabled": 0,
        "material_per_unit": 0.0, "min_batch": 1, "pack_size": 1,
        "blank_type": "",
    }.items():
        if column not in rows.columns:
            rows[column] = default
    rows["nm_id"] = pd.to_numeric(rows["nm_id"], errors="coerce").fillna(0).astype(int)
    rows = rows[rows["nm_id"] > 0]
    rows["enabled"] = pd.to_numeric(rows["enabled"], errors="coerce").fillna(0).astype(int)
    rows["material_per_unit"] = pd.to_numeric(rows["material_per_unit"], errors="coerce").fillna(0.0)
    rows["min_batch"] = pd.to_numeric(rows["min_batch"], errors="coerce").fillna(1).astype(int)
    rows["pack_size"] = pd.to_numeric(rows["pack_size"], errors="coerce").fillna(1).astype(int)
    rows = rows.sort_values(["supplier_article", "nm_id"], kind="stable")
    return json_safe({"items": _records(rows[[
        "nm_id", "supplier_article", "product_name", "enabled",
        "material_per_unit", "min_batch", "pack_size", "blank_type",
    ]])})


@app.patch("/api/v1/settings/production-norms")
def update_production_norms(payload: ProductionNormsUpdateInput) -> dict:
    if not payload.items:
        raise HTTPException(status_code=422, detail="Нет норм для сохранения")
    if any(item.nm_id <= 0 or item.material_per_unit < 0 or item.min_batch <= 0 or item.pack_size <= 0 for item in payload.items):
        raise HTTPException(status_code=422, detail="Проверьте расход, размер партии и комплект")
    with sqlite3.connect(DB_PATH) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(production_settings)")}
        if "nm_id" not in columns:
            raise HTTPException(status_code=409, detail="Таблица норм производства недоступна")
        for item in payload.items:
            assignments = []
            values: list[object] = []
            for name, value in (("material_per_unit", item.material_per_unit), ("min_batch", item.min_batch), ("pack_size", item.pack_size), ("enabled", int(item.enabled))):
                if name in columns:
                    assignments.append(f"{name} = ?")
                    values.append(value)
            if assignments:
                values.append(item.nm_id)
                conn.execute(f"UPDATE production_settings SET {', '.join(assignments)} WHERE nm_id = ?", values)
    return {"saved": len(payload.items)}


@app.get("/api/v1/agent/candidates")
def list_agent_candidates(status: str = Query("pending", pattern="^(pending|applied|rejected|all)$")) -> dict:
    store = AgentStore(DB_PATH)
    rows = store.list_candidates(status=None if status == "all" else status)
    if status == "pending":
        rows = _unique_agent_candidates(rows)
    return {"items": json_safe(rows[:100])}


@app.post("/api/v1/agent/refresh")
def refresh_agent_recommendations() -> dict:
    """Refresh recommendations only; never applies a marketplace change."""
    token = get_token()
    if not token:
        raise HTTPException(status_code=409, detail="WB token не подключён")
    store = AgentStore(DB_PATH)
    client = WBAgentClient(token)
    warnings: list[str] = []
    price_ids: list[int] = []
    ad_ids: list[int] = []
    try:
        price_ids = price_agent.evaluate(client, store, PriceRules(), cost_lookup=wb_cost_lookup())
    except Exception as exc:
        warnings.append(f"цены: {exc}")
    try:
        ad_ids = ad_agent.evaluate(client, store, AdRules())
    except Exception as exc:
        warnings.append(f"реклама: {exc}")
    pending = _unique_agent_candidates(store.list_candidates(status="pending"))
    for candidate in pending:
        _close_stale_agent_duplicates(store, candidate)
    return {"price": len(price_ids), "ads": len(ad_ids), "pending": len(pending), "warnings": warnings, "mode": "recommend-only"}


@app.post("/api/v1/agent/candidates/{candidate_id}/decision")
def decide_agent_candidate(candidate_id: int, payload: AgentDecisionInput) -> dict:
    if payload.outcome not in {"apply", "reject"}:
        raise HTTPException(status_code=422, detail="outcome должен быть apply или reject")
    store = AgentStore(DB_PATH)
    candidates = [row for row in store.list_candidates(status="pending") if int(row["id"]) == candidate_id]
    if not candidates:
        raise HTTPException(status_code=404, detail="agent_candidate_not_found")
    candidate = candidates[0]
    if payload.outcome == "reject":
        store.record_decision(candidate_id, decided_by="human", outcome="rejected")
        _close_stale_agent_duplicates(store, candidate)
        return {"id": candidate_id, "status": "rejected"}

    marketplace = candidate["marketplace"]
    if marketplace == "wb":
        token = get_token()
        if not token:
            raise HTTPException(status_code=409, detail="WB token не подключён")
        client = WBAgentClient(token)
    else:
        credentials = get_ozon_credentials()
        if not credentials:
            raise HTTPException(status_code=409, detail="Ozon credentials не подключены")
        client = OzonAgentClient(*credentials)
    module = price_agent if candidate["agent"] == "price" else ad_agent
    try:
        result = module.apply_candidate(client, store, candidate, decided_by="human", dry_run=False)
    except NotImplementedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result.ok:
        _close_stale_agent_duplicates(store, candidate)
    return {"id": candidate_id, "status": "applied" if result.ok else "rejected", "detail": result.detail}


@app.get("/api/v1/dashboard/reports")
def get_dashboard_reports() -> dict:
    backup = latest_backup()
    store = ReportStore(DB_PATH)
    definitions = store.list_definitions(enabled_only=True)
    runs = store.list_runs(limit=50)
    reports = [{"title": row.get("name", "Отчёт"), "detail": row.get("schedule_type", ""),
                "period": row.get("schedule_time", ""), "status": "включён" if row.get("enabled") else "выключен", "tone": "good" if row.get("enabled") else "info"}
               for row in definitions]
    exports = [{"name": row.get("file_path") or f"Запуск отчёта #{row.get('id')}", "format": "PDF",
                "updated": row.get("finished_at") or row.get("started_at", ""), "tone": "good" if row.get("status") == "ok" else "warn"}
               for row in runs]
    return json_safe({"metrics": [{"key": "backup", "label": "Резервная копия", "value": backup.get("modified_at") if backup else "нет данных", "note": "локальное состояние"},
                                 {"key": "scheduled", "label": "Запланированные отчёты", "value": len(definitions), "note": "настройки tenant"}],
                      "reports": reports, "exports": exports})
