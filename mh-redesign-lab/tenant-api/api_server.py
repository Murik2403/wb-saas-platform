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

from datetime import date, datetime, timedelta, timezone
import json
import hashlib
import math
import os
from contextlib import asynccontextmanager
import sqlite3
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import monotonic
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, FiniteFloat, Field

from ads_service import compute_ads
from backup_tools import create_backup, latest_backup, list_backups
from calculations import build_dashboard
from config import DB_PATH, IS_DEMO, delete_ozon_credentials, delete_token, get_ozon_credentials, get_token, load_settings, save_ozon_credentials, save_settings, save_token
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
from growth_activation import activation_summary, initialize_growth_schema, record_first_value
from growth_delivery import deliver_pending_growth_events
from today_service import compute_today, json_safe
from ui_helpers import build_data_quality_overview
from reports.store import ReportStore
from reports.metrics import METRIC_LABELS
from reports import delivery
from db.procurement import create_procurement_order, post_procurement_receipt
from db.manual_expenses import (
    ALLOCATION_BASES,
    EXPENSE_ARTICLES,
    SOURCES,
    delete_manual_expense,
    read_manual_expenses,
    save_manual_expense,
)
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
from sync import sync_all, sync_ads_only, sync_finances
from wb_api import WBAPI
from report_tools import build_finance_excel, build_reconciliation
from sync_lock import is_sync_running, sync_lock
from sync_runner import SyncRunner
from request_security import write_origin_allowed
from action_plan_executor import ActionPlanExecutor
from sentry_integration import initialize as initialize_sentry


initialize_sentry(service_name="tenant-api")


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
    result: str | None = None


class QuickReportInput(BaseModel):
    name: str = "Операционный отчёт"
    days: int = 30
    metrics: list[str] | None = None


class ProcurementItemInput(BaseModel):
    nm_id: int = 0
    supplier_article: str = ""
    product_name: str = ""
    quantity: FiniteFloat
    # React sends the supplier price in the order currency. The procurement
    # store converts it using the order's exchange_rate; unit_price is the
    # older, already-RUB cost and remains accepted for existing callers.
    supplier_unit_price: FiniteFloat = Field(default=0, ge=0)
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
    task_type: str = "production"
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


class WBTokenInput(BaseModel):
    token: str = Field(min_length=20, max_length=4096)


class OzonCredentialsInput(BaseModel):
    client_id: str = Field(min_length=1, max_length=256)
    api_key: str = Field(min_length=8, max_length=4096)


class ProductCostInput(BaseModel):
    nm_id: int
    supplier_article: str = ""
    cost_per_wb_unit: FiniteFloat
    note: str = ""


class ProductCostsUpdateInput(BaseModel):
    items: list[ProductCostInput]


class ManualExpenseInput(BaseModel):
    nm_id: int = Field(gt=0)
    article: str
    amount_rub: FiniteFloat = Field(ge=0)
    allocation_basis: str = "per_unit"
    source: str = "manual"
    period_start: date
    period_end: date
    note: str = ""


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


class ReportEnabledInput(BaseModel):
    enabled: bool


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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS redesign_price_uploads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wb_upload_id INTEGER NOT NULL,
                goods_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'queued',
                error TEXT NOT NULL DEFAULT '',
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
    item["id"] = str(item["id"])
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
    financial_products = getattr(data, "financial_products", None)
    financial_index = {}
    if financial_products is not None and not financial_products.empty and "Артикул WB" in financial_products.columns:
        for _, financial_product in financial_products.iterrows():
            try:
                key = int(float(financial_product.get("Артикул WB", 0) or 0))
            except (TypeError, ValueError):
                continue
            financial_index[key] = financial_product
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
        margin = pd.to_numeric(product.get("Маржа до комиссий WB"), errors="coerce")
        margin_pct = pd.to_numeric(None, errors="coerce")
        try:
            product_id = int(float(product.get("Артикул WB", 0) or 0))
        except (TypeError, ValueError):
            product_id = 0
        financial_product = financial_index.get(product_id)
        if financial_product is not None:
            margin = pd.to_numeric(financial_product.get("Чистая прибыль"), errors="coerce")
            margin_pct = pd.to_numeric(financial_product.get("Чистая маржинальность, %"), errors="coerce")
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
            "margin_rub": None if pd.isna(margin) else round(float(margin), 2),
            "margin_pct": None if pd.isna(margin_pct) else round(float(margin_pct), 2),
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


def _production_material_covered(tasks: list[dict]) -> int:
    """Return how many planned units are covered by confirmed physical material."""
    settings = read_table("production_settings")
    materials = read_table("material_inventory_color")
    if settings.empty or materials.empty:
        return 0
    norms = {
        int(row.get("nm_id") or 0): (
            str(row.get("material_name") or "").strip().casefold(),
            max(0.0, float(pd.to_numeric(row.get("material_per_unit", 0), errors="coerce") or 0)),
        )
        for _, row in settings.iterrows()
    }
    remaining: dict[str, float] = {}
    for _, row in materials.iterrows():
        if int(pd.to_numeric(row.get("balance_known", 0), errors="coerce") or 0) != 1:
            continue
        key = str(row.get("material_name") or row.get("material_key") or "").strip().casefold()
        roll_length = max(0.1, float(pd.to_numeric(row.get("roll_length", 0), errors="coerce") or 25.5))
        remaining[key] = max(0.0, float(pd.to_numeric(row.get("full_rolls", 0), errors="coerce") or 0) * roll_length + float(pd.to_numeric(row.get("partial_meters", 0), errors="coerce") or 0))
    covered = 0
    for task in tasks:
        planned = max(0, int(task.get("planned_units", task.get("planned", 0)) or 0))
        material_key, rate = norms.get(int(task.get("nm_id") or 0), ("", 0.0))
        if planned <= 0 or not material_key or rate <= 0 or material_key not in remaining:
            continue
        units = min(planned, int((remaining[material_key] + 1e-9) // rate))
        covered += units
        remaining[material_key] = max(0.0, remaining[material_key] - units * rate)
    return covered


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
wb_cost_response_cache = DashboardResponseCache(ttl_seconds=10.0)
_wb_paid_storage_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wb-paid-storage")
_wb_paid_storage_lock = Lock()
_wb_paid_storage_jobs: dict[str, dict[str, object]] = {}
_wb_cost_jobs: dict[str, dict[str, object]] = {}
customer_quality_cache: tuple[float, dict] | None = None
sync_runner = SyncRunner()


@app.middleware("http")
async def invalidate_after_write(request, call_next):
    headers = getattr(request, "headers", {})
    tenant_role = headers.get("x-tenant-role", "owner").lower()
    if tenant_role not in {"owner", "manager", "viewer"}:
        return JSONResponse(status_code=403, content={"detail": "Роль пользователя не распознана"})
    if tenant_role == "viewer" and request.method not in {"GET", "HEAD", "OPTIONS"}:
        return JSONResponse(status_code=403, content={"detail": "Роль наблюдателя доступна только для просмотра"})
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
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
    initialize_growth_schema(DB_PATH)


@app.get("/api/v1/health")
def health():
    return {"service": "marketshelper-tenant", "version": 2}


@app.get("/healthz")
@app.get("/_stcore/health")
def legacy_health() -> dict[str, str]:
    """Compatibility endpoints for external uptime probes and Traefik routing."""
    return {"status": "ok"}


@app.get("/api/v1/session")
def get_dashboard_session(request: Request) -> dict:
    """Return the lightweight identity used by the dashboard shell.

    Authentication is handled by the gateway/Traefik middleware before a
    request reaches the tenant container. The tenant API still needs this
    route so the frontend identity lookup does not become a missing page.
    """
    slug = os.getenv("WB_SAAS_TENANT_SLUG", "marketshelper-live")
    tenant_name = os.getenv("WB_SAAS_TENANT_NAME", slug)
    return {
        "user": {"id": request.headers.get("x-tenant-account", slug), "name": "Пользователь", "role": request.headers.get("x-tenant-role", "owner")},
        "tenant": {"id": slug, "name": tenant_name},
        "mode": "tenant",
    }


@app.get("/api/v1/jobs")
def jobs_status():
    workers = {"sync": False, "recommendations": False, "reports": False}
    proc_root = Path("/proc")
    if proc_root.exists():
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                command = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "ignore")
            except (OSError, UnicodeError):
                continue
            if "sync.py --loop" in command:
                workers["sync"] = True
            if "agent_sync.py --loop" in command:
                workers["recommendations"] = True
            if "report_scheduler.py --loop" in command:
                workers["reports"] = True
    return {"enabled": os.environ.get("MH_LOCAL_JOBS") == "1" and not IS_DEMO, "jobs": local_jobs.status(), "worker_processes": workers}


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


class FinanceSyncInput(BaseModel):
    days: int = 90


class PriceGoodInput(BaseModel):
    nmID: int = Field(gt=0, strict=True)
    price: int = Field(gt=0, strict=True)
    discount: int = Field(ge=0, le=99, strict=True)


class PriceUploadInput(BaseModel):
    goods: list[PriceGoodInput] = Field(min_length=1, max_length=1000)


class ProductCardUpdateInput(BaseModel):
    """Safe subset of card fields editable from the redesigned UI.

    WB overwrites the complete card, so the endpoint reads the authoritative
    card first and applies only these fields before submitting it. The
    explicit confirmation prevents an accidental click from becoming a
    destructive full replacement.
    """

    confirmed: bool = False
    title: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    brand: str | None = Field(default=None, max_length=255)


class ProductCardMediaInput(BaseModel):
    """Complete replacement list for a card's media URLs."""

    confirmed: bool = False
    urls: list[str] = Field(min_length=1, max_length=30)


class FeedbackAnswerInput(BaseModel):
    id: str
    text: str
    edit: bool = False


class QuestionAnswerInput(BaseModel):
    id: str
    text: str
    state: str = "wbRu"


class ReturnClaimInput(BaseModel):
    id: str
    action: str


class ChatMessageInput(BaseModel):
    reply_sign: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=1000)


@app.post("/api/v1/sync/finance")
def start_finance_sync(payload: FinanceSyncInput = FinanceSyncInput()):
    """Refresh the WB realization/financial report without rewriting other sections."""
    if not get_token() or is_sync_running():
        raise HTTPException(status_code=409, detail="WB не подключён или синхронизация уже выполняется")
    days = max(7, min(365, int(payload.days)))
    def worker():
        try:
            sync_finances(days)
        finally:
            dashboard_response_cache.clear()
            context_response_cache.clear()
    if not sync_runner.start(worker):
        raise HTTPException(status_code=409, detail="Синхронизация уже выполняется")
    return {"status": "started", "days": days}


@app.get("/api/v1/products/prices")
def get_wb_product_prices() -> dict:
    token = get_token()
    if not token:
        raise HTTPException(status_code=409, detail="WB token не подключён")
    try:
        return json_safe({"items": WBAPI(token).get_product_prices()})
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Не удалось получить цены WB: {str(exc)[:300]}") from exc


@app.post("/api/v1/products/prices/upload")
def upload_wb_product_prices(payload: PriceUploadInput) -> dict:
    token = get_token()
    if not token:
        raise HTTPException(status_code=409, detail="WB token не подключён")
    if not payload.goods or len(payload.goods) > 1000:
        raise HTTPException(status_code=422, detail="Передайте от 1 до 1000 товаров")
    try:
        result = WBAPI(token, timeout=20).upload_product_prices([good.model_dump() for good in payload.goods])
        if result.get("error"):
            raise ValueError(result.get("errorText") or "WB отклонил загрузку")
        upload_data = result.get("data") if isinstance(result.get("data"), dict) else {}
        upload_id = int(upload_data.get("id") or upload_data.get("uploadID") or result.get("id") or 0)
        if upload_id:
            now = datetime.now(ZoneInfo("Europe/Moscow")).replace(tzinfo=None).isoformat(timespec="seconds")
            _init_redesign_schema()
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("INSERT INTO redesign_price_uploads(wb_upload_id, goods_count, status, created_at, updated_at) VALUES (?, ?, 'queued', ?, ?)", (upload_id, len(payload.goods), now, now))
        return json_safe(result)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Не удалось поставить цены WB в загрузку: {str(exc)[:300]}") from exc


@app.get("/api/v1/products/prices/uploads/{upload_id}")
def get_wb_price_upload_status(upload_id: int) -> dict:
    token = get_token()
    if not token:
        raise HTTPException(status_code=409, detail="WB token не подключён")
    try:
        result = json_safe(WBAPI(token, timeout=15).get_price_upload_status(upload_id))
        _init_redesign_schema()
        terminal = bool(result.get("terminal"))
        status = str(result.get("label") or ("completed" if terminal else "processing"))
        now = datetime.now(ZoneInfo("Europe/Moscow")).replace(tzinfo=None).isoformat(timespec="seconds")
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE redesign_price_uploads SET status=?, updated_at=? WHERE wb_upload_id=?", (status, now, upload_id))
        return result
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Не удалось получить статус загрузки цен: {str(exc)[:300]}") from exc


@app.get("/api/v1/products/prices/uploads")
def list_wb_price_uploads() -> dict:
    _init_redesign_schema()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT wb_upload_id, goods_count, status, error, created_at, updated_at FROM redesign_price_uploads ORDER BY id DESC LIMIT 20").fetchall()
    return {"items": [dict(row) for row in rows]}


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
    today_msk = datetime.now(ZoneInfo("Europe/Moscow")).date()
    try:
        window_start, window_end = resolve_window(period, today_msk, start, end)
    except ValueError:
        raise HTTPException(status_code=422, detail="custom_period_requires_start_and_end")

    if table_count("orders") == 0 and table_count("sales") == 0:
        return {
            "period_label": PERIOD_LABELS.get(period, period),
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
            "kpis": [], "funnel": [], "products": [], "problem_products": [],
            "alerts": [], "cash_windows": [], "chart": {"rows": []},
            "empty_reason": "no_data_yet",
        }

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
    today_msk = datetime.now(ZoneInfo("Europe/Moscow")).date()
    try:
        window_start, window_end = resolve_window(period, today_msk, start, end)
    except ValueError:
        raise HTTPException(status_code=422, detail="custom_period_requires_start_and_end")

    if table_count("orders") == 0 and table_count("sales") == 0:
        return {
            "period_label": PERIOD_LABELS.get(period, period),
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
            "products": [],
            "empty_reason": "no_data_yet",
        }

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


class FirstReportViewedInput(BaseModel):
    nm_id: int = Field(gt=0)
    report_revision: str = Field(min_length=64, max_length=64)


@app.get("/api/v1/growth/activation")
def get_growth_activation(background_tasks: BackgroundTasks, nm_id: int | None = Query(None, gt=0)) -> dict:
    """Read readiness; retry only already-recorded events, never create one."""
    result = activation_summary(DB_PATH, connected=bool(get_token()), is_demo=IS_DEMO, nm_id=nm_id)
    if not IS_DEMO:
        background_tasks.add_task(deliver_pending_growth_events, DB_PATH)
    return result


def _build_first_report(nm_id: int) -> dict:
    summary = activation_summary(DB_PATH, connected=bool(get_token()), is_demo=IS_DEMO, nm_id=nm_id)
    if not summary["ready_for_report"]:
        raise HTTPException(status_code=409, detail="Отчёт пока не готов: проверьте подключение, успешную загрузку, себестоимость и данные реализации выбранного товара.")
    start, end = (date.fromisoformat(summary["period"][key]) for key in ("start", "end"))
    # Use the same accounting-backed computation as Products. No network calls,
    # invented data or default zero profit are permitted in this first result.
    data = build_dashboard(start, end)
    payload = compute_products({"data": data, "period": "custom", "start": start, "end": end})
    product = next((row for row in payload["products"] if str(row.get("nm_id")) == str(nm_id)), None)
    required = ("revenue", "sales", "unit_cost", "margin", "margin_pct")
    if not product or any(not isinstance(product.get(key), (int, float)) or not math.isfinite(product[key]) for key in required):
        raise HTTPException(status_code=409, detail="Для этого товара ещё нет полного расчёта экономики. Проверьте реализацию и себестоимость, затем повторите.")
    if product["unit_cost"] <= 0 or (product["sales"] <= 0 and product["revenue"] == 0):
        raise HTTPException(status_code=409, detail="Нужны сохранённая себестоимость и реальные продажи выбранного товара.")
    report = {
        "nm_id": nm_id,
        "product": str(product.get("product") or product.get("article") or nm_id),
        "period": summary["period"],
        **{key: product[key] for key in required},
        "limitation": "Результат по загруженной реализации WB и учтённым затратам. Налоги и внешние расходы, которых нет в учёте, сюда не входят.",
    }
    report["report_revision"] = hashlib.sha256(json.dumps(report, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return report


@app.get("/api/v1/growth/first-report")
def get_first_growth_report(nm_id: int = Query(..., gt=0)) -> dict:
    return _build_first_report(nm_id)


@app.post("/api/v1/growth/report-viewed")
def record_growth_report_viewed(payload: FirstReportViewedInput, request: Request, background_tasks: BackgroundTasks) -> dict:
    # Gateway/Traefik authenticates the session and overwrites these headers.
    # Unlike a readiness GET, a local request without this identity cannot
    # create a paid-funnel activation record.
    if not request.headers.get("x-tenant-account") or request.headers.get("x-tenant-role", "") not in {"owner", "manager"}:
        raise HTTPException(status_code=401, detail="Войдите в кабинет, чтобы сохранить первый результат.")
    report = _build_first_report(payload.nm_id)
    if report["report_revision"] != payload.report_revision:
        raise HTTPException(status_code=409, detail="Расчёт изменился. Откройте первый отчёт ещё раз.")
    first = record_first_value(DB_PATH, nm_id=payload.nm_id, occurred_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    background_tasks.add_task(deliver_pending_growth_events, DB_PATH)
    return {"first_value_verified": True, "first_value_at": first["occurred_at"]}


@app.get("/api/v1/products/cards")
def get_product_cards() -> dict:
    """Read the seller's WB cards, including a small media preview."""
    token = get_token()
    if not token:
        raise HTTPException(status_code=409, detail="WB token не подключён")
    try:
        rows = WBAPI(token, timeout=30).get_product_cards()
    except Exception as exc:
        return json_safe({"cards": [], "errors": [_customer_quality_error("Карточки WB", exc)]})
    cards = []
    for row in rows[:200]:
        photos = row.get("photos") or row.get("mediaFiles") or []
        if not isinstance(photos, list):
            photos = []
        photo_urls = []
        for photo in photos:
            if isinstance(photo, str) and photo.strip():
                photo_urls.append(photo.strip())
            elif isinstance(photo, dict):
                for key in ("big", "c516x688", "square", "tm", "url"):
                    value = photo.get(key)
                    if isinstance(value, str) and value.strip():
                        photo_urls.append(value.strip())
                        break
        cards.append({
            "nm_id": row.get("nmID") or row.get("nmId") or 0,
            "vendor_code": row.get("vendorCode") or row.get("vendor_code") or "",
            "title": row.get("title") or row.get("imtName") or "Товар без названия",
            "brand": row.get("brand") or "",
            "subject": row.get("subjectName") or row.get("subject") or "",
            "description": row.get("description") or "",
            "photos": photo_urls[:3],
            "sizes": len(row.get("sizes") or []),
        })
    return json_safe({"cards": cards, "errors": []})


def _wb_write_client() -> WBAPI:
    """Return a client suitable for an explicitly confirmed WB write."""
    token = get_token()
    if not token:
        raise HTTPException(status_code=409, detail="WB token не подключён")
    if IS_DEMO:
        raise HTTPException(status_code=409, detail="Записи в WB отключены для демо-кабинета")
    client = WBAPI(token, timeout=30)
    scope_info = client.token_scope_info()
    if scope_info.get("read_only"):
        raise HTTPException(status_code=403, detail="WB-токен доступен только для чтения")
    return client


def _card_nm_id(card: dict) -> int:
    try:
        return int(card.get("nmID") or card.get("nmId") or card.get("nm_id") or 0)
    except (TypeError, ValueError):
        return 0


@app.patch("/api/v1/products/cards/{nm_id}")
def update_wb_product_card(nm_id: int, payload: ProductCardUpdateInput) -> dict:
    """Update a small text subset while preserving the complete WB card."""
    if nm_id <= 0:
        raise HTTPException(status_code=422, detail="Артикул WB должен быть положительным")
    if not payload.confirmed:
        raise HTTPException(status_code=422, detail="Подтвердите отправку изменений в WB")
    changes = payload.model_dump(exclude_none=True, exclude={"confirmed"})
    if not changes:
        raise HTTPException(status_code=422, detail="Изменений карточки нет")
    client = _wb_write_client()
    try:
        rows = client.get_product_cards([nm_id])
        card = next((row for row in rows if _card_nm_id(row) == nm_id), None)
        if card is None:
            raise HTTPException(status_code=404, detail="Карточка WB не найдена")
        updated = dict(card)
        updated.update(changes)
        result = client.update_product_card(updated)
        return json_safe({"status": "submitted", "nm_id": nm_id, "result": result})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Не удалось обновить карточку WB: {str(exc)[:300]}") from exc


@app.post("/api/v1/products/cards/{nm_id}/media")
def save_wb_product_media(nm_id: int, payload: ProductCardMediaInput) -> dict:
    """Replace all media links in a WB card after explicit confirmation."""
    if nm_id <= 0:
        raise HTTPException(status_code=422, detail="Артикул WB должен быть положительным")
    if not payload.confirmed:
        raise HTTPException(status_code=422, detail="Подтвердите замену медиафайлов в WB")
    urls = [url.strip() for url in payload.urls if url.strip()]
    if any(not url.lower().startswith(("http://", "https://")) for url in urls):
        raise HTTPException(status_code=422, detail="Ссылки на медиафайлы должны начинаться с http:// или https://")
    try:
        result = _wb_write_client().save_product_media(nm_id, urls)
        return json_safe({"status": "submitted", "nm_id": nm_id, "media_count": len(urls), "result": result})
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Не удалось обновить медиа WB: {str(exc)[:300]}") from exc


@app.get("/api/ads")
def get_ads(
    period: str = Query(DEFAULT_PERIOD, pattern="^(today|7d|30d|90d|custom)$"),
    start: date | None = None,
    end: date | None = None,
) -> dict:
    """The "Реклама" screen -- ad KPIs + per-campaign stats for the selected
    period. Same `period`/`start`/`end` contract as /api/overview; no
    previous-period comparison, pages/ads.py draws no trend badges."""
    today_msk = datetime.now(ZoneInfo("Europe/Moscow")).date()
    try:
        window_start, window_end = resolve_window(period, today_msk, start, end)
    except ValueError:
        raise HTTPException(status_code=422, detail="custom_period_requires_start_and_end")

    if table_count("orders") == 0 and table_count("sales") == 0:
        return {
            "period_label": PERIOD_LABELS.get(period, period),
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
            "kpis": [], "campaigns": [], "funnel": [], "creative_tests": [],
            "empty_reason": "no_data_yet",
        }

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


@app.get("/api/v1/dashboard/finance")
def get_dashboard_finance(
    period: str = Query(DEFAULT_PERIOD, pattern="^(today|7d|30d|90d|custom)$"),
    start: date | None = None,
    end: date | None = None,
) -> dict:
    """Expose the same itemised WB financial result used by the legacy page."""
    today_msk = datetime.now(ZoneInfo("Europe/Moscow")).date()
    try:
        window_start, window_end = resolve_window(period, today_msk, start, end)
    except ValueError:
        raise HTTPException(status_code=422, detail="custom_period_requires_start_and_end")

    def build() -> dict:
        data = build_dashboard(window_start, window_end)
        f = data.financial or {}
        articles = [
            ("Разница: выкупы − к перечислению (справочно)", -f.get("sales_to_pay_gap", 0)),
            ("Эквайринг (уже учтён WB, справочно)", -f.get("acquiring", 0)),
            ("Комиссия WB (уже учтена, справочно)", -f.get("commission", 0)),
            ("Логистика", -f.get("logistics", 0)),
            ("Дополнительная логистика", -f.get("rebill_logistics", 0)),
            ("Хранение", -f.get("storage", 0)),
            ("Штрафы", -f.get("penalties", 0)),
            ("Удержания", -f.get("deductions", 0)),
            ("Приёмка", -f.get("acceptance", 0)),
            ("Доплаты WB", f.get("additional_payment", 0)),
            ("Реклама", -f.get("financial_ad_spend", 0)),
            ("Себестоимость", -f.get("cost", 0)),
        ]
        balance = {}
        token = get_token()
        if token:
            try:
                balance = WBAPI(token).get_balance()
            except Exception:
                balance = {}
        return json_safe({
            "period": {"label": PERIOD_LABELS.get(period, period), "start": window_start.isoformat(), "end": window_end.isoformat()},
            "financial": f,
            "expense_rows": [{"label": label, "amount": float(amount or 0)} for label, amount in articles],
            "products": data.financial_products.head(200).to_dict(orient="records") if not data.financial_products.empty else [],
            "balance": balance,
        })

    return _dashboard_response(f"finance:{period}:{window_start}:{window_end}", build)


@app.get("/api/v1/operations/finance/export")
def export_finance_workbook(
    period: str = Query(DEFAULT_PERIOD, pattern="^(today|7d|30d|90d|custom)$"),
    start: date | None = None,
    end: date | None = None,
) -> Response:
    """Download the legacy finance workbook from the redesigned dashboard."""
    today_msk = datetime.now(ZoneInfo("Europe/Moscow")).date()
    try:
        window_start, window_end = resolve_window(period, today_msk, start, end)
    except ValueError:
        raise HTTPException(status_code=422, detail="custom_period_requires_start_and_end")
    data = build_dashboard(window_start, window_end)
    f = data.financial or {}
    expense_rows = pd.DataFrame([
        ["Комиссия WB", -f.get("commission", 0)], ["Эквайринг", -f.get("acquiring", 0)],
        ["Логистика", -f.get("logistics", 0)], ["Дополнительная логистика", -f.get("rebill_logistics", 0)],
        ["Хранение", -f.get("storage", 0)], ["Штрафы", -f.get("penalties", 0)],
        ["Удержания", -f.get("deductions", 0)], ["Приёмка", -f.get("acceptance", 0)],
        ["Доплаты WB", f.get("additional_payment", 0)], ["Реклама", -f.get("financial_ad_spend", 0)],
        ["Себестоимость", -f.get("cost", 0)],
    ], columns=["Статья", "Влияние на результат, ₽"])
    wb_detail = read_table("financial_report")
    if not wb_detail.empty and "operation_date" in wb_detail.columns:
        dates = pd.to_datetime(wb_detail["operation_date"], errors="coerce").dt.date
        wb_detail = wb_detail.loc[(dates >= window_start) & (dates <= window_end)]
    wb_summary = {"gross_sales": f.get("gross_sales", 0), "sale_units": f.get("sale_units", 0), "return_units": f.get("return_units", 0), "net_units": f.get("net_units", 0), "logistics": f.get("logistics", 0), "penalties": f.get("penalties", 0), "acquiring": f.get("acquiring", 0)}
    workbook = build_finance_excel(start=window_start, end=window_end, financial=f, ad_spend=f.get("financial_ad_spend", 0), financial_products=data.financial_products, expense_rows=expense_rows, reconciliation=build_reconciliation(f, wb_summary), wb_detail=wb_detail)
    filename = f"marketshelper-finance-{window_start.isoformat()}-{window_end.isoformat()}.xlsx"
    return Response(content=workbook, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _wb_cost_error(label: str, exc: Exception) -> str:
    import logging
    logging.getLogger(__name__).warning("WB retention report %s failed: %s", label, exc)
    detail = str(exc)
    if "retention_report_too_large" in detail:
        return f"{label}: отчёт слишком большой для одного запуска. WB вернул частичные данные, поэтому сумма не показывается как полная; повторите с меньшим периодом."
    if "429" in detail or "частоту" in detail:
        return f"{label}: WB ограничил частоту запросов. Повторите через минуту."
    if "401" in detail or "403" in detail:
        return f"{label}: проверьте доступ токена WB к категории «Аналитика» в настройках."
    return f"{label}: не удалось загрузить расшифровку WB. Попробуйте обновить позже. Основной финансовый отчёт доступен ниже."


def _customer_quality_error(label: str, exc: Exception) -> str:
    """Turn provider failures into actionable, non-sensitive UI messages."""
    import logging
    logging.getLogger(__name__).warning("WB customer quality section %s failed (%s)", label, type(exc).__name__)
    detail = str(exc)
    if "401" in detail:
        return f"{label}: токен WB отклонён, переподключите его в настройках."
    if "403" in detail:
        scope = {
            "rating": "Отзывы/Вопросы",
            "quarantine": "Цены и скидки",
        }.get(label, "нужную категорию")
        return f"{label}: у токена нет доступа к категории «{scope}»."
    if "404" in detail:
        return f"{label}: WB временно не предоставляет этот раздел для текущего кабинета."
    if "400" in detail:
        return f"{label}: WB отклонил параметры запроса. Повторите после обновления данных."
    if "429" in detail or "частот" in detail:
        return f"{label}: WB ограничил частоту запросов. Повторите позже."
    return f"{label}: данные WB временно недоступны. Повторите позже."


def _wb_paid_storage_job_key(token: str, start: date, end: date) -> str:
    token_fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    return f"{token_fingerprint}:{start.isoformat()}:{end.isoformat()}"


def _start_wb_paid_storage_job(token: str, start: date, end: date) -> dict[str, object]:
    """Start one idempotent paid-storage generation without blocking a dashboard request."""
    key = _wb_paid_storage_job_key(token, start, end)
    with _wb_paid_storage_lock:
        existing = _wb_paid_storage_jobs.get(key)
        if existing is not None:
            return dict(existing)
        state: dict[str, object] = {"status": "processing", "rows": [], "error": ""}
        _wb_paid_storage_jobs[key] = state

    def worker() -> None:
        try:
            rows = WBAPI(token, timeout=15).get_paid_storage_report(start.isoformat(), end.isoformat())
            result: dict[str, object] = {"status": "ready", "rows": rows, "error": ""}
        except Exception as exc:  # provider failures are surfaced as a user-facing status
            result = {"status": "error", "rows": [], "error": _wb_cost_error("Платное хранение", exc)}
        with _wb_paid_storage_lock:
            _wb_paid_storage_jobs[key] = result

    _wb_paid_storage_executor.submit(worker)
    return dict(state)


def _compact_wb_cost_rows(rows: list[dict]) -> list[dict]:
    """Keep dashboard payloads small while preserving numeric report fields."""
    compact: list[dict] = []
    for row in rows:
        item = {key: value for key, value in row.items() if key not in {"photoUrls", "photo_urls", "photos"}}
        compact.append(item)
    return compact


def _wb_cost_job_key(token: str, start: date, end: date) -> str:
    return f"{_wb_paid_storage_job_key(token, start, end)}:all"


def _start_wb_cost_job(token: str, start: date, end: date) -> dict[str, object]:
    """Build all WB cost sections in the background.

    Retention endpoints can paginate through thousands of rows and must not
    run on the request thread, even when only the paid-storage part is
    configured as background work by the UI.
    """
    key = _wb_cost_job_key(token, start, end)
    with _wb_paid_storage_lock:
        existing = _wb_cost_jobs.get(key)
        if existing is not None:
            return dict(existing)
        state: dict[str, object] = {
            "status": "processing",
            "deductions": [],
            "measurement_penalties": [],
            "paid_storage": [],
            "paid_storage_status": "processing",
            "errors": [],
        }
        _wb_cost_jobs[key] = state

    def worker() -> None:
        client = WBAPI(token, timeout=15)
        deductions: list[dict] = []
        measurement_penalties: list[dict] = []
        paid_storage: list[dict] = []
        errors: list[str] = []
        try:
            deductions = _compact_wb_cost_rows(client.get_deductions(start.isoformat(), end.isoformat()))
        except Exception as exc:
            errors.append(_wb_cost_error("Подмены", exc))
        try:
            measurement_penalties = _compact_wb_cost_rows(client.get_measurement_penalties(start.isoformat(), end.isoformat()))
        except Exception as exc:
            errors.append(_wb_cost_error("Габариты", exc))
        paid_storage_status = "ready"
        try:
            paid_storage = _compact_wb_cost_rows(client.get_paid_storage_report(start.isoformat(), end.isoformat()))
        except Exception as exc:
            paid_storage_status = "error"
            errors.append(_wb_cost_error("Платное хранение", exc))
        result: dict[str, object] = {
            "status": "ready",
            "deductions": deductions,
            "measurement_penalties": measurement_penalties,
            "paid_storage": paid_storage,
            "paid_storage_status": paid_storage_status,
            "errors": errors,
        }
        with _wb_paid_storage_lock:
            _wb_cost_jobs[key] = result

    _wb_paid_storage_executor.submit(worker)
    return dict(state)


@app.get("/api/v1/dashboard/finance/wb-costs")
def get_wb_cost_reports(period: str = DEFAULT_PERIOD, start: date | None = None, end: date | None = None, background: bool = False) -> dict:
    token = get_token()
    if not token:
        raise HTTPException(status_code=409, detail="WB token не подключён")
    try:
        start, end = resolve_window(period, datetime.now(ZoneInfo("Europe/Moscow")).date(), start, end)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if background:
        job = _start_wb_cost_job(token, start, end)
        response = {
            "period": {"start": start.isoformat(), "end": end.isoformat()},
            "deductions": job.get("deductions", []),
            "measurement_penalties": job.get("measurement_penalties", []),
            "paid_storage": job.get("paid_storage", []),
            "paid_storage_status": job.get("paid_storage_status", "processing"),
            "errors": job.get("errors", []) or (["Финансовый отчёт WB формируется в фоне; обновите раздел через несколько секунд."] if job.get("status") == "processing" else []),
        }
        return json_safe(response)

    def build():
        client = WBAPI(token, timeout=15)
        deductions, measurement_penalties, paid_storage, errors = [], [], [], []
        try:
            deductions = _compact_wb_cost_rows(client.get_deductions(start.isoformat(), end.isoformat()))
        except Exception as exc:
            errors.append(_wb_cost_error("Подмены", exc))
        try:
            measurement_penalties = _compact_wb_cost_rows(client.get_measurement_penalties(start.isoformat(), end.isoformat()))
        except Exception as exc:
            errors.append(_wb_cost_error("Габариты", exc))
        paid_storage_status = "disabled"
        if background:
            job = _start_wb_paid_storage_job(token, start, end)
            paid_storage_status = str(job.get("status", "processing"))
            if paid_storage_status == "ready":
                paid_storage = _compact_wb_cost_rows(job.get("rows", []) if isinstance(job.get("rows"), list) else [])
            elif paid_storage_status == "error":
                errors.append(str(job.get("error") or "Платное хранение: данные WB временно недоступны."))
            else:
                errors.append("Платное хранение: отчёт формируется в фоне; обновите раздел через несколько секунд.")
        else:
            paid_storage_getter = getattr(client, "get_paid_storage_report", None)
            if callable(paid_storage_getter):
                try:
                    paid_storage = _compact_wb_cost_rows(paid_storage_getter(start.isoformat(), end.isoformat()))
                    paid_storage_status = "ready"
                except Exception as exc:
                    errors.append(_wb_cost_error("Платное хранение", exc))
                    paid_storage_status = "error"
        return json_safe({"period": {"start": start.isoformat(), "end": end.isoformat()}, "deductions": deductions, "measurement_penalties": measurement_penalties, "paid_storage": paid_storage, "paid_storage_status": paid_storage_status, "errors": errors})

    return wb_cost_response_cache.get_or_build(f"{hash(token)}:{start}:{end}", build)


@app.get("/api/v1/dashboard/customer-quality")
def get_customer_quality() -> dict:
    """Expose seller reputation, unread feedbacks and WB price quarantine."""
    token = get_token()
    if not token:
        raise HTTPException(status_code=409, detail="WB token не подключён")
    global customer_quality_cache
    if customer_quality_cache and customer_quality_cache[0] > monotonic():
        return customer_quality_cache[1]
    client = WBAPI(token, timeout=15)
    rating: dict = {}
    unread: dict = {}
    quarantine: list[dict] = []
    claims: dict = {}
    return_history: list[dict] = []
    chats: list[dict] = []
    feedbacks: list[dict] = []
    questions: list[dict] = []
    errors: list[str] = []
    for label, getter in (("rating", client.get_seller_rating), ("unread", client.get_new_feedbacks_questions), ("feedbacks", client.get_feedbacks), ("questions", client.get_questions), ("quarantine", client.get_quarantine_goods), ("returns", client.get_returns_claims), ("return_history", lambda: client.get_goods_returns((datetime.now(ZoneInfo("Europe/Moscow")).date() - timedelta(days=30)).isoformat(), datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat())), ("chats", client.get_seller_chats)):
        try:
            value = getter()
            if label == "rating": rating = value if isinstance(value, dict) else {}
            elif label == "unread": unread = value if isinstance(value, dict) else {}
            elif label == "quarantine": quarantine = value if isinstance(value, list) else []
            elif label == "feedbacks": feedbacks = value if isinstance(value, list) else []
            elif label == "questions": questions = value if isinstance(value, list) else []
            elif label == "return_history": return_history = value if isinstance(value, list) else []
            elif label == "chats": chats = value if isinstance(value, list) else []
            else: claims = value if isinstance(value, dict) else {}
        except Exception as exc:
            errors.append(_customer_quality_error(label, exc))
    payload = json_safe({"rating": rating, "unread": unread, "feedbacks": feedbacks, "questions": questions, "quarantine": quarantine, "returns": claims, "return_history": return_history, "chats": chats, "errors": errors})
    customer_quality_cache = (monotonic() + 60.0, payload)
    return payload


@app.post("/api/v1/customer-quality/feedbacks/answer")
def answer_customer_feedback(payload: FeedbackAnswerInput) -> dict:
    token = get_token()
    if not token: raise HTTPException(status_code=409, detail="WB token не подключён")
    try: return json_safe(WBAPI(token).answer_feedback(payload.id, payload.text, payload.edit))
    except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Не удалось ответить на отзыв WB: {str(exc)[:240]}") from exc


@app.post("/api/v1/customer-quality/questions/answer")
def answer_customer_question(payload: QuestionAnswerInput) -> dict:
    token = get_token()
    if not token: raise HTTPException(status_code=409, detail="WB token не подключён")
    try: return json_safe(WBAPI(token).answer_question(payload.id, payload.text, payload.state))
    except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Не удалось ответить на вопрос WB: {str(exc)[:240]}") from exc


@app.post("/api/v1/customer-quality/returns/process")
def process_customer_return(payload: ReturnClaimInput) -> dict:
    token = get_token()
    if not token: raise HTTPException(status_code=409, detail="WB token не подключён")
    try: return json_safe(WBAPI(token).process_return_claim(payload.id, payload.action))
    except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Не удалось обработать возврат WB: {str(exc)[:240]}") from exc


@app.post("/api/v1/customer-quality/chats/message")
def send_customer_chat_message(payload: ChatMessageInput) -> dict:
    token = get_token()
    if not token: raise HTTPException(status_code=409, detail="WB token не подключён")
    try: return json_safe(WBAPI(token).send_seller_message(payload.reply_sign, payload.text))
    except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Не удалось отправить сообщение в чат WB: {str(exc)[:240]}") from exc


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
        item = conn.execute("SELECT * FROM redesign_action_plan WHERE id = ?", (item_id,)).fetchone()
    # Creating a plan item must not execute it implicitly. The plan is the
    # durable queue; execution starts only when the user starts the item.
    return json_safe({"id": str(item_id), "status": "created", "item": _plan_row(item)})


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
            if current["status"] in {"done", "cancelled"}:
                raise HTTPException(status_code=409, detail="Задача уже завершена или отменена. Обновите план задач.")
            if changes["status"] == "done" and not str(changes.get("result") or current["result"] or "").strip():
                raise HTTPException(status_code=422, detail="Укажите результат выполнения задачи")
            if changes["status"] in {"in_progress", "done"} and not current["started_at"]:
                changes["started_at"] = now
            if changes["status"] in {"done", "cancelled"}:
                changes["completed_at"] = now
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
    rows: list[dict] = []
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        for task in payload.tasks:
            item = task.model_dump()
            article = str(item.get("supplier_article") or "").strip()
            if not article:
                raise HTTPException(status_code=422, detail="Укажите артикул производственного товара")
            if not item.get("task_date"):
                raise HTTPException(status_code=422, detail="Укажите дату смены")
            item["task_type"] = "production"
            if int(item.get("nm_id") or 0) <= 0:
                match = conn.execute(
                    """
                    SELECT p.nm_id,p.supplier_article,COALESCE(c.product_name,'') AS product_name
                      FROM production_settings p
                      LEFT JOIN products_catalog c ON c.nm_id=p.nm_id
                     WHERE LOWER(TRIM(COALESCE(p.supplier_article,'')))=LOWER(TRIM(?))
                     LIMIT 1
                    """,
                    (article,),
                ).fetchone()
                if match is None:
                    raise HTTPException(status_code=422, detail=f"Артикул «{article}» отсутствует в нормах производства")
                item["nm_id"] = int(match["nm_id"])
                item["supplier_article"] = str(match["supplier_article"] or article)
                if not str(item.get("product_name") or "").strip():
                    item["product_name"] = str(match["product_name"] or article)
            rows.append(item)
    try:
        save_execution_tasks(pd.DataFrame(rows))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"saved": len(payload.tasks), "status": "Не начато"}


@app.get("/api/v1/operations/production/execution")
def get_production_execution() -> dict:
    tasks = read_table("execution_tasks")
    if not tasks.empty:
        task_types = tasks["task_type"].astype(str).str.strip().str.casefold()
        tasks = tasks.loc[task_types.isin({"production", "производство"})].copy()
        tasks = tasks.loc[~tasks["status"].astype(str).str.strip().str.casefold().eq("закрыто")].copy()
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
    return json_safe({"definitions": store.list_definitions(enabled_only=False), "runs": runs, "metric_labels": METRIC_LABELS})


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


@app.patch("/api/v1/operations/reports/definitions/{definition_id}")
def update_report_definition(definition_id: int, payload: ReportDefinitionInput) -> dict:
    name = payload.name.strip()
    if not name or not payload.metrics or any(code not in METRIC_LABELS for code in payload.metrics):
        raise HTTPException(status_code=422, detail="Укажите название и допустимые метрики отчёта")
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
    try:
        store.update_definition(definition_id, name=name, metrics=payload.metrics,
            schedule_type=payload.schedule_type, schedule_time=f"{hour:02d}:{minute:02d}",
            schedule_weekday=payload.schedule_weekday, schedule_day=payload.schedule_day,
            email_enabled=payload.email_enabled, telegram_enabled=payload.telegram_enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Расписание не найдено") from exc
    return {"id": definition_id, "status": "updated"}


@app.patch("/api/v1/operations/reports/definitions/{definition_id}/enabled")
def toggle_report_definition(definition_id: int, payload: ReportEnabledInput) -> dict:
    store = ReportStore(DB_PATH)
    try:
        store.set_enabled(definition_id, payload.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Расписание не найдено") from exc
    return {"id": definition_id, "enabled": payload.enabled, "status": "updated"}


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


def _catalog_chrt_ids() -> list[int]:
    ids: set[int] = set()
    catalog = read_table("products_catalog")
    if catalog.empty or "raw_json" not in catalog.columns:
        return []
    for raw in catalog["raw_json"].tolist():
        try:
            card = json.loads(str(raw or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        for size in card.get("sizes") or []:
            if not isinstance(size, dict):
                continue
            for key in ("chrtID", "chrtId", "chrt_id"):
                try:
                    value = int(size.get(key) or 0)
                except (TypeError, ValueError):
                    value = 0
                if value > 0:
                    ids.add(value)
    return sorted(ids)[:1000]


@app.get("/api/v1/operations/stock/seller-warehouses")
def get_seller_warehouse_stock() -> dict:
    """Read seller-owned FBS/DBS warehouses and their current quantities."""
    token = get_token()
    if not token:
        return {"warehouses": [], "errors": {"connection": "Подключите токен WB с категорией «Маркетплейс»."}}
    def build() -> dict:
        client = WBAPI(token)
        try:
            warehouses = client.get_seller_warehouses()
        except Exception as exc:
            return {"warehouses": [], "errors": {"warehouses": _wb_cost_error("Склады продавца", exc)}}
        chrt_ids = _catalog_chrt_ids()
        output: list[dict] = []
        errors: dict[str, str] = {}
        for warehouse in warehouses[:50]:
            warehouse_id = warehouse.get("id") or warehouse.get("warehouseId")
            try:
                warehouse_id = int(warehouse_id or 0)
            except (TypeError, ValueError):
                warehouse_id = 0
            row = {"id": warehouse_id, "name": warehouse.get("name") or f"Склад продавца #{warehouse_id}", "office_id": warehouse.get("officeId"), "processing": bool(warehouse.get("isProcessing")), "stocks": [], "quantity": 0}
            if warehouse_id > 0 and chrt_ids:
                try:
                    stocks = client.get_seller_warehouse_inventory(warehouse_id, chrt_ids)
                    row["stocks"] = stocks
                    row["quantity"] = sum(int(item.get("amount") or item.get("quantity") or 0) for item in stocks)
                except Exception as exc:
                    errors[str(warehouse_id)] = _wb_cost_error(str(row["name"]), exc)
            elif not chrt_ids:
                errors["inventory"] = "Размеры товаров ещё не загружены. Запустите синхронизацию карточек WB."
            output.append(row)
        return json_safe({"warehouses": output, "errors": errors})
    return _dashboard_response("seller-warehouse-stock", build)


def _sales_funnel_summary(rows: list[dict]) -> dict:
    """Normalize WB v3 funnel rows into one stable dashboard payload."""
    totals = {key: 0.0 for key in ("open_card", "cart", "orders", "order_sum", "buyouts", "buyout_sum", "wishlist")}
    products: list[dict] = []
    for row in rows:
        product = row.get("product") if isinstance(row.get("product"), dict) else {}
        statistic = row.get("statistic") if isinstance(row.get("statistic"), dict) else {}
        selected = statistic.get("selected") if isinstance(statistic.get("selected"), dict) else statistic
        selected = selected.get("period") if isinstance(selected.get("period"), dict) else selected
        values = {
            "open_card": selected.get("openCard", selected.get("open_card", 0)),
            "cart": selected.get("cartCount", selected.get("cart", 0)),
            "orders": selected.get("orderCount", selected.get("orders", 0)),
            "order_sum": selected.get("orderSum", selected.get("order_sum", 0)),
            "buyouts": selected.get("buyoutCount", selected.get("buyouts", 0)),
            "buyout_sum": selected.get("buyoutSum", selected.get("buyout_sum", 0)),
            "wishlist": selected.get("addToWishlistCount", selected.get("wishlist", 0)),
        }
        clean = {}
        for key, value in values.items():
            try:
                clean[key] = max(0.0, float(value or 0))
            except (TypeError, ValueError):
                clean[key] = 0.0
            totals[key] += clean[key]
        nm_id = product.get("nmId", product.get("nmID"))
        products.append({"nm_id": nm_id, "name": product.get("title") or product.get("name") or "Товар", **clean})
    def integer(value: float) -> int | float:
        return int(value) if float(value).is_integer() else round(value, 2)
    return {
        "stages": [
            {"key": "open_card", "label": "Переходы в карточку", "value": integer(totals["open_card"])},
            {"key": "cart", "label": "Добавили в корзину", "value": integer(totals["cart"])},
            {"key": "orders", "label": "Заказы", "value": integer(totals["orders"])},
            {"key": "buyouts", "label": "Выкупы", "value": integer(totals["buyouts"])},
        ],
        "totals": {key: integer(value) for key, value in totals.items()},
        "products": sorted(products, key=lambda item: item["open_card"], reverse=True)[:20],
        "source": "WB Analytics v3",
    }


@app.get("/api/v1/dashboard/analytics")
def get_dashboard_analytics(
    period: str = Query(DEFAULT_PERIOD, pattern="^(today|7d|30d|90d|custom)$"),
    start: date | None = None,
    end: date | None = None,
) -> dict:
    """Expose WB sales funnel and locally retained stock history."""
    today_msk = datetime.now(ZoneInfo("Europe/Moscow")).date()
    try:
        window_start, window_end = resolve_window(period, today_msk, start, end)
    except ValueError:
        raise HTTPException(status_code=422, detail="custom_period_requires_start_and_end")

    def build() -> dict:
        errors: dict[str, str] = {}
        funnel = {"stages": [], "totals": {}, "products": [], "source": "WB Analytics v3"}
        token = get_token()
        if token and not IS_DEMO:
            try:
                funnel = _sales_funnel_summary(WBAPI(token).get_sales_funnel_products(window_start.isoformat(), window_end.isoformat()))
            except Exception as exc:
                errors["sales_funnel"] = _wb_cost_error("Воронка продаж", exc)
        elif not token:
            errors["sales_funnel"] = "Подключите токен WB с категорией «Аналитика»."

        stocks = read_table("stocks")
        history: list[dict] = []
        if not stocks.empty and "snapshot_at" in stocks.columns:
            frame = stocks.copy()
            frame["date"] = pd.to_datetime(frame["snapshot_at"], errors="coerce").dt.date
            frame["quantity"] = pd.to_numeric(frame.get("quantity", 0), errors="coerce").fillna(0)
            frame = frame.dropna(subset=["date"])
            for day, group in frame.groupby("date"):
                history.append({"date": day.isoformat(), "quantity": int(round(float(group["quantity"].sum()))), "rows": int(len(group))})
            history = sorted(history, key=lambda row: row["date"])[-90:]
        return json_safe({
            "period": {"start": window_start.isoformat(), "end": window_end.isoformat()},
            "sales_funnel": funnel,
            "stock_history": history,
            "errors": errors,
            "partial": bool(errors),
        })

    return _dashboard_response(f"dashboard-analytics:{period}:{window_start}:{window_end}", build)


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
    today_msk = datetime.now(ZoneInfo("Europe/Moscow")).date()
    try:
        window_start, window_end = resolve_window(period, today_msk, start, end)
    except ValueError:
        raise HTTPException(status_code=422, detail="custom_period_requires_start_and_end")

    if table_count("orders") == 0 and table_count("sales") == 0:
        return {
            "period_label": PERIOD_LABELS.get(period, period),
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
            "metrics": [], "campaigns": [], "funnel": [], "creative_tests": [],
            "empty_reason": "no_data_yet",
        }

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
        execution = read_table("execution_tasks")
        if not execution.empty:
            task_types = execution["task_type"].astype(str).str.strip().str.casefold()
            execution = execution.loc[task_types.isin({"production", "производство"})].copy()
            execution = execution.loc[~execution["status"].astype(str).str.strip().str.casefold().eq("закрыто")].copy()
        tasks = _records(execution)
        settings = _records(read_table("production_settings"))
        shift_tasks = [task for task in (shift.get("tasks", []) or []) if str(task.get("status") or "").strip().casefold() != "закрыто"]
        saved_tasks = shift_tasks or tasks
        recommendations = shift.get("recommendations", []) or []
        visible_tasks = saved_tasks or recommendations
        if saved_tasks:
            planned = sum(int(task.get("planned_units", task.get("planned", 0)) or 0) for task in saved_tasks)
            covered = _production_material_covered(saved_tasks)
        else:
            planned = shift.get("planned", 0) or sum(int(task.get("planned", 0) or 0) for task in recommendations)
            covered = sum(
                int(task.get("planned", 0) or 0)
                for task in recommendations
                if task.get("status") != "Заблокировано сырьём"
            )
        stages, materials = _production_panels()
        task_word = "задача" if len(saved_tasks) == 1 else "задачи" if 2 <= len(saved_tasks) <= 4 else "задач"
        stages[-1] = {
            "label": "Смена",
            "value": f"{len(saved_tasks)} {task_word} в открытой смене" if saved_tasks else "нет открытых заданий",
            "status": "active" if saved_tasks else "pending",
        }
        return json_safe({"metrics": [
            {"key": "planned", "label": "План смены" if saved_tasks else "К запуску", "value": planned, "note": "компл."},
            {"key": "covered", "label": "Материал подтверждён", "value": covered, "note": "компл."},
            {"key": "configured", "label": "Настроено SKU", "value": len(settings), "note": "параметры производства"},
        ], "stages": stages, "tasks": visible_tasks, "materials": materials or material.get("rows", [])})

    return _dashboard_response("dashboard-production", build)


@app.get("/api/v1/dashboard/supply")
def get_dashboard_supply() -> dict:
    def build() -> dict:
        today, _ = _today_dashboard()
        procurement = today.get("tabs", {}).get("procurement", {})
        orders = procurement.get("active_orders", []) or _records(read_procurement_orders())
        suppliers = read_suppliers(active_only=True)
        wb_supplies = []
        wb_new_orders = []
        acceptance_coefficients = []
        transit_tariffs = []
        errors: dict[str, str] = {}
        token = get_token()
        if token:
            try:
                client = WBAPI(token)
                wb_supplies = client.get_fbs_supplies()
            except Exception as exc:
                wb_supplies = []
                errors["fbs_supplies"] = _customer_quality_error("FBS-поставки", exc)
            try:
                wb_new_orders = client.get_fbs_new_orders()
            except Exception as exc:
                wb_new_orders = []
                errors["fbs_new_orders"] = _customer_quality_error("Новые сборочные задания", exc)
            try:
                acceptance_coefficients = client.get_acceptance_coefficients()
            except Exception as exc:
                acceptance_coefficients = []
                errors["acceptance_coefficients"] = _customer_quality_error("Коэффициенты приёмки", exc)
            try:
                transit_tariffs = client.get_transit_tariffs()
            except Exception as exc:
                transit_tariffs = []
                errors["transit_tariffs"] = _customer_quality_error("Тарифы транзита", exc)
        elif not token:
            errors["connection"] = "Подключите WB-токен в настройках, чтобы загрузить данные поставок."
        return json_safe({"metrics": [
            {"key": "active", "label": "Активные закупки", "value": len(orders), "note": "не закрыты"},
            {"key": "to_buy", "label": "Товары к заказу", "value": len(procurement.get("products_to_buy", [])), "note": "расчёт WB"},
        ], "shipments": orders, "wb_supplies": wb_supplies, "wb_new_orders": wb_new_orders, "acceptance_coefficients": acceptance_coefficients, "transit_tariffs": transit_tariffs, "errors": errors, "suppliers": _records(suppliers)})

    return _dashboard_response("dashboard-supply", build)


def _fbs_supply_id(value: str) -> str:
    supply_id = value.strip()
    if not supply_id or len(supply_id) > 80 or not re.fullmatch(r"[A-Za-z0-9_-]+", supply_id):
        raise HTTPException(status_code=422, detail="Некорректный идентификатор поставки WB")
    return supply_id


@app.get("/api/v1/operations/supply/fbs/{supply_id}")
def get_fbs_supply_details(supply_id: str) -> dict:
    token = get_token()
    if not token:
        raise HTTPException(status_code=409, detail="WB token не подключён")
    supply_id = _fbs_supply_id(supply_id)
    try:
        return json_safe(WBAPI(token, timeout=15).get_fbs_supply_details(supply_id))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=_customer_quality_error("FBS-поставка", exc)) from exc


@app.get("/api/v1/operations/supply/fbs/{supply_id}/barcode")
def get_fbs_supply_barcode(supply_id: str, barcode_type: str = Query("png", alias="type")) -> dict:
    token = get_token()
    if not token:
        raise HTTPException(status_code=409, detail="WB token не подключён")
    supply_id = _fbs_supply_id(supply_id)
    if barcode_type not in {"png", "svg"}:
        raise HTTPException(status_code=422, detail="Тип QR-кода должен быть png или svg")
    try:
        return json_safe(WBAPI(token, timeout=15).get_fbs_supply_barcode(supply_id, barcode_type))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=_customer_quality_error("QR поставки FBS", exc)) from exc


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


@app.get("/api/v1/settings/wb-token")
def get_wb_token_status() -> dict:
    """Return token scope metadata without returning or revalidating the token."""
    token = get_token()
    if not token:
        return {"connected": False, "scopes": [], "missing_scopes": [], "scope_metadata_available": False, "read_only": False, "expires_at": None}
    info = WBAPI(token).token_scope_info()
    all_scope_labels = list(getattr(WBAPI, "TOKEN_SCOPES", {}).values())
    if not info.get("known"):
        return {"connected": True, "scopes": ["Статистика"], "missing_scopes": [label for label in all_scope_labels if label != "Статистика"], "scope_metadata_available": False, "read_only": False, "expires_at": None}
    scopes = [str(item) for item in info.get("scopes", []) if str(item) in all_scope_labels]
    return {"connected": True, "scopes": scopes, "missing_scopes": [label for label in all_scope_labels if label not in scopes], "scope_metadata_available": True, "read_only": bool(info.get("read_only")), "expires_at": info.get("expires_at")}


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


@app.post("/api/v1/settings/wb-token")
def connect_wb_token(payload: WBTokenInput) -> dict:
    """Validate and securely store a WB token without ever returning it."""
    token = payload.token.strip()
    if not token:
        raise HTTPException(status_code=422, detail="Укажите WB-токен")
    try:
        client = WBAPI(token, timeout=10)
        if not client.test_statistics():
            raise RuntimeError("WB не подтвердил доступ к статистике")
        scope_info = client.token_scope_info() if hasattr(client, "token_scope_info") else {"known": False}
        expires_at = scope_info.get("expires_at") if isinstance(scope_info, dict) else None
        if expires_at is not None and int(expires_at) <= int(datetime.now(timezone.utc).timestamp()):
            raise RuntimeError("WB-токен истёк")
    except Exception as exc:
        # Keep provider details out of the response and never persist an
        # unvalidated credential.  The raw exception is intentionally not
        # logged because some providers echo request data in error bodies.
        raise HTTPException(status_code=422, detail="WB-токен не прошёл проверку. Проверьте токен и права категории «Статистика».") from exc
    scope_info = scope_info if isinstance(scope_info, dict) else {"known": False}
    all_scope_labels = list(getattr(WBAPI, "TOKEN_SCOPES", {}).values()) or [
        "Контент", "Аналитика", "Цены и скидки", "Маркетплейс", "Статистика", "Продвижение",
        "Отзывы/Вопросы", "Чат с покупателями", "Поставки", "Возвраты", "Документы", "Финансы",
    ]
    scopes = [str(item) for item in scope_info.get("scopes", []) if str(item) in all_scope_labels]
    if "Статистика" not in scopes:
        scopes.append("Статистика")
    missing_scopes = [label for label in all_scope_labels if label not in scopes]
    if scope_info.get("known"):
        missing_scopes = [str(item) for item in scope_info.get("missing_scopes", []) if str(item) in all_scope_labels and str(item) not in scopes]
    save_token(token)
    global customer_quality_cache
    customer_quality_cache = None
    return {
        "connected": True,
        "scopes": scopes,
        "missing_scopes": missing_scopes,
        "scope_metadata_available": bool(scope_info.get("known")),
        "read_only": bool(scope_info.get("read_only")),
        "expires_at": scope_info.get("expires_at"),
    }


@app.delete("/api/v1/settings/wb-token")
def disconnect_wb_token() -> dict:
    delete_token()
    global customer_quality_cache
    customer_quality_cache = None
    return {"connected": False, "scopes": [], "missing_scopes": []}


@app.post("/api/v1/settings/ozon")
def connect_ozon_credentials(payload: OzonCredentialsInput) -> dict:
    try:
        save_ozon_credentials(payload.client_id, payload.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Укажите корректные Client-Id и Api-Key Ozon") from exc
    return {"connected": True}


@app.delete("/api/v1/settings/ozon")
def disconnect_ozon_credentials() -> dict:
    delete_ozon_credentials()
    return {"connected": False}


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


@app.get("/api/v1/settings/manual-expenses")
def get_manual_expenses(
    nm_id: int | None = Query(default=None, gt=0),
    start: date | None = None,
    end: date | None = None,
) -> dict:
    if start and end and end < start:
        raise HTTPException(status_code=422, detail="end must be on or after start")
    return {"items": read_manual_expenses(nm_id=nm_id, start=start, end=end)}


@app.post("/api/v1/settings/manual-expenses")
def create_manual_expense(payload: ManualExpenseInput) -> dict:
    try:
        expense_id = save_manual_expense(
            nm_id=payload.nm_id,
            article=payload.article,
            amount_rub=payload.amount_rub,
            allocation_basis=payload.allocation_basis,
            source=payload.source,
            period_start=payload.period_start.isoformat(),
            period_end=payload.period_end.isoformat(),
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": expense_id, "message": "Расход сохранён"}


@app.delete("/api/v1/settings/manual-expenses/{expense_id}")
def remove_manual_expense(expense_id: int) -> dict:
    if not delete_manual_expense(expense_id):
        raise HTTPException(status_code=404, detail="Расход не найден")
    return {"message": "Расход удалён"}


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
