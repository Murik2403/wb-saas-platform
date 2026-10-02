import { scopedFetch as fetch } from "./selected-shop";
import { API_BASE_URL, apiHeaders, type DashboardPeriod } from "./data-source";

export type BuyoutGroup = "day" | "week" | "month";
export type BuyoutRecord = {
  id: number; day: string; nm_id: number; vendor_code: string; units: number;
  buyout_amount_kop: number; cashback_kop: number; net_cost_kop: number;
  format: string | null; note: string | null; created_at: string; actor: string | null;
};
export type BuyoutTotals = { records: number; units: number; paid_kop: number; cashback_kop: number; net_cost_kop: number };
export type BuyoutDelta = { value: number; previous: number; delta: number; delta_pct: number | null };
export type BuyoutReport = {
  start: string; end: string; group: BuyoutGroup; nm_id: number | null;
  previous_window: { start: string; end: string } | null;
  totals: BuyoutTotals; previous_totals: BuyoutTotals;
  deltas: Record<"units" | "paid_kop" | "cashback_kop" | "net_cost_kop", BuyoutDelta>;
  groups: Array<BuyoutTotals & { start: string }>;
  records: BuyoutRecord[]; truncated: boolean;
};
export type BuyoutInput = { day: string; nm_id: number; units: number; buyout_amount_kop: number; cashback_kop: number; format: string | null; note: string | null };

/** Server-side field errors (422 detail.errors) keep their field names so the form can show them next to inputs. */
export class BuyoutError extends Error {
  fields: Record<string, string>;
  constructor(message: string, fields: Record<string, string> = {}) { super(message); this.fields = fields; }
}

async function request<T>(path: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE_URL}/api/v1/self-buyouts${path}`, {
    method, headers: apiHeaders(body !== undefined), body: body === undefined ? undefined : JSON.stringify(body), signal,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({})) as { detail?: unknown };
    const detail = error.detail as { errors?: Record<string, string> } | string | undefined;
    if (detail && typeof detail === "object" && detail.errors) throw new BuyoutError("Проверьте поля формы", detail.errors);
    throw new BuyoutError(typeof detail === "string" ? detail : `Не удалось выполнить запрос (HTTP ${response.status})`);
  }
  return response.json() as Promise<T>;
}

export function getSelfBuyouts(period: DashboardPeriod, group: BuyoutGroup, signal?: AbortSignal): Promise<BuyoutReport> {
  const query = new URLSearchParams({ period: period.id, group });
  if (period.id === "custom" && period.start && period.end) { query.set("start", period.start); query.set("end", period.end); }
  return request<BuyoutReport>(`?${query}`, "GET", undefined, signal);
}
export const createSelfBuyout = (input: BuyoutInput) => request<BuyoutRecord>("", "POST", input);
export const updateSelfBuyout = (id: number, input: BuyoutInput) => request<BuyoutRecord>("/update", "POST", { ...input, id });
export const deleteSelfBuyout = (id: number) => request<{ deleted: number }>("/delete", "POST", { id });

/** "1 234,50" / "1234.5" -> kopecks; null for negatives, garbage, more than 2 decimals. Never goes through floats. */
export function parseRubToKop(text: string): number | null {
  const clean = text.trim().replace(/[\s ]/g, "");
  const match = /^(\d{1,9})(?:[.,](\d{1,2}))?$/.exec(clean);
  return match ? Number(match[1]) * 100 + Number((match[2] ?? "").padEnd(2, "0") || 0) : null;
}

export const formatRub = (kop: number) => `${new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(kop / 100)} ₽`;
