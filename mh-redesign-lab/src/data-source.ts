import { useCallback, useEffect, useRef, useState } from "react";
import type {
  ActionRecommendation,
  AdCampaign,
  AdsHero,
  AssistantAutomation,
  AgentCandidate,
  CreativeSignal,
  CashWindow,
  DashboardMetric,
  DashboardSync,
  ExpenseSplit,
  ExportItem,
  FinanceLedgerItem,
  FunnelStage,
  IconKey,
  InventoryWatchRow,
  MaterialNeed,
  NavItem,
  PageId,
  PageMeta,
  ProductionStage,
  ProductionTask,
  ReportCard,
  ReorderItem,
  SupplierPulse,
  SupplyShipment,
  StockCoverageRow,
  StockHero,
  StockRule,
  Tone,
} from "./contracts";
import * as mock from "./mock-dashboard";

type ConnectionMode = "loading" | "live" | "stale" | "fallback" | "mock";

type DashboardConnection = {
  mode: ConnectionMode;
  label: string;
  detail: string;
};

export type DashboardViewModel = {
  dataWarning?: string;
  connection: DashboardConnection;
  navItems: NavItem[];
  pageMeta: Record<PageId, PageMeta>;
  mobileNav: PageId[];
  sync: DashboardSync;
  todayMetrics: DashboardMetric[];
  stockMetrics: DashboardMetric[];
  adsMetrics: DashboardMetric[];
  actions: ActionRecommendation[];
  inventory: InventoryWatchRow[];
  reorderPlan: ReorderItem[];
  stockHero: StockHero;
  stockCoverage: StockCoverageRow[];
  stockRules: StockRule[];
  adsHero: AdsHero;
  campaigns: AdCampaign[];
  adsFunnel: FunnelStage[];
  creativeSignals: CreativeSignal[];
  financeMetrics: DashboardMetric[];
  financeLedger: FinanceLedgerItem[];
  expenseSplit: ExpenseSplit[];
  cashWindows: CashWindow[];
  productionMetrics: DashboardMetric[];
  productionStages: ProductionStage[];
  productionTasks: ProductionTask[];
  materialNeeds: MaterialNeed[];
  supplyMetrics: DashboardMetric[];
  supplyShipments: SupplyShipment[];
  supplierPulse: SupplierPulse[];
  assistantMetrics: DashboardMetric[];
  automations: AssistantAutomation[];
  agentCandidates: AgentCandidate[];
  agentHistory: AgentCandidate[];
  reportMetrics: DashboardMetric[];
  reportCards: ReportCard[];
  exportItems: ExportItem[];
  chartBars: number[];
  chartBarsWeek: number[];
  productEconomics: ProductEconomicsRow[];
  dataQuality: DataQualityData | null;
  procurementOrders: Array<Record<string, unknown>>;
};

export type DataQualityData = { summary: Record<string, unknown>; checks: Array<Record<string, unknown>>; reliability: Array<Record<string, unknown>>; fifo_guard: Record<string, unknown> };
export type ReportDefinition = { id: number; name: string; metrics: string; schedule_type: "daily" | "weekly" | "monthly"; schedule_time: string; schedule_weekday: number | null; schedule_day: number | null; enabled: number; email_enabled?: number; telegram_enabled?: number; last_run_at: string | null };
export type ReportRun = { id: number; definition_id: number; definition_name?: string; definition_metrics?: string; metric_count?: number; started_at: string; finished_at: string | null; status: string; file_path: string | null; file_available?: boolean; error: string | null; delivery_requested?: number; delivery_status?: string; delivery_error?: string | null };
export type ReportOperations = { definitions: ReportDefinition[]; runs: ReportRun[]; metric_labels: Record<string, string> };
export type ReportDelivery = { configured: boolean; email_configured: boolean; email?: string; telegram_configured?: boolean; telegram_linked?: boolean; telegram_bot_username?: string };
export type ProductionExecutionTask = { task_key: string; task_type: string; task_date: string | null; stage: string; nm_id: number; supplier_article: string; product_name: string; planned_units: number; actual_units: number; status: string; note: string };
export type IncidentOperations = { cases: Array<Record<string, unknown>>; summary: Record<string, number> };

export type ProductEconomicsRow = {
  nmId: string;
  article: string;
  product: string;
  orders: number;
  sales: number;
  revenue: number;
  buyout: number;
  adSpend: number;
  drr: number;
  stock: number;
  dailySales: number;
  daysOfStock: number | null;
  unitCost: number;
  margin: number;
};

export type DashboardDataState = DashboardViewModel & {
  refreshing: boolean;
  refresh: () => Promise<void>;
};

export type DashboardPeriodId = "today" | "7d" | "30d" | "90d" | "custom";

export type DashboardPeriod = {
  id: DashboardPeriodId;
  start?: string;
  end?: string;
};

export type FifoReconciliationData = {
  preview: Array<Record<string, unknown>>;
  status: Record<string, unknown>;
  guard: Record<string, unknown>;
  wb_context: Record<string, unknown>;
  runs: Array<Record<string, unknown>>;
};

export type TenantSettings = { sync_interval_minutes: number; initial_history_days: number; agent_interval_minutes: number; wb_connected: boolean; ozon_connected: boolean };
export type ProductCostSetting = { nm_id: number; supplier_article: string; product_name: string; cost_per_wb_unit: number; note: string; updated_at: string };
export type ProductionNorm = { nm_id: number; supplier_article: string; product_name: string; enabled: number; material_per_unit: number; min_batch: number; pack_size: number; blank_type: string };
export type MaterialInventory = { material_key?: string; material_name: string; balance_known: number | boolean; full_rolls: number; partial_meters: number; roll_length: number; unit: string; tracking_mode: "packaged" | "quantity"; opening_rate_rub: number; note: string; updated_at?: string };
export type MaterialFifoRow = MaterialInventory & { physical_meters: number; fifo_meters: number; fifo_amount_rub: number; weighted_rate_rub_m: number; next_fifo_rate_rub_m: number; active_layers: number; difference_meters: number };
export type MaterialInventoryData = { items: MaterialInventory[]; fifo: MaterialFifoRow[]; opening_rate_rub_m: number };
export type SupplierRecord = { id: number; name: string; contact_person: string; phone: string; messenger: string; email: string; country: string; default_currency: string; payment_terms_days: number; lead_time_days: number; active: number; note: string; order_count?: number; last_order_date?: string | null };

export async function startTenantSync(scope: "all" | "ads" = "all"): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/v1/sync${scope === "ads" ? "/ads" : ""}`, { method: "POST" });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Не удалось запустить синхронизацию: HTTP ${response.status}`);
  }
}

export async function getTenantSyncStatus(): Promise<{ status: string; started_at?: string; finished_at?: string; details?: string }> {
  const response = await fetch(`${API_BASE_URL}/api/v1/sync/status`, { headers: apiHeaders() });
  if (!response.ok) throw new Error(`Не удалось получить статус синхронизации: HTTP ${response.status}`);
  return response.json() as Promise<{ status: string; started_at?: string; finished_at?: string; details?: string }>;
}

type ApiTone = Tone | "default";

type ApiMetric = {
  id?: string;
  key?: string;
  label?: string;
  value?: unknown;
  note?: string;
  delta_bps?: number;
  comparison_label?: string;
  tone?: ApiTone;
};

type ApiAction = {
  id?: string;
  type?: string;
  sku?: string;
  article?: string;
  title?: string;
  issue?: string;
  signal?: string;
  action?: string;
  recommended_action?: string;
  impact_kop?: number;
  due_at?: string;
  priority?: string;
  tone?: ApiTone;
};

type ApiInventoryWatchRow = {
  [key: string]: unknown;
  sku?: string;
  article?: string;
  name?: string;
  product?: string;
  stock?: number;
  stock_days?: number;
  days_of_stock?: number;
  margin_bps?: number;
  signal?: string;
  tone?: ApiTone;
};

type ApiReorderItem = {
  sku?: string;
  article?: string;
  name?: string;
  product?: string;
  stock_days?: number;
  days_of_stock?: number;
  recommended_qty?: number;
  order_qty?: number;
  warehouse?: string;
  risk_label?: string;
  action?: string;
  tone?: ApiTone;
};

type ApiCampaign = {
  campaign?: number | string;
  spend?: number;
  ad_revenue?: number;
  drr?: number;
  id?: string;
  name?: string;
  spend_kop?: number;
  revenue_kop?: number;
  acos_bps?: number;
  signal?: string;
  recommended_action?: string;
  tone?: ApiTone;
};

type ApiSync = {
  state?: DashboardSync["state"];
  source?: DashboardSync["source"];
  updated_at?: string;
  lag_minutes?: number;
};

type ApiTodayResponse = {
  generated_at?: string;
  sync?: ApiSync;
  metrics?: ApiMetric[];
  kpis?: ApiMetric[];
  hero_action?: ApiAction;
  actions?: ApiAction[];
  signals?: ApiAction[];
  revenue_trend?: Array<{ revenue_rub?: number; revenue_kop?: number; plan_bps?: number; hour?: number; date?: string }>;
  revenue_trend_day?: Array<{ revenue_rub?: number; revenue_kop?: number; plan_bps?: number; hour?: number; date?: string }>;
  revenue_trend_week?: Array<{ revenue_rub?: number; revenue_kop?: number; plan_bps?: number; hour?: number; date?: string }>;
  inventory_watch?: ApiInventoryWatchRow[];
  tabs?: {
    shift?: {
      material_covered?: number;
      planned?: number;
      tasks?: ApiProductionTask[];
      blocked_by_material?: ApiMaterialNeed[];
    };
    material?: {
      rows?: ApiMaterialNeed[];
    };
    procurement?: {
      products_to_buy?: ApiReorderItem[];
      active_orders?: ApiSupplyShipment[];
    };
    stock?: {
      rows?: ApiInventoryWatchRow[];
    };
  };
};

type ApiProductionTask = {
  sku?: string;
  article?: string;
  supplier_article?: string;
  title?: string;
  product?: string;
  product_name?: string;
  planned?: number;
  planned_units?: number;
  actual?: number;
  actual_units?: number;
  status?: string;
};

type ApiMaterialNeed = {
  name?: string;
  material_name?: string;
  need?: string;
  stock?: string;
  status?: string;
  tone?: ApiTone;
};

type ApiSupplyShipment = {
  id?: string;
  order_number?: string;
  supplier?: string;
  supplier_name?: string;
  status?: string;
  eta?: string;
  expected_date?: string;
  amount?: string;
  total_amount?: string;
  outstanding_amount?: string;
  tone?: ApiTone;
};

type ApiStockResponse = {
  sync?: ApiSync;
  metrics?: ApiMetric[];
  hero?: Partial<StockHero>;
  reorder_plan?: ApiReorderItem[];
  inventory_watch?: ApiInventoryWatchRow[];
  coverage?: ApiCoverageRow[];
  warehouses?: ApiCoverageRow[];
  rules?: ApiRule[];
};

type ApiAdsResponse = {
  sync?: ApiSync;
  metrics?: ApiMetric[];
  hero?: Partial<AdsHero>;
  campaigns?: ApiCampaign[];
  funnel?: ApiFunnelStage[];
  creative_tests?: ApiCreativeSignal[];
};

type ApiProductionResponse = {
  sync?: ApiSync;
  metrics?: ApiMetric[];
  stages?: ProductionStage[];
  tasks?: ApiProductionTask[];
  materials?: ApiMaterialNeed[];
};

type ApiSupplyResponse = {
  sync?: ApiSync;
  metrics?: ApiMetric[];
  shipments?: ApiSupplyShipment[];
  suppliers?: Array<{ name?: string; active?: number | boolean; status?: string }>;
};

type ApiAssistantResponse = {
  sync?: ApiSync;
  metrics?: ApiMetric[];
  automations?: AssistantAutomation[];
  candidates?: Array<Record<string, unknown>>;
  history?: Array<Record<string, unknown>>;
};

type ApiReportsResponse = {
  sync?: ApiSync;
  metrics?: ApiMetric[];
  reports?: ReportCard[];
  exports?: ExportItem[];
};

type ApiCoverageRow = {
  label?: string;
  name?: string;
  detail?: string;
  value?: string;
  value_text?: string;
  width?: number;
  coverage_bps?: number;
  tone?: ApiTone;
};

type ApiRule = {
  id?: string;
  title?: string;
  description?: string;
};

type ApiFunnelStage = {
  label?: string;
  value?: number;
  value_text?: string;
  width?: number;
  width_pct?: number;
};

type ApiCreativeSignal = {
  label?: string;
  value?: string;
  winner?: boolean;
  active?: boolean;
};

type ApiOverviewKpi = {
  key?: string;
  label?: string;
  value?: string;
  note?: string;
  delta?: number | null;
  tone?: ApiTone;
};

type ApiOverviewProduct = {
  nm_id?: number | string;
  article?: string;
  orders?: number;
  sales?: number;
  revenue?: number;
  drr?: number;
  stock?: number;
  days_of_stock?: number;
};

type ApiProblemProduct = {
  status?: string;
  severity?: string;
  name?: string;
  profit?: string;
  reason?: string;
  action?: string;
};

type ApiOverviewResponse = {
  period_label?: string;
  start?: string;
  end?: string;
  kpis?: ApiOverviewKpi[];
  funnel?: ApiFunnelStage[];
  products?: ApiOverviewProduct[];
  problem_products?: ApiProblemProduct[];
  alerts?: ApiAction[];
  cash_windows?: Array<{ label?: string; value?: string; detail?: string }>;
  chart?: {
    rows?: Array<{ revenue?: number; order_amount?: number; ad_spend?: number }>;
  };
};

type ApiProductsResponse = {
  products?: Array<Record<string, unknown>>;
};

type ApiDataQualityResponse = DataQualityData;
type ApiProcurementJournalResponse = { orders?: Array<Record<string, unknown>> };

const API_BASE_URL = import.meta.env.VITE_MH_API_BASE_URL ?? "/tenant-api";
const API_MODE = import.meta.env.VITE_MH_API_MODE ?? "auto";

export async function getRuntimeStatus(signal: AbortSignal) {
  return Promise.all([
    fetchJson<{ enabled: boolean; jobs: Record<string, {status: string; finished_at?: string}> }>("/api/v1/jobs", signal),
    fetchJson<{ sections: Record<string, string | null> }>("/api/v1/sync/sections", signal),
  ]);
}
const TENANT_STORAGE_KEY = "mh-tenant-id";

function apiHeaders(contentType = false): Record<string, string> {
  const headers: Record<string, string> = { accept: "application/json" };
  if (contentType) headers["content-type"] = "application/json";
  try {
    const storedTenant = window.localStorage.getItem(TENANT_STORAGE_KEY);
    const tenantId = storedTenant === "demo-tenant" ? "marketshelper-live" : storedTenant ?? "marketshelper-live";
    if (storedTenant === "demo-tenant") window.localStorage.setItem(TENANT_STORAGE_KEY, tenantId);
    headers["x-tenant-id"] = tenantId;
  } catch {
    headers["x-tenant-id"] = "marketshelper-live";
  }
  return headers;
}

const emptyResponse = Symbol("empty-response");

export type ActionPlanItem = {
  id: string;
  source?: string;
  source_action_id?: string | null;
  title: string;
  sku: string;
  due_at?: string | null;
  status: "created" | "in_progress" | "done" | "cancelled" | "blocked";
  assignee?: string | null;
  executor?: string | null;
  result?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  priority?: "low" | "medium" | "high" | "critical";
  history?: Array<{ status: ActionPlanItem["status"]; at: string }>;
  created_at?: string;
  updated_at?: string;
};

export type DashboardSession = {
  user: { id: string; name: string; role: string };
  tenant: { id: string; name: string };
  mode: string;
};

export async function getDashboardSession(): Promise<DashboardSession> {
  const response = await fetch(`${API_BASE_URL}/api/v1/session`, { headers: apiHeaders() });
  if (!response.ok) {
    throw new Error(`Session returned HTTP ${response.status}`);
  }
  return response.json() as Promise<DashboardSession>;
}

export async function getActionPlanItems(): Promise<ActionPlanItem[]> {
  const response = await fetch(`${API_BASE_URL}/api/v1/action-plan/items`, { headers: apiHeaders() });
  if (!response.ok) {
    throw new Error(`Action plan returned HTTP ${response.status}`);
  }
  const payload = await response.json() as { items?: ActionPlanItem[] };
  return Array.isArray(payload.items) ? payload.items : [];
}

export async function createActionPlanItem(input: {
  source?: string;
  sourceActionId?: string;
  title: string;
  sku: string;
  dueAt?: string;
}): Promise<{ id: string; status: ActionPlanItem["status"]; item?: ActionPlanItem }> {
  const response = await fetch(`${API_BASE_URL}/api/v1/action-plan/items`, {
    method: "POST",
    headers: apiHeaders(true),
    body: JSON.stringify({
      source: input.source ?? "dashboard",
      source_action_id: input.sourceActionId,
      title: input.title,
      sku: input.sku,
      due_at: input.dueAt,
    }),
  });

  if (!response.ok) {
    throw new Error(`Action plan returned HTTP ${response.status}`);
  }

  return response.json() as Promise<{ id: string; status: ActionPlanItem["status"]; item?: ActionPlanItem }>;
}

export async function updateActionPlanItem(id: string, patch: Partial<Pick<ActionPlanItem, "status" | "assignee" | "due_at" | "priority">>): Promise<ActionPlanItem> {
  const response = await fetch(`${API_BASE_URL}/api/v1/action-plan/items/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: apiHeaders(true),
    body: JSON.stringify(patch),
  });
  if (!response.ok) {
    throw new Error(`Action plan returned HTTP ${response.status}`);
  }
  return response.json() as Promise<ActionPlanItem>;
}

async function postOperation<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: apiHeaders(true),
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(detail || `Operation returned HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

async function patchOperation<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, { method: "PATCH", headers: apiHeaders(true), body: JSON.stringify(body) });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json() as Promise<T>;
}

export function createProcurementOperation(input: {
  supplierName: string;
  supplierArticle: string;
  productName: string;
  quantity: number;
  unitPrice: number;
  status: string;
  orderDate: string;
  paymentDueDate: string;
  expectedDate: string;
  currency: string;
  exchangeRate: number;
  note: string;
}): Promise<{ id: number; status: string }> {
  return postOperation("/api/v1/operations/procurement", {
    supplier_name: input.supplierName,
    procurement_type: "Товар",
    status: input.status,
    order_date: input.orderDate || null,
    payment_due_date: input.paymentDueDate || null,
    expected_date: input.expectedDate || null,
    currency: input.currency,
    exchange_rate: input.exchangeRate,
    note: input.note,
    items: [{
      supplier_article: input.supplierArticle,
      product_name: input.productName,
      quantity: input.quantity,
      supplier_unit_price: input.unitPrice,
      exchange_rate: input.exchangeRate,
    }],
  });
}

export function createProductionShift(input: {
  sku: string;
  productName: string;
  plannedUnits: number;
}): Promise<{ saved: number; status: string }> {
  return postOperation("/api/v1/operations/production/shift", {
    tasks: [{
      task_key: `redesign:${input.sku}:${new Date().toISOString()}`,
      task_type: "Производство",
      supplier_article: input.sku,
      product_name: input.productName,
      planned_units: input.plannedUnits,
      task_date: new Date().toISOString().slice(0, 10),
    }],
  });
}

export function postSupplyReceipt(orderId: string, itemId: number, quantity: number): Promise<{ posted: number; items: number }> {
  return postOperation(`/api/v1/operations/supply/${encodeURIComponent(orderId)}/receipt`, {
    items: [{ item_id: itemId, receive_now: quantity }],
  });
}

export async function getProcurementOperations(): Promise<{ orders: Array<Record<string, unknown>>; items: Array<Record<string, unknown>> }> {
  const response = await fetch(`${API_BASE_URL}/api/v1/operations/procurement`, { headers: apiHeaders() });
  if (!response.ok) throw new Error(`/api/v1/operations/procurement: HTTP ${response.status}`);
  return response.json() as Promise<{ orders: Array<Record<string, unknown>>; items: Array<Record<string, unknown>> }>;
}

export async function getSuppliers(): Promise<{ items: SupplierRecord[] }> {
  const response = await fetch(`${API_BASE_URL}/api/v1/operations/suppliers`, { headers: apiHeaders() });
  if (!response.ok) throw new Error(`/api/v1/operations/suppliers: HTTP ${response.status}`);
  return response.json() as Promise<{ items: SupplierRecord[] }>;
}

export function saveSupplier(item: Omit<SupplierRecord, "order_count" | "last_order_date">): Promise<{ id: number; status: string; item?: SupplierRecord }> {
  return postOperation("/api/v1/operations/suppliers", item);
}

export async function downloadReportPdf(name = "Операционный отчёт", days = 30): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/v1/operations/reports/pdf?${new URLSearchParams({ name, days: String(days) })}`, {
    headers: apiHeaders(),
  });
  if (!response.ok) throw new Error(`PDF returned HTTP ${response.status}`);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "marketshelper-report.pdf";
  link.click();
  URL.revokeObjectURL(url);
}

export function decideAgentCandidate(id: number, outcome: "apply" | "reject"): Promise<{ status: string; detail?: string }> {
  return postOperation(`/api/v1/agent/candidates/${encodeURIComponent(id)}/decision`, { outcome });
}

export function refreshAgentRecommendations(): Promise<{ price: number; ads: number; pending: number; warnings: string[]; mode: string }> {
  return postOperation("/api/v1/agent/refresh", {});
}

export async function getFifoReconciliation(): Promise<FifoReconciliationData> {
  const response = await fetch(`${API_BASE_URL}/api/v1/fifo/reconciliation`, { headers: apiHeaders() });
  if (!response.ok) throw new Error(`/api/v1/fifo/reconciliation: HTTP ${response.status}`);
  return response.json() as Promise<FifoReconciliationData>;
}

export function applyFifoReconciliation(note: string): Promise<{ run_id?: number; lines?: number; added_units?: number; removed_units?: number; skipped_lines?: number; skipped_units?: number }> {
  return postOperation("/api/v1/fifo/reconciliation/apply", { confirmed: true, note });
}

export async function getTenantSettings(): Promise<TenantSettings> {
  const response = await fetch(`${API_BASE_URL}/api/v1/settings`, { headers: apiHeaders() });
  if (!response.ok) throw new Error(`/api/v1/settings: HTTP ${response.status}`);
  return response.json() as Promise<TenantSettings>;
}

export function updateTenantSettings(settings: Pick<TenantSettings, "sync_interval_minutes" | "initial_history_days" | "agent_interval_minutes">): Promise<TenantSettings> {
  return patchOperation("/api/v1/settings", settings);
}

export async function getProductCostSettings(): Promise<{ items: ProductCostSetting[] }> {
  const response = await fetch(`${API_BASE_URL}/api/v1/settings/product-costs`, { headers: apiHeaders() });
  if (!response.ok) throw new Error(`/api/v1/settings/product-costs: HTTP ${response.status}`);
  return response.json() as Promise<{ items: ProductCostSetting[] }>;
}

export async function getProductionNorms(): Promise<{ items: ProductionNorm[] }> {
  const response = await fetch(`${API_BASE_URL}/api/v1/settings/production-norms`, { headers: apiHeaders() });
  if (!response.ok) throw new Error(`/api/v1/settings/production-norms: HTTP ${response.status}`);
  return response.json() as Promise<{ items: ProductionNorm[] }>;
}

export function updateProductionNorms(items: Array<Pick<ProductionNorm, "nm_id" | "material_per_unit" | "min_batch" | "pack_size" | "enabled">>): Promise<{ saved: number }> {
  return patchOperation("/api/v1/settings/production-norms", { items });
}

export async function getMaterialInventory(): Promise<MaterialInventoryData> {
  const response = await fetch(`${API_BASE_URL}/api/v1/settings/material-inventory`, { headers: apiHeaders() });
  if (!response.ok) throw new Error(`/api/v1/settings/material-inventory: HTTP ${response.status}`);
  return response.json() as Promise<MaterialInventoryData>;
}

export function updateMaterialInventory(items: MaterialInventory[]): Promise<MaterialInventoryData & { saved: number }> {
  return patchOperation("/api/v1/settings/material-inventory", { items });
}

export function initializeMaterialFifo(openingRateRubM: number): Promise<{ result: { created: number; meters: number; materials: number; warnings: string[] }; fifo: MaterialFifoRow[]; opening_rate_rub_m: number }> {
  return postOperation("/api/v1/fifo/materials/initialize", { opening_rate_rub_m: openingRateRubM });
}

export function updateProductCostSettings(items: Array<Pick<ProductCostSetting, "nm_id" | "supplier_article" | "cost_per_wb_unit" | "note">>): Promise<{ saved: number }> {
  return patchOperation("/api/v1/settings/product-costs", { items });
}

export function updateProcurementStatus(orderId: number, status: string): Promise<{ id: number; status: string }> {
  return patchOperation(`/api/v1/operations/procurement/${orderId}/status`, { status });
}

export function createProcurementPayment(orderId: number, payment: { amount: number; payment_date: string; method: string; note: string }): Promise<{ id: number; status: string }> {
  return postOperation(`/api/v1/operations/procurement/${orderId}/payments`, payment);
}

export async function getReportOperations(): Promise<ReportOperations> {
  const response = await fetch(`${API_BASE_URL}/api/v1/operations/reports`, { headers: apiHeaders() });
  if (!response.ok) throw new Error(`/api/v1/operations/reports: HTTP ${response.status}`);
  return response.json() as Promise<ReportOperations>;
}

export async function getReportDelivery(): Promise<ReportDelivery> {
  const response = await fetch(`${API_BASE_URL}/api/v1/operations/reports/delivery`, { headers: apiHeaders() });
  if (!response.ok) throw new Error(`Не удалось получить настройки доставки: HTTP ${response.status}`);
  return response.json() as Promise<ReportDelivery>;
}

export async function requestTelegramLinkCode(): Promise<{ code: string; ttl_minutes: number; deep_link: string; bot_username: string }> {
  return postOperation("/api/v1/operations/reports/telegram/link-code", {});
}

export async function getProductionExecution(): Promise<{ tasks: ProductionExecutionTask[] }> {
  const response = await fetch(`${API_BASE_URL}/api/v1/operations/production/execution`, { headers: apiHeaders() });
  if (!response.ok) throw new Error(`/api/v1/operations/production/execution: HTTP ${response.status}`);
  return response.json() as Promise<{ tasks: ProductionExecutionTask[] }>;
}

export async function getIncidentOperations(): Promise<IncidentOperations> {
  const response = await fetch(`${API_BASE_URL}/api/v1/operations/incidents`, { headers: apiHeaders() });
  if (!response.ok) throw new Error(`/api/v1/operations/incidents: HTTP ${response.status}`);
  return response.json() as Promise<IncidentOperations>;
}

export function createIncidentCase(payload: { incident_name: string; incident_date: string; warehouse_label: string; document_ref: string; note: string }): Promise<{ id: number; status: string }> {
  return postOperation("/api/v1/operations/incidents", payload);
}

export function createIncidentCompensation(caseId: number, payload: { amount_rub: number; compensation_date: string; source_ref: string; note: string }): Promise<{ id: number; status: string }> {
  return postOperation(`/api/v1/operations/incidents/${caseId}/compensations`, payload);
}

export function applyIncidentLoss(caseId: number, payload: { nm_id: number; units: number; note: string; confirmed: boolean }): Promise<{ units: number; fifo_cost_rub: number }> {
  return postOperation(`/api/v1/operations/incidents/${caseId}/loss`, payload);
}

export async function getTenantBackups(): Promise<{ backups: Array<Record<string, unknown>>; last_sync: Record<string, unknown> }> {
  const response = await fetch(`${API_BASE_URL}/api/v1/operations/backups`, { headers: apiHeaders() });
  if (!response.ok) throw new Error(`/api/v1/operations/backups: HTTP ${response.status}`);
  return response.json() as Promise<{ backups: Array<Record<string, unknown>>; last_sync: Record<string, unknown> }>;
}

export function createTenantBackup(): Promise<{ name: string; status: string; latest: Record<string, unknown> }> {
  return postOperation("/api/v1/operations/backups", {});
}

export type TenantSyncRun = {
  id: number;
  started_at: string | null;
  finished_at: string | null;
  status: string;
  details: string | null;
};

export async function getTenantSyncHistory(): Promise<{ runs: TenantSyncRun[] }> {
  const response = await fetch(`${API_BASE_URL}/api/v1/sync/history`, { headers: apiHeaders() });
  if (!response.ok) throw new Error(`/api/v1/sync/history: HTTP ${response.status}`);
  return response.json() as Promise<{ runs: TenantSyncRun[] }>;
}

export function saveProductionExecution(tasks: ProductionExecutionTask[]): Promise<{ saved: number; status: string }> {
  return postOperation("/api/v1/operations/production/execution", { tasks });
}

export function closeProductionExecution(taskDate: string): Promise<{ posted: number; units: number; skipped: number }> {
  return postOperation(`/api/v1/operations/production/execution/${encodeURIComponent(taskDate)}/close`, {});
}

export function createReportDefinition(payload: { name: string; metrics: string[]; schedule_type: string; schedule_time: string; schedule_weekday?: number; schedule_day?: number; email_enabled?: boolean; telegram_enabled?: boolean }): Promise<{ id: number; status: string }> {
  return postOperation("/api/v1/operations/reports/definitions", payload);
}

export function generateReportDefinition(id: number): Promise<{ id: number; run_id: number; status: string }> {
  return postOperation(`/api/v1/operations/reports/definitions/${id}/generate`, {});
}

export function generateQuickReport(name = "Операционный отчёт", days = 30, metrics?: string[]): Promise<{ id: number; run_id: number; status: string }> {
  return postOperation("/api/v1/operations/reports/quick", { name, days, metrics });
}

export async function deleteReportDefinition(id: number): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/v1/operations/reports/definitions/${id}`, { method: "DELETE", headers: apiHeaders() });
  if (!response.ok) throw new Error(`Удаление отчёта: HTTP ${response.status}`);
}

export async function downloadSavedReport(id: number): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/v1/operations/reports/runs/${id}/download`, { headers: apiHeaders() });
  if (!response.ok) throw new Error(`PDF returned HTTP ${response.status}`);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "marketshelper-report.pdf";
  link.click();
  URL.revokeObjectURL(url);
}

export function useDashboardData(activePage: PageId, selectedPeriod: DashboardPeriod): DashboardDataState {
  const [dashboard, setDashboard] = useState<DashboardViewModel>(() => ({
    ...(API_MODE === "mock" ? createMockViewModel() : createLiveEmptyViewModel()),
    connection: {
      mode: API_MODE === "mock" ? "mock" : "loading",
      label: API_MODE === "mock" ? "Mock режим" : "Подключаю API",
      detail: API_MODE === "mock" ? "Интерфейс работает на локальных данных" : "Пробую tenant API",
    },
  }));
  const [refreshing, setRefreshing] = useState(false);
  const activeController = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    activeController.current?.abort();
    const controller = new AbortController();
    activeController.current = controller;

    setRefreshing(true);
    try {
      const nextDashboard = await loadDashboardData(controller.signal, activePage, selectedPeriod);
      if (!controller.signal.aborted) {
        setDashboard(nextDashboard);
      }
    } catch (error: unknown) {
      if (controller.signal.aborted) {
        return;
      }

      const detail = error instanceof Error ? error.message : "Tenant API недоступен";
      setDashboard({
        ...createLiveEmptyViewModel({ state: "error" }),
        connection: {
          mode: "fallback",
          label: "Tenant API недоступен",
          detail,
        },
      });
    } finally {
      if (!controller.signal.aborted) {
        setRefreshing(false);
      }
    }
  }, [activePage, selectedPeriod]);

  useEffect(() => {
    if (API_MODE === "mock") {
      setDashboard({
        ...createMockViewModel(),
        connection: {
          mode: "mock",
          label: "Mock режим",
          detail: "Интерфейс работает на локальных данных",
        },
      });
      return;
    }

    void refresh();

    return () => activeController.current?.abort();
  }, [refresh]);

  return { ...dashboard, refreshing, refresh };
}

async function loadDashboardData(
  signal: AbortSignal,
  activePage: PageId,
  selectedPeriod: DashboardPeriod,
): Promise<DashboardViewModel> {
  type DataStatus = { connected: boolean; state: "fresh" | "stale"; partial?: boolean; details?: string; updated_at: string | null; lag_minutes: number | null; missing_costs: boolean; missing_material_inventory?: boolean };
  const [model, status] = await Promise.all([
    loadDashboardPayload(signal, activePage, selectedPeriod),
    fetchJson<DataStatus>("/api/v1/data-status", signal).catch(() => null),
  ]);
  const fresh = status?.connected === true && status.state === "fresh";
  return {
    ...model,
    connection: {
      mode: fresh ? "live" : "stale",
      label: !status ? "Свежесть данных неизвестна" : !status.connected ? "WB не подключён" : status.partial ? "Данные обновлены частично" : fresh ? "Данные WB обновлены" : "Данные WB устарели",
      detail: status?.updated_at
        ? `${status.partial ? "Частичное обновление" : "Последняя синхронизация"}: ${new Date(status.updated_at).toLocaleString("ru-RU", { timeZone: "Europe/Moscow" })} МСК${status.partial ? ". Некоторые разделы не обновились — подробности в журнале синхронизации." : ""}`
        : "Дата успешной синхронизации неизвестна",
    },
    sync: { state: fresh ? "fresh" : "stale", source: "wb-api", updatedAt: status?.updated_at ?? "", lagMinutes: status?.lag_minutes ?? 0 },
    dataWarning: status?.missing_material_inventory
      ? "Остатки сырья не заведены. Производственный план не может подтвердить наличие материала."
      : status?.missing_costs
        ? "Себестоимость не заполнена. Прибыль и маржа показаны без подтверждённых затрат на товары."
        : undefined,
  };
}

async function loadDashboardPayload(
  signal: AbortSignal,
  activePage: PageId,
  selectedPeriod: DashboardPeriod,
): Promise<DashboardViewModel> {
  if (API_MODE !== "mock") {
    try {
      return await loadApiV1(signal, activePage, selectedPeriod);
    } catch (error) {
      if (API_MODE === "v1") {
        throw error;
      }
    }
  }

  return loadLegacyToday(signal);
}

async function loadApiV1(
  signal: AbortSignal,
  activePage: PageId,
  selectedPeriod: DashboardPeriod,
): Promise<DashboardViewModel> {
  const model = createLiveEmptyViewModel();
  let today: ApiTodayResponse | undefined;
  let stock: ApiStockResponse | undefined;
  let ads: ApiAdsResponse | undefined;
  let overview: ApiOverviewResponse | undefined;
  let production: ApiProductionResponse | undefined;
  let supply: ApiSupplyResponse | undefined;
  let assistant: ApiAssistantResponse | undefined;
  let reports: ApiReportsResponse | undefined;
  let products: ApiProductsResponse | undefined;
  let dataQuality: ApiDataQualityResponse | undefined;
  let procurementJournal: ApiProcurementJournalResponse | undefined;

  switch (activePage) {
    case "today":
      today = await fetchJson<ApiTodayResponse>("/api/v1/dashboard/today", signal);
      break;
    case "stock":
      stock = await fetchJson<ApiStockResponse>("/api/v1/dashboard/stock", signal);
      break;
    case "ads":
      ads = await fetchJson<ApiAdsResponse>(`/api/v1/dashboard/ads${periodQuery(selectedPeriod)}`, signal);
      break;
    case "finance":
      overview = await fetchJson<ApiOverviewResponse>(`/api/overview${periodQuery(selectedPeriod)}`, signal);
      break;
    case "production":
      production = await fetchJson<ApiProductionResponse>("/api/v1/dashboard/production", signal);
      break;
    case "supply":
      [supply, procurementJournal] = await Promise.all([
        fetchJson<ApiSupplyResponse>("/api/v1/dashboard/supply", signal),
        fetchJson<ApiProcurementJournalResponse>("/api/v1/operations/procurement", signal),
      ]);
      break;
    case "assistant":
      assistant = await fetchJson<ApiAssistantResponse>("/api/v1/dashboard/assistant", signal);
      break;
    case "reports":
      reports = await fetchJson<ApiReportsResponse>("/api/v1/dashboard/reports", signal);
      break;
    case "products":
      products = await fetchJson<ApiProductsResponse>(`/api/products${periodQuery(selectedPeriod)}`, signal);
      break;
    case "control":
      dataQuality = await fetchJson<ApiDataQualityResponse>("/api/v1/control/data-quality", signal);
      break;
  }

  const sync = today?.sync ?? stock?.sync ?? ads?.sync;
  const mergedActions = adaptActions([...(today?.actions ?? []), ...(overview?.alerts ?? [])], model.actions);
  const mergedInventory = adaptInventory(today?.inventory_watch ?? stock?.inventory_watch ?? [], model.inventory);
  const mergedReorder = adaptReorder(stock?.reorder_plan ?? [], model.reorderPlan);
  const mergedCampaigns = adaptCampaigns(ads?.campaigns ?? [], model.campaigns);

  return {
    ...model,
    connection: {
      mode: sync?.state === "stale" ? "stale" : "live",
      label: sync?.state === "stale" ? "WB API устарел" : "WB API активно",
      detail: liveDetail(sync),
    },
    sync: syncFromApi(sync),
    todayMetrics: adaptMetrics(today?.metrics, model.todayMetrics),
    stockMetrics: adaptMetrics(stock?.metrics, model.stockMetrics),
    adsMetrics: adaptMetrics(ads?.metrics, model.adsMetrics),
    actions: mergedActions,
    inventory: mergedInventory,
    reorderPlan: mergedReorder,
    stockHero: adaptStockHero(stock?.hero, model.stockHero),
    stockCoverage: adaptCoverage(stock?.coverage ?? stock?.warehouses ?? [], model.stockCoverage),
    stockRules: adaptRules(stock?.rules ?? [], model.stockRules),
    adsHero: adaptAdsHero(ads?.hero, ads?.campaigns?.length ? { title: `${ads.campaigns.length} кампаний за выбранный период`, description: "Расходы и выручка по атрибуции рекламного API WB.", tone: "info" } : model.adsHero),
    campaigns: mergedCampaigns,
    adsFunnel: adaptFunnel(ads?.funnel ?? [], model.adsFunnel),
    creativeSignals: adaptCreativeSignals(ads?.creative_tests ?? [], model.creativeSignals),
    financeMetrics: adaptFinanceMetrics(overview, model.financeMetrics),
    financeLedger: adaptFinanceLedger(overview, model.financeLedger),
    expenseSplit: adaptExpenseSplit(overview, model.expenseSplit),
    cashWindows: adaptCashWindows(overview?.cash_windows ?? []),
    productionMetrics: production?.metrics?.length ? adaptMetrics(production.metrics, []) : adaptProductionMetrics(today, model.productionMetrics),
    productionStages: production?.stages !== undefined ? production.stages : adaptProductionStages(today, model.productionStages),
    productionTasks: production?.tasks !== undefined ? adaptProductionTasksPayload(production.tasks) : adaptProductionTasks(today, mergedReorder, model.productionTasks),
    materialNeeds: production?.materials !== undefined ? adaptMaterialPayload(production.materials) : adaptMaterialNeeds(today, model.materialNeeds),
    supplyMetrics: supply?.metrics?.length ? adaptMetrics(supply.metrics, []) : adaptSupplyMetrics(today, stock, mergedReorder, model.supplyMetrics),
    supplyShipments: supply?.shipments !== undefined ? adaptSupplyPayloadShipments(supply.shipments) : adaptSupplyShipments(today, mergedReorder, model.supplyShipments),
    supplierPulse: supply?.suppliers !== undefined ? adaptSupplierPayload(supply.suppliers) : adaptSupplierPulse(mergedReorder, model.supplierPulse),
    assistantMetrics: assistant?.metrics?.length ? adaptMetrics(assistant.metrics, []) : adaptAssistantMetrics(mergedActions, model.assistantMetrics),
    automations: assistant?.automations !== undefined ? assistant.automations : adaptAutomations(mergedActions, model.automations),
    agentCandidates: assistant?.candidates !== undefined ? adaptAgentCandidates(assistant.candidates) : [],
    agentHistory: assistant?.history !== undefined ? adaptAgentCandidates(assistant.history) : [],
    reportMetrics: reports?.metrics?.length ? adaptMetrics(reports.metrics, []) : adaptReportMetrics(overview, model.reportMetrics),
    reportCards: reports?.reports !== undefined ? reports.reports : adaptReportCards(overview, stock, ads, model.reportCards),
    exportItems: reports?.exports !== undefined ? reports.exports : adaptExportItems(overview, model.exportItems),
    chartBars: adaptTrend(today?.revenue_trend_day ?? today?.revenue_trend, model.chartBars),
    chartBarsWeek: adaptTrend(today?.revenue_trend_week ?? today?.revenue_trend, model.chartBarsWeek),
    productEconomics: products?.products ? adaptProductEconomics(products.products) : [],
    dataQuality: dataQuality ?? null,
    procurementOrders: procurementJournal?.orders ?? [],
  };
}

function periodQuery(selectedPeriod: DashboardPeriod): string {
  const query = new URLSearchParams({ period: selectedPeriod.id });
  if (selectedPeriod.id === "custom") {
    if (selectedPeriod.start) query.set("start", selectedPeriod.start);
    if (selectedPeriod.end) query.set("end", selectedPeriod.end);
  }
  return `?${query.toString()}`;
}

async function loadLegacyToday(signal: AbortSignal): Promise<DashboardViewModel> {
  const today = await fetchJson<ApiTodayResponse>("/api/today", signal);
  const model = createLiveEmptyViewModel();
  const actions = adaptActions(today.signals ?? [], []);
  const inventory = adaptInventory(today.inventory_watch ?? today.tabs?.stock?.rows ?? [], []);
  const reorderPlan = adaptReorder(today.tabs?.procurement?.products_to_buy ?? [], []);

  return {
    ...model,
    connection: {
      mode: "live",
      label: "Legacy API активно",
      detail: "Данные получены из /api/today",
    },
    sync: syncFromApi(today.sync),
    todayMetrics: today.kpis ? adaptLegacyMetrics(today.kpis, []) : adaptMetrics(today.metrics, []),
    actions,
    inventory,
    reorderPlan,
    stockMetrics: legacyStockMetrics(inventory, reorderPlan),
    stockHero: legacyStockHero(reorderPlan),
  };
}

async function fetchJson<T>(path: string, signal: AbortSignal): Promise<T> {
  const controller = new AbortController();
  let timedOut = false;
  const timeoutId = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, 20000);
  const abort = () => controller.abort();
  signal.addEventListener("abort", abort, { once: true });

  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      headers: apiHeaders(),
      signal: controller.signal,
    });

    if (response.status === 204) {
      throw emptyResponse;
    }

    if (!response.ok) {
      throw new Error(`${path}: HTTP ${response.status}`);
    }

    return response.json() as Promise<T>;
  } catch (error) {
    if (timedOut) {
      throw new Error(`${path}: timeout`);
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
    signal.removeEventListener("abort", abort);
  }
}

function createMockViewModel(syncOverride?: Partial<DashboardSync>): DashboardViewModel {
  return {
    connection: {
      mode: "mock",
      label: "Mock режим",
      detail: "Интерфейс работает на локальных данных",
    },
    navItems: mock.navItems,
    pageMeta: mock.pageMeta,
    mobileNav: mock.mobileNav,
    sync: { ...mock.dashboardSync, ...syncOverride },
    todayMetrics: mock.todayMetrics,
    stockMetrics: mock.stockMetrics,
    adsMetrics: mock.adsMetrics,
    actions: mock.actions,
    inventory: mock.inventory,
    reorderPlan: mock.reorderPlan,
    stockHero: mock.stockHero,
    stockCoverage: mock.stockCoverage,
    stockRules: mock.stockRules,
    adsHero: mock.adsHero,
    campaigns: mock.campaigns,
    adsFunnel: mock.adsFunnel,
    creativeSignals: mock.creativeSignals,
    financeMetrics: mock.financeMetrics,
    financeLedger: mock.financeLedger,
    expenseSplit: mock.expenseSplit,
    cashWindows: [],
    productionMetrics: mock.productionMetrics,
    productionStages: mock.productionStages,
    productionTasks: mock.productionTasks,
    materialNeeds: mock.materialNeeds,
    supplyMetrics: mock.supplyMetrics,
    supplyShipments: mock.supplyShipments,
    supplierPulse: mock.supplierPulse,
    assistantMetrics: mock.assistantMetrics,
    automations: mock.automations,
    agentCandidates: [],
    agentHistory: [],
    reportMetrics: mock.reportMetrics,
    reportCards: mock.reportCards,
    exportItems: mock.exportItems,
    chartBars: mock.chartBars,
    chartBarsWeek: mock.chartBars,
    productEconomics: [],
    dataQuality: null,
    procurementOrders: [],
  };
}

function createLiveEmptyViewModel(syncOverride?: Partial<DashboardSync>): DashboardViewModel {
  return {
    connection: { mode: "loading", label: "Подключаю API", detail: "Пробую tenant API" },
    navItems: mock.navItems.map(({ count: _count, ...item }) => item),
    pageMeta: mock.pageMeta,
    mobileNav: mock.mobileNav,
    sync: { state: "stale", source: "wb-api", updatedAt: "", lagMinutes: 0, ...syncOverride },
    todayMetrics: [], stockMetrics: [], adsMetrics: [], actions: [], inventory: [], reorderPlan: [],
    stockHero: { primary: "Нет данных по пополнению", primaryNote: "API пока не передал план закупок.", secondary: "-", secondaryNote: "Нет данных", tone: "info" },
    stockCoverage: [], stockRules: [],
    adsHero: { title: "Нет данных по рекламе", description: "API пока не передал рекламные показатели.", tone: "info" },
    campaigns: [], adsFunnel: [], creativeSignals: [],
    financeMetrics: [], financeLedger: [], expenseSplit: [], cashWindows: [],
    productionMetrics: [], productionStages: [], productionTasks: [], materialNeeds: [],
    supplyMetrics: [], supplyShipments: [], supplierPulse: [],
    assistantMetrics: [], automations: [], agentCandidates: [], agentHistory: [], reportMetrics: [], reportCards: [], exportItems: [], chartBars: [], chartBarsWeek: [], productEconomics: [], dataQuality: null, procurementOrders: [],
  };
}

function adaptMetrics(input: ApiMetric[] | undefined, fallback: DashboardMetric[]): DashboardMetric[] {
  if (!input?.length) {
    return fallback;
  }

  return input.slice(0, 4).map((metric, index) => ({
    label: text(metric.label ?? metric.key ?? metric.id, fallback[index]?.label ?? "Метрика"),
    value: displayValue(metric.value, fallback[index]?.value ?? "0"),
    delta:
      typeof metric.delta_bps === "number"
        ? signedPercent(metric.delta_bps)
        : text(metric.note, fallback[index]?.delta ?? ""),
    detail: text(metric.comparison_label, fallback[index]?.detail ?? ""),
    tone: normalizeTone(metric.tone, fallback[index]?.tone ?? "info"),
  }));
}

function adaptLegacyMetrics(input: ApiMetric[], fallback: DashboardMetric[]): DashboardMetric[] {
  return input.slice(0, 4).map((metric, index) => ({
    label: text(metric.label ?? metric.key ?? metric.id, fallback[index]?.label ?? "Метрика"),
    value: displayValue(metric.value, fallback[index]?.value ?? "0"),
    delta: text(metric.note, metric.tone === "warn" ? "требует внимания" : "из API"),
    detail: legacyMetricDetail(metric.key ?? metric.id),
    tone: normalizeTone(metric.tone, fallback[index]?.tone ?? "info"),
  }));
}

function legacyMetricDetail(key: string | undefined): string {
  if (key === "orders") {
    return "заказы за сегодня";
  }
  if (key === "sales") {
    return "выкупы за сегодня";
  }
  if (key === "ads") {
    return "рекламные расходы";
  }
  if (key === "shift") {
    return "план и факт смены";
  }
  return "данные /api/today";
}

function adaptActions(input: ApiAction[], fallback: ActionRecommendation[]): ActionRecommendation[] {
  if (!input.length) {
    return fallback;
  }

  return input.slice(0, 8).map((action, index) => ({
    id: text(action.id, `api-action-${index + 1}`),
    sku: text(action.sku ?? action.article, "SKU"),
    title: text(action.title, text(action.type, fallback[index]?.title ?? "Решение")),
    issue: text(action.issue ?? action.signal, fallback[index]?.issue ?? "Проверьте сигнал"),
    impact: typeof action.impact_kop === "number" ? `+${formatMoney(action.impact_kop)}` : fallback[index]?.impact ?? "",
    owner: compactActionLabel(action.recommended_action ?? action.action, fallback[index]?.owner ?? "Добавить в план"),
    due: formatDue(action.due_at) ?? priorityToDue(action.priority) ?? fallback[index]?.due ?? "сегодня",
    tone: normalizeTone(action.tone, priorityToTone(action.priority)),
    iconKey: action.type === "ads" ? "ads" : fallback[index]?.iconKey ?? "packageX",
  }));
}

function adaptInventory(input: ApiInventoryWatchRow[], fallback: InventoryWatchRow[]): InventoryWatchRow[] {
  if (!input.length) {
    return fallback;
  }

  return input.slice(0, 12).map((row, index) => ({
    sku: text(row.sku ?? row.article ?? row["Артикул продавца"], fallback[index]?.sku ?? "SKU"),
    name: text(row.name ?? row.product ?? row["Товар"], fallback[index]?.name ?? "Товар"),
    stock:
      typeof row.stock_days === "number" || typeof row.days_of_stock === "number" || typeof row["Запас, дней"] === "number"
        ? `${formatNumber((row.stock_days ?? row.days_of_stock ?? row["Запас, дней"] ?? 0) as number)} дня`
        : typeof row.stock === "number"
          ? `${formatNumber(row.stock)} шт.`
          : "остатки WB не загружены",
    margin: typeof row.margin_bps === "number" ? formatPercent(row.margin_bps) : "-",
    signal: text(row.signal, "Нет данных от WB"),
    tone: normalizeTone(row.tone, legacyStockTone(row)),
  }));
}

function legacyStockTone(row: ApiInventoryWatchRow): Tone {
  const days = row.stock_days ?? row.days_of_stock ?? row["Запас, дней"];
  if (typeof days !== "number") return "info";
  if (days <= 3) return "danger";
  if (days <= 7) return "warn";
  return "good";
}

function legacyStockMetrics(inventory: InventoryWatchRow[], reorderPlan: ReorderItem[]): DashboardMetric[] {
  const critical = inventory.filter((item) => item.tone === "danger").length;
  return [
    { label: "Под наблюдением", value: `${inventory.length} SKU`, delta: critical ? `${critical} критично` : "без критичных", detail: "по данным WB", tone: critical ? "danger" : "good" },
    { label: "К пополнению", value: `${reorderPlan.length} SKU`, delta: reorderPlan.length ? "есть план" : "нет заявок", detail: "из закупок", tone: reorderPlan.length ? "warn" : "info" },
  ];
}

function legacyStockHero(reorderPlan: ReorderItem[]): StockHero {
  const first = reorderPlan[0];
  if (!first) return createLiveEmptyViewModel().stockHero;
  return {
    primary: `${reorderPlan.length} SKU в плане пополнения`,
    primaryNote: first.risk,
    secondary: first.qty,
    secondaryNote: `${first.name} · ${first.days}`,
    tone: first.tone,
  };
}

function adaptReorder(input: ApiReorderItem[], fallback: ReorderItem[]): ReorderItem[] {
  if (!input.length) {
    return fallback;
  }

  return input.slice(0, 8).map((item, index) => ({
    sku: text(item.sku ?? item.article, fallback[index]?.sku ?? "SKU"),
    name: text(item.name ?? item.product, fallback[index]?.name ?? "Товар"),
    days:
      typeof item.stock_days === "number" || typeof item.days_of_stock === "number"
        ? `${formatNumber(item.stock_days ?? item.days_of_stock ?? 0)} дня`
        : fallback[index]?.days ?? "нет данных",
    qty:
      typeof item.recommended_qty === "number" || typeof item.order_qty === "number"
        ? `${formatNumber(item.recommended_qty ?? item.order_qty ?? 0)} шт.`
        : fallback[index]?.qty ?? "0 шт.",
    warehouse: text(item.warehouse, fallback[index]?.warehouse ?? "WB"),
    risk: text(item.risk_label ?? item.action, fallback[index]?.risk ?? "проверить запас"),
    tone: normalizeTone(item.tone, fallback[index]?.tone ?? "info"),
  }));
}

function adaptStockHero(input: Partial<StockHero> | undefined, fallback: StockHero): StockHero {
  if (!input) {
    return fallback;
  }

  return {
    primary: text(input.primary, fallback.primary),
    primaryNote: text(input.primaryNote, fallback.primaryNote),
    secondary: text(input.secondary, fallback.secondary),
    secondaryNote: text(input.secondaryNote, fallback.secondaryNote),
    tone: normalizeTone(input.tone, fallback.tone),
  };
}

function adaptCoverage(input: ApiCoverageRow[], fallback: StockCoverageRow[]): StockCoverageRow[] {
  if (!input.length) {
    return fallback;
  }

  return input.slice(0, 4).map((row, index) => {
    const width =
      typeof row.width === "number"
        ? row.width
        : typeof row.coverage_bps === "number"
          ? row.coverage_bps / 100
          : fallback[index]?.width ?? 0;

    return {
      label: text(row.label ?? row.name, fallback[index]?.label ?? "Сегмент"),
      detail: text(row.detail, fallback[index]?.detail ?? "покрытие"),
      value: text(row.value ?? row.value_text, `${formatNumber(width)}%`),
      width: clamp(width, 4, 100),
      tone: normalizeTone(row.tone, fallback[index]?.tone ?? "info"),
    };
  });
}

function adaptRules(input: ApiRule[], fallback: StockRule[]): StockRule[] {
  if (!input.length) {
    return fallback;
  }

  return input.slice(0, 3).map((rule, index) => ({
    id: text(rule.id, `api-rule-${index + 1}`),
    title: text(rule.title, fallback[index]?.title ?? "Правило"),
    description: text(rule.description, fallback[index]?.description ?? "Проверить правило пополнения."),
  }));
}

function adaptAdsHero(input: Partial<AdsHero> | undefined, fallback: AdsHero): AdsHero {
  if (!input) {
    return fallback;
  }

  return {
    title: text(input.title, fallback.title),
    description: text(input.description, fallback.description),
    tone: normalizeTone(input.tone, fallback.tone),
  };
}

function adaptCampaigns(input: ApiCampaign[], fallback: AdCampaign[]): AdCampaign[] {
  if (!input.length) {
    return fallback;
  }

  return input.map((campaign, index) => ({
    name: text(campaign.name, `Кампания ${campaign.campaign ?? campaign.id ?? index + 1}`),
    spend: typeof campaign.spend_kop === "number" ? formatMoney(campaign.spend_kop) : typeof campaign.spend === "number" ? formatMoney(campaign.spend * 100) : "-",
    acos: typeof campaign.acos_bps === "number" ? formatPercent(campaign.acos_bps) : typeof campaign.drr === "number" ? `${campaign.drr.toFixed(1)}%` : "-",
    revenue:
      typeof campaign.revenue_kop === "number" ? formatMoney(campaign.revenue_kop) : typeof campaign.ad_revenue === "number" ? formatMoney(campaign.ad_revenue * 100) : "-",
    signal: text(campaign.signal, fallback[index]?.signal ?? "проверка"),
    action: text(campaign.recommended_action, fallback[index]?.action ?? "Проверить"),
    tone: normalizeTone(campaign.tone, fallback[index]?.tone ?? "info"),
  }));
}

function adaptFunnel(input: ApiFunnelStage[], fallback: FunnelStage[]): FunnelStage[] {
  if (!input.length) {
    return fallback;
  }

  return input.slice(0, 5).map((stage, index) => {
    const width = typeof stage.width_pct === "number" ? stage.width_pct : stage.width ?? fallback[index]?.width ?? 0;
    return {
      label: text(stage.label, fallback[index]?.label ?? "Шаг"),
      value: text(stage.value_text, typeof stage.value === "number" ? formatNumber(stage.value) : fallback[index]?.value ?? "0"),
      width: clamp(width, 4, 100),
    };
  });
}

function adaptCreativeSignals(input: ApiCreativeSignal[], fallback: CreativeSignal[]): CreativeSignal[] {
  if (!input.length) {
    return fallback;
  }

  return input.slice(0, 3).map((signal, index) => ({
    label: text(signal.label, fallback[index]?.label ?? "Сигнал"),
    value: text(signal.value, fallback[index]?.value ?? "нет данных"),
    active: Boolean(signal.winner ?? signal.active ?? fallback[index]?.active),
  }));
}

function adaptFinanceMetrics(overview: ApiOverviewResponse | undefined, fallback: DashboardMetric[]): DashboardMetric[] {
  if (!overview?.kpis?.length) {
    return fallback;
  }

  const profit = findOverviewKpi(overview, "profit");
  const revenue = findOverviewKpi(overview, "revenue");
  const ads = findOverviewKpi(overview, "ad_spend");
  const stock = findOverviewKpi(overview, "stock");

  return [
    overviewMetricCard(profit, fallback[0], "Прибыль", "расчетная по товарам"),
    overviewMetricCard(revenue, fallback[1], "Выручка", "оперативные продажи"),
    overviewMetricCard(ads, fallback[2], "Реклама", "расход за период", true),
    {
      label: "Остаток WB",
      value: text(stock?.value, fallback[3]?.value ?? "0"),
      delta: text(stock?.note, fallback[3]?.delta ?? "на складах"),
      detail: text(overview.period_label, fallback[3]?.detail ?? "период"),
      tone: normalizeTone(stock?.tone, fallback[3]?.tone ?? "info"),
    },
  ];
}

function adaptCashWindows(rows: Array<{ label?: string; value?: string; detail?: string }>): CashWindow[] {
  return rows.slice(0, 3).map((row) => ({
    label: text(row.label, "Период"),
    value: text(row.value, "—"),
    detail: text(row.detail, "оперативные данные"),
  }));
}

function adaptFinanceLedger(overview: ApiOverviewResponse | undefined, fallback: FinanceLedgerItem[]): FinanceLedgerItem[] {
  if (!overview?.kpis?.length) {
    return fallback;
  }

  const revenue = findOverviewKpi(overview, "revenue");
  const profit = findOverviewKpi(overview, "profit");
  const ads = findOverviewKpi(overview, "ad_spend");
  const orders = findOverviewKpi(overview, "orders");

  return [
    {
      label: "Выручка WB",
      amount: signedAmount(text(revenue?.value, "0 ₽"), true),
      detail: overviewDeltaText(revenue, "оперативные данные"),
      tone: overviewTone(revenue, false),
    },
    {
      label: "Реклама",
      amount: signedAmount(text(ads?.value, "0 ₽"), false),
      detail: text(ads?.note, "расход за период"),
      tone: overviewTone(ads, true),
    },
    {
      label: "Заказы",
      amount: text(orders?.value, "0"),
      detail: text(orders?.note, "количество и сумма"),
      tone: overviewTone(orders, false),
    },
    {
      label: "Прибыль по товарам",
      amount: signedAmount(text(profit?.value, "0 ₽"), true),
      detail: overviewDeltaText(profit, "расчетная прибыль"),
      tone: overviewTone(profit, false),
    },
  ];
}

function adaptExpenseSplit(overview: ApiOverviewResponse | undefined, fallback: ExpenseSplit[]): ExpenseSplit[] {
  const revenue = moneyStringToNumber(findOverviewKpi(overview, "revenue")?.value);
  const profit = moneyStringToNumber(findOverviewKpi(overview, "profit")?.value);
  const ads = moneyStringToNumber(findOverviewKpi(overview, "ad_spend")?.value);

  if (revenue <= 0) {
    return fallback;
  }

  const otherCosts = Math.max(revenue - profit - ads, 0);

  return [
    splitRow("Прибыль", profit, revenue, "good"),
    splitRow("Реклама", ads, revenue, ads / revenue > 0.11 ? "warn" : "good"),
    splitRow("Прочие расходы", otherCosts, revenue, "info"),
  ];
}

function adaptProductionTasksPayload(tasks: ApiProductionTask[]): ProductionTask[] {
  return tasks.slice(0, 8).map((task, index) => {
    const planned = task.planned ?? task.planned_units ?? 0;
    const actual = task.actual ?? task.actual_units ?? 0;
    return {
      sku: text(task.sku ?? task.article ?? task.supplier_article, `task-${index + 1}`),
      title: text(task.title ?? task.product ?? task.product_name, "Партия"),
      plan: formatNumber(planned),
      actual: formatNumber(actual),
      status: text(task.status, actual >= planned && planned > 0 ? "готово" : "в работе"),
      tone: actual >= planned && planned > 0 ? "good" : "warn",
    };
  });
}

function adaptMaterialPayload(rows: ApiMaterialNeed[]): MaterialNeed[] {
  return rows.slice(0, 8).map((row) => ({
    name: text(row.name ?? row.material_name, "Материал"),
    need: text(row.need, "нет данных"),
    stock: text(row.stock, "нет данных"),
    status: text(row.status, "проверить"),
    tone: normalizeTone(row.tone, "warn"),
  }));
}

function adaptSupplyPayloadShipments(rows: ApiSupplyShipment[]): SupplyShipment[] {
  return rows.slice(0, 8).map((order, index) => ({
    id: text(order.id ?? order.order_number, `PO-${index + 1}`),
    supplier: text(order.supplier ?? order.supplier_name, "Поставщик"),
    status: text(order.status, "в работе"),
    eta: text(order.eta ?? order.expected_date, "уточнить"),
    amount: text(order.outstanding_amount ?? order.amount ?? order.total_amount, "0 ₽"),
    tone: normalizeTone(order.tone, statusTone(order.status)),
  }));
}

function adaptProductionMetrics(today: ApiTodayResponse | undefined, fallback: DashboardMetric[]): DashboardMetric[] {
  const shift = today?.tabs?.shift;
  if (!shift) {
    return fallback;
  }

  const planned = shift.planned ?? 0;
  const covered = shift.material_covered ?? 0;
  const taskCount = shift.tasks?.length ?? 0;
  const blockers = shift.blocked_by_material?.length ?? 0;
  const coverage = planned > 0 ? Math.round((covered / planned) * 100) : 0;

  return [
    { label: "План смены", value: formatNumber(planned), delta: "компл.", detail: "ближайшая смена", tone: planned > 0 ? "info" : "warn" },
    { label: "Сырьем закрыто", value: `${formatNumber(coverage)}%`, delta: `${formatNumber(covered)} компл.`, detail: "по нормам материалов", tone: coverage >= 90 ? "good" : coverage >= 60 ? "warn" : "danger" },
    { label: "Задания", value: formatNumber(taskCount), delta: taskCount ? "в плане" : "нет смены", detail: "производственный лист", tone: taskCount ? "good" : "warn" },
    { label: "Блокеры", value: formatNumber(blockers), delta: blockers ? "материалы" : "чисто", detail: "мешают запуску", tone: blockers ? "danger" : "good" },
  ];
}

function adaptProductionStages(today: ApiTodayResponse | undefined, fallback: ProductionStage[]): ProductionStage[] {
  const shift = today?.tabs?.shift;
  if (!shift) {
    return fallback;
  }

  const planned = shift.planned ?? 0;
  const covered = shift.material_covered ?? 0;
  const blockers = shift.blocked_by_material?.length ?? 0;

  return [
    { label: "План", value: planned > 0 ? `${formatNumber(planned)} компл.` : "не сформирован", status: planned > 0 ? "done" : "pending" },
    { label: "Материалы", value: planned > 0 ? `${formatNumber(covered)}/${formatNumber(planned)}` : "нет плана", status: blockers ? "blocked" : covered >= planned && planned > 0 ? "done" : "active" },
    { label: "Задания", value: `${formatNumber(shift.tasks?.length ?? 0)}`, status: shift.tasks?.length ? "active" : "pending" },
    { label: "Блокеры", value: `${formatNumber(blockers)}`, status: blockers ? "blocked" : "done" },
  ];
}

function adaptProductionTasks(
  today: ApiTodayResponse | undefined,
  reorderPlan: ReorderItem[],
  fallback: ProductionTask[],
): ProductionTask[] {
  const tasks = today?.tabs?.shift?.tasks ?? [];
  if (tasks.length) {
    return tasks.slice(0, 8).map((task, index) => {
      const planned = task.planned_units ?? 0;
      const actual = task.actual_units ?? 0;
      return {
        sku: text(task.sku ?? task.article ?? task.supplier_article, `task-${index + 1}`),
        title: text(task.title ?? task.product ?? task.product_name, fallback[index]?.title ?? "Партия"),
        plan: formatNumber(planned),
        actual: formatNumber(actual),
        status: text(task.status, actual >= planned && planned > 0 ? "готово" : "в работе"),
        tone: actual >= planned && planned > 0 ? "good" : "warn",
      };
    });
  }

  if (reorderPlan.length) {
    return reorderPlan.slice(0, 5).map((item) => ({
      sku: item.sku,
      title: item.name,
      plan: item.qty,
      actual: "0",
      status: item.tone === "danger" ? "готовить срочно" : "в очередь",
      tone: item.tone,
    }));
  }

  return fallback;
}

function adaptMaterialNeeds(today: ApiTodayResponse | undefined, fallback: MaterialNeed[]): MaterialNeed[] {
  const materialRows = [...(today?.tabs?.material?.rows ?? []), ...(today?.tabs?.shift?.blocked_by_material ?? [])];
  if (!materialRows.length) {
    return fallback;
  }

  return materialRows.slice(0, 6).map((row, index) => ({
    name: text(row.name ?? row.material_name, fallback[index]?.name ?? "Материал"),
    need: text(row.need, fallback[index]?.need ?? "нет данных"),
    stock: text(row.stock, fallback[index]?.stock ?? "нет данных"),
    status: text(row.status, fallback[index]?.status ?? "проверить"),
    tone: normalizeTone(row.tone, fallback[index]?.tone ?? "warn"),
  }));
}

function adaptSupplyMetrics(
  today: ApiTodayResponse | undefined,
  stock: ApiStockResponse | undefined,
  reorderPlan: ReorderItem[],
  fallback: DashboardMetric[],
): DashboardMetric[] {
  const activeOrders = today?.tabs?.procurement?.active_orders ?? [];
  const productsToBuy = today?.tabs?.procurement?.products_to_buy ?? [];
  const stockMetrics = stock?.metrics ?? [];
  const stockTotal = stockMetrics.find((metric) => metric.id === "stock_total");

  if (!activeOrders.length && !productsToBuy.length && !reorderPlan.length) {
    return fallback;
  }

  return [
    { label: "К пополнению", value: `${formatNumber(reorderPlan.length)} SKU`, delta: "из плана", detail: "приоритет по дням запаса", tone: reorderPlan.some((item) => item.tone === "danger") ? "danger" : "warn" },
    { label: "Активные заявки", value: formatNumber(activeOrders.length), delta: activeOrders.length ? "в работе" : "нет", detail: "procurement", tone: activeOrders.length ? "info" : "good" },
    { label: "Закупить", value: formatNumber(productsToBuy.length || reorderPlan.length), delta: "позиции", detail: "товары и сырье", tone: productsToBuy.length || reorderPlan.length ? "warn" : "good" },
    { label: "Остаток WB", value: displayValue(stockTotal?.value, fallback[3]?.value ?? "0"), delta: text(stockTotal?.note, "склад"), detail: "после поставок", tone: normalizeTone(stockTotal?.tone, fallback[3]?.tone ?? "info") },
  ];
}

function adaptSupplyShipments(
  today: ApiTodayResponse | undefined,
  reorderPlan: ReorderItem[],
  fallback: SupplyShipment[],
): SupplyShipment[] {
  const activeOrders = today?.tabs?.procurement?.active_orders ?? [];
  if (activeOrders.length) {
    return activeOrders.slice(0, 8).map((order, index) => ({
      id: text(order.id ?? order.order_number, `PO-${index + 1}`),
      supplier: text(order.supplier ?? order.supplier_name, "Поставщик"),
      status: text(order.status, "в работе"),
      eta: text(order.eta ?? order.expected_date, "уточнить"),
      amount: text(order.outstanding_amount ?? order.amount ?? order.total_amount, "0 ₽"),
      tone: normalizeTone(order.tone, statusTone(order.status)),
    }));
  }

  if (reorderPlan.length) {
    return reorderPlan.slice(0, 6).map((item, index) => ({
      id: `REQ-${index + 1}`,
      supplier: item.name,
      status: item.tone === "danger" ? "создать заявку" : "запланировать",
      eta: "сегодня",
      amount: item.qty,
      tone: item.tone,
    }));
  }

  return fallback;
}

function adaptSupplierPulse(reorderPlan: ReorderItem[], fallback: SupplierPulse[]): SupplierPulse[] {
  if (!reorderPlan.length) {
    return fallback;
  }

  const danger = reorderPlan.filter((item) => item.tone === "danger").length;
  const warn = reorderPlan.filter((item) => item.tone === "warn").length;
  const calm = Math.max(reorderPlan.length - danger - warn, 0);

  return [
    { name: "Срочные позиции", metric: `${formatNumber(danger)} SKU`, detail: "создать поставку", tone: danger ? "danger" : "good" },
    { name: "Плановые позиции", metric: `${formatNumber(warn)} SKU`, detail: "проверить окно", tone: warn ? "warn" : "good" },
    { name: "Без давления", metric: `${formatNumber(calm)} SKU`, detail: "можно отложить", tone: "info" },
  ];
}

function adaptSupplierPayload(rows: Array<{ name?: string; active?: number | boolean; status?: string }>): SupplierPulse[] {
  const active = rows.filter((row) => row.active === undefined || row.active === true || row.active === 1).length;
  return rows.slice(0, 6).map((row) => ({
    name: text(row.name, "Поставщик"),
    metric: row.status ? text(row.status, "активен") : (row.active === false || row.active === 0 ? "неактивен" : "активен"),
    detail: `${formatNumber(active)} активных в справочнике`,
    tone: row.active === false || row.active === 0 ? "info" : "good",
  }));
}

function adaptAssistantMetrics(actions: ActionRecommendation[], fallback: DashboardMetric[]): DashboardMetric[] {
  if (!actions.length) {
    return fallback;
  }

  const urgent = actions.filter((action) => action.tone === "danger").length;
  const warn = actions.filter((action) => action.tone === "warn").length;

  return [
    { label: "Решения", value: formatNumber(actions.length), delta: `${formatNumber(urgent)} срочно`, detail: "в очереди агента", tone: urgent ? "danger" : warn ? "warn" : "good" },
    { label: "Автоправила", value: "нет данных", delta: "API не передал", detail: "настройки агента", tone: "info" },
    { label: "Ручной контроль", value: formatNumber(warn + urgent), delta: "нужен владелец", detail: "до автодействий", tone: warn + urgent ? "warn" : "good" },
    { label: "Экономия", value: "нет данных", delta: "API не передал", detail: "оценка времени", tone: "info" },
  ];
}

function adaptAutomations(actions: ActionRecommendation[], fallback: AssistantAutomation[]): AssistantAutomation[] {
  if (!actions.length) {
    return fallback;
  }

  const byTone = [
    actions.find((action) => action.tone === "danger"),
    actions.find((action) => action.tone === "warn"),
    actions.find((action) => action.tone === "good" || action.tone === "info"),
  ].filter(Boolean) as ActionRecommendation[];

  return byTone.map((action) => ({
    title: action.title,
    detail: action.issue,
    status: action.owner,
    tone: action.tone,
  }));
}

function adaptAgentCandidates(rows: Array<Record<string, unknown>>): AgentCandidate[] {
  return rows.slice(0, 50).map((row) => ({
    id: Number(row.id ?? 0),
    status: text(row.status, "pending"),
    agent: text(row.agent, "agent"),
    marketplace: text(row.marketplace, "marketplace"),
    targetId: text(row.target_id, ""),
    targetName: text(row.target_name ?? row.target_id, "Цель"),
    action: text(row.action, "Проверить"),
    currentValue: text(row.current_value, "-"),
    proposedValue: text(row.proposed_value, "-"),
    reason: text(row.reason, "Без пояснения"),
  }));
}

function adaptProductEconomics(rows: Array<Record<string, unknown>>): ProductEconomicsRow[] {
  return rows.map((row) => ({
    nmId: String(row.nm_id ?? row.article ?? ""), article: text(row.article, ""), product: text(row.product, "Товар"),
    orders: numberValue(row.orders), sales: numberValue(row.sales), revenue: numberValue(row.revenue), buyout: numberValue(row.buyout),
    adSpend: numberValue(row.ad_spend), drr: numberValue(row.drr), stock: numberValue(row.stock), dailySales: numberValue(row.daily_sales),
    daysOfStock: typeof row.days_of_stock === "number" && Number.isFinite(row.days_of_stock) ? row.days_of_stock : null, unitCost: numberValue(row.unit_cost), margin: numberValue(row.margin),
  }));
}

function numberValue(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function adaptReportMetrics(overview: ApiOverviewResponse | undefined, fallback: DashboardMetric[]): DashboardMetric[] {
  if (!overview?.kpis?.length) {
    return fallback;
  }

  return [
    { label: "KPI", value: formatNumber(overview.kpis.length), delta: "в отчете", detail: text(overview.period_label, "период"), tone: "good" },
    { label: "Товары", value: formatNumber(overview.products?.length ?? 0), delta: "строк", detail: "для экспорта", tone: "info" },
    { label: "Аномалии", value: formatNumber(overview.problem_products?.length ?? 0), delta: "проверить", detail: "problem products", tone: overview.problem_products?.length ? "warn" : "good" },
    { label: "График", value: formatNumber(overview.chart?.rows?.length ?? 0), delta: "точек", detail: "выручка и заказы", tone: "info" },
  ];
}

function adaptReportCards(
  overview: ApiOverviewResponse | undefined,
  stock: ApiStockResponse | undefined,
  ads: ApiAdsResponse | undefined,
  fallback: ReportCard[],
): ReportCard[] {
  if (!overview?.kpis?.length) {
    return fallback;
  }

  return [
    {
      title: "Сводка собственника",
      detail: `KPI, funnel и ${formatNumber(overview.problem_products?.length ?? 0)} проблемных товаров`,
      period: text(overview.period_label, "30 дней"),
      status: "готово",
      tone: overview.problem_products?.length ? "warn" : "good",
    },
    {
      title: "Остатки и пополнение",
      detail: `${formatNumber(stock?.reorder_plan?.length ?? 0)} SKU в плане пополнения`,
      period: "сегодня",
      status: stock?.reorder_plan?.length ? "обновляется" : "готово",
      tone: stock?.reorder_plan?.some((item) => item.tone === "danger") ? "danger" : "warn",
    },
    {
      title: "Реклама и ставки",
      detail: `${formatNumber(ads?.campaigns?.length ?? 0)} товарных строк для контроля`,
      period: text(overview.period_label, "30 дней"),
      status: "готово",
      tone: ads?.campaigns?.some((item) => item.tone !== "good") ? "warn" : "good",
    },
  ];
}

function adaptExportItems(overview: ApiOverviewResponse | undefined, fallback: ExportItem[]): ExportItem[] {
  if (!overview) {
    return fallback;
  }

  const period = text(overview.period_label, "period").replace(/\s+/g, "_").toLocaleLowerCase("ru-RU");
  return [
    { name: `owner_summary_${period}.pdf`, format: "PDF", updated: "сейчас", tone: "good" },
    { name: `products_${period}.xlsx`, format: "XLSX", updated: `${formatNumber(overview.products?.length ?? 0)} строк`, tone: "info" },
    { name: `alerts_${period}.csv`, format: "CSV", updated: `${formatNumber(overview.alerts?.length ?? 0)} сигналов`, tone: overview.alerts?.length ? "warn" : "good" },
  ];
}

function overviewMetricCard(
  metric: ApiOverviewKpi | undefined,
  fallback: DashboardMetric | undefined,
  label: string,
  detail: string,
  lowerIsBetter = false,
): DashboardMetric {
  return {
    label,
    value: text(metric?.value, fallback?.value ?? "0"),
    delta: overviewDeltaText(metric, fallback?.delta ?? ""),
    detail: text(metric?.note, fallback?.detail ?? detail),
    tone: overviewTone(metric, lowerIsBetter, fallback?.tone ?? "info"),
  };
}

function findOverviewKpi(overview: ApiOverviewResponse | undefined, key: string): ApiOverviewKpi | undefined {
  return overview?.kpis?.find((metric) => metric.key === key);
}

function overviewDeltaText(metric: ApiOverviewKpi | undefined, fallback: string): string {
  if (typeof metric?.delta === "number") {
    const sign = metric.delta > 0 ? "+" : "";
    return `${sign}${formatNumber(metric.delta)}% к прошлому`;
  }

  return fallback;
}

function overviewTone(metric: ApiOverviewKpi | undefined, lowerIsBetter = false, fallback: Tone = "info"): Tone {
  if (metric?.tone) {
    return normalizeTone(metric.tone, fallback);
  }

  if (typeof metric?.delta !== "number") {
    return fallback;
  }

  if (lowerIsBetter) {
    return metric.delta <= 0 ? "good" : "warn";
  }

  return metric.delta >= 0 ? "good" : "warn";
}

function signedAmount(value: string, positive: boolean): string {
  if (!value || value.startsWith("+") || value.startsWith("-")) {
    return value;
  }

  return `${positive ? "+" : "-"}${value}`;
}

function splitRow(label: string, amount: number, total: number, tone: Tone): ExpenseSplit {
  const share = total > 0 ? Math.max(4, Math.min(100, (amount / total) * 100)) : 4;
  return {
    label,
    value: `${formatNumber((amount / Math.max(total, 1)) * 100)}%`,
    width: share,
    tone,
  };
}

function moneyStringToNumber(value: string | undefined): number {
  if (!value) {
    return 0;
  }

  return Number(value.replace(/\s+/g, "").replace(",", ".").replace(/[^0-9.-]/g, "")) || 0;
}

function statusTone(status: string | undefined): Tone {
  const normalized = status?.toLocaleLowerCase("ru-RU") ?? "";

  if (normalized.includes("задерж") || normalized.includes("проср")) {
    return "danger";
  }
  if (normalized.includes("оплат") || normalized.includes("ожида")) {
    return "warn";
  }
  if (normalized.includes("готов") || normalized.includes("получ")) {
    return "good";
  }
  return "info";
}

function adaptTrend(input: ApiTodayResponse["revenue_trend"], fallback: number[]): number[] {
  if (!input?.length) {
    return fallback;
  }

  const values = input
    .map((point) => point.revenue_rub ?? point.plan_bps ?? (typeof point.revenue_kop === "number" ? point.revenue_kop / 100000 : 0))
    .filter((value) => Number.isFinite(value));

  return values.length ? values : fallback;
}

function syncFromApi(sync: ApiSync | undefined): DashboardSync {
  return {
    state: sync?.state ?? "fresh",
    source: sync?.source ?? "wb-api",
    updatedAt: sync?.updated_at ?? new Date().toISOString(),
    lagMinutes: sync?.lag_minutes ?? 0,
  };
}

function liveDetail(sync: ApiSync | undefined): string {
  if (typeof sync?.lag_minutes === "number") {
    return `Обновлено ${sync.lag_minutes} минут назад`;
  }

  return "Данные получены из tenant API";
}

function displayValue(value: unknown, fallback: string): string {
  if (typeof value === "string") {
    return value;
  }

  if (typeof value === "number") {
    return formatNumber(value);
  }

  if (value && typeof value === "object") {
    const boxed = value as { kind?: string; amount_kop?: number; value?: number; currency?: string };
    if (boxed.kind === "money" && typeof boxed.amount_kop === "number") {
      return formatMoney(boxed.amount_kop);
    }
    if (boxed.kind === "percent" && typeof boxed.value === "number") {
      return formatPercent(boxed.value);
    }
    if (typeof boxed.value === "number") {
      return formatNumber(boxed.value);
    }
  }

  return fallback;
}

function normalizeTone(value: ApiTone | undefined, fallback: Tone): Tone {
  return value === "good" || value === "warn" || value === "danger" || value === "info" ? value : fallback;
}

function priorityToTone(priority: string | undefined): Tone {
  if (priority === "Критично") {
    return "danger";
  }
  if (priority === "Высокий" || priority === "Средний") {
    return "warn";
  }
  return "info";
}

function priorityToDue(priority: string | undefined): string | undefined {
  if (priority === "Критично") {
    return "срочно";
  }
  if (priority === "Высокий") {
    return "сегодня";
  }
  return undefined;
}

function formatDue(value: string | undefined): string | undefined {
  if (!value) {
    return undefined;
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

function signedPercent(value: number): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${formatNumber(value / 100)}%`;
}

function formatPercent(value: number): string {
  return `${formatNumber(value / 100)}%`;
}

function formatMoney(kop: number): string {
  return `${formatNumber(Math.round(kop / 100))} ₽`;
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(value);
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function text(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function compactActionLabel(value: unknown, fallback: string): string {
  const raw = text(value, fallback);
  const normalized = raw.toLocaleLowerCase("ru-RU");

  if (normalized.includes("синхронизац")) {
    return "Синхронизировать";
  }
  if (normalized.includes("fifo")) {
    return "Открыть сверку FIFO";
  }
  if (normalized.includes("резервн") || normalized.includes("копи")) {
    return "Создать копию";
  }
  if (raw.length <= 28) {
    return raw;
  }

  const firstClause = raw.split(";")[0]?.trim();
  if (firstClause && firstClause.length <= 28) {
    return firstClause;
  }

  return `${raw.slice(0, 25).trim()}...`;
}
