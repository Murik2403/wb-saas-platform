import { API_BASE_URL, apiHeaders } from "./data-source";

export type RnpDay = {
  day: string; is_future: boolean; is_today: boolean; plan_orders: number | null;
  fact_orders: number | null; fact_orders_sum: number | null; fact_sales: number | null; fact_sales_sum: number | null;
  forecast_sales: number | null; deviation_abs: number | null; deviation_pct: number | null;
  price_before: number | null; price_after: number | null; ad_spend: number | null; drr_pct: number | null; stock: number | null;
};
export type RnpTotals = {
  plan_orders: number | null; fact_orders: number; fact_orders_sum: number | null; fact_sales: number; fact_sales_sum: number | null;
  forecast_sales: number | null; deviation_abs: number | null; deviation_pct: number | null; ad_spend: number | null; drr_pct: number | null;
};
export type RnpItem = {
  nm_id: number; vendor_code: string; current_price: number | null; current_discount: number | null; current_final: number | null;
  stock_now: number | null; stock_snapshot_at: string | null; margin_status: string; margin_note: string; breakeven_final: number | null;
  days: RnpDay[]; totals: RnpTotals; plan_margin_pct: number | null; forecast_margin_pct: number | null; margin_gap_pp: number | null;
};
export type Rnp = {
  start: string; end: string; today: string; capped: boolean; max_days: number; max_favorites: number; forecast_note: string;
  favorites: number[]; items: RnpItem[]; ads_note: string | null;
};

const BASE = "/api/v1/dashboard/rnp";

async function failure(response: Response, fallback: string): Promise<Error> {
  let detail = "";
  try { const parsed = await response.json() as { detail?: unknown }; detail = typeof parsed.detail === "string" ? parsed.detail : ""; } catch { /* not JSON */ }
  return new Error(detail || `${fallback}: HTTP ${response.status}`);
}

export async function getRnp(days: number, signal: AbortSignal): Promise<Rnp> {
  const response = await fetch(`${API_BASE_URL}${BASE}?period=${days}d`, { headers: apiHeaders(), signal });
  if (!response.ok) throw await failure(response, "План-факт");
  return response.json() as Promise<Rnp>;
}

async function put(path: string, body: unknown): Promise<void> {
  const response = await fetch(`${API_BASE_URL}${BASE}${path}`, { method: "PUT", headers: apiHeaders(true), body: JSON.stringify(body) });
  if (!response.ok) throw await failure(response, "Не удалось сохранить");
}

export const saveRnpFavorites = (nmIds: number[]) => put("/favorites", { nm_ids: nmIds });
export const saveRnpPlan = (nmId: number, plan: Record<string, number | null>) => put("/plan", { nm_id: nmId, plan });
export const saveRnpTarget = (nmId: number, planMarginPct: number | null) => put("/target", { nm_id: nmId, plan_margin_pct: planMarginPct });
