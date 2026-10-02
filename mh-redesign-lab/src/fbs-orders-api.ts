import { apiHeaders, API_BASE_URL } from "./data-source";
import { scopedFetch as fetch } from "./selected-shop";
import { writeError } from "./write-error";

export type FbsState = {
  last_run_at: string | null; last_success_at: string | null; last_error: string | null;
  min_interval_seconds: number; retry_after_seconds: number; has_marketplace_scope: boolean | null;
  overdue_hours: number; orders_total: number;
};
export type FbsOrder = {
  order_id: number; nm_id: number | null; article: string | null; created_at: string | null; price: number | null;
  warehouse_id: number | null; supply_id: string | null; supplier_status: string | null; wb_status: string | null;
  state: string; state_label: string; age_hours: number | null; overdue: boolean;
};
export type FbsOrders = {
  items: FbsOrder[]; total: number; limit: number; offset: number; days: number; overdue_hours: number;
  warehouses: Array<{ id: number; count: number }>;
};
export type FbsAnalytics = {
  days: number; total: number; by_status: Array<{ key: string; label: string; count: number }>; final_count: number;
  buyout_pct: number | null; avg_delivery_hours: number | null; avg_delivery_sample: number; overdue_count: number;
  by_day: Array<{ day: string; total: number; delivered: number; cancelled: number }>; estimate_note: string;
};
export class FbsTooSoon extends Error {
  constructor(message: string, readonly retryAfter: number) { super(message); this.name = "FbsTooSoon"; }
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, { headers: apiHeaders(), signal });
  if (!response.ok) throw new Error(`FBS-заказы: данные недоступны (код ${response.status})`);
  return response.json() as Promise<T>;
}

export type FbsFilters = { status: string; overdue: boolean; warehouse: string; days: number; offset: number };

export function getFbsState(signal?: AbortSignal) { return get<FbsState>("/api/v1/fbs/state", signal); }
export function getFbsAnalytics(days: number, signal?: AbortSignal) {
  return get<FbsAnalytics>(`/api/v1/fbs/analytics?period=${days <= 1 ? "today" : `${days}d`}`, signal);
}
export function getFbsOrders(f: FbsFilters, limit = 50, signal?: AbortSignal) {
  const q = new URLSearchParams({ days: String(f.days), limit: String(limit), offset: String(f.offset) });
  if (f.status) q.set("status", f.status);
  if (f.overdue) q.set("overdue", "true");
  if (f.warehouse) q.set("warehouse", f.warehouse);
  return get<FbsOrders>(`/api/v1/fbs/orders?${q}`, signal);
}
export async function refreshFbs(days: number): Promise<FbsState> {
  const response = await fetch(`${API_BASE_URL}/api/v1/fbs/refresh?days=${days}`, { method: "POST", headers: apiHeaders(true) });
  if (response.status === 429) {
    const payload = await response.json().catch(() => null) as { detail?: string; state?: FbsState } | null;
    throw new FbsTooSoon(payload?.detail ?? "Обновление пока недоступно, повторите позже.", payload?.state?.retry_after_seconds ?? 300);
  }
  if (!response.ok) throw await writeError(response);
  return response.json() as Promise<FbsState>;
}
