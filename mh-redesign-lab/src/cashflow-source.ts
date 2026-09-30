import { scopedFetch as fetch } from "./selected-shop";
import { API_BASE_URL, apiHeaders } from "./data-source";
export type CashEntry = {
  id: number; kind: "opening" | "movement"; direction: "inflow" | "outflow";
  status: "planned" | "actual" | "cancelled"; planned_date: string; actual_date: string | null;
  amount_minor: number; amount: string; currency: string; exchange_rate: string;
  category: string; reference: string;
};
export type CashReport = {
  items: CashEntry[]; events: Array<{id: number; entry_id: number; status: string; actual_date: string | null; changed_at: string; actor: string | null}>;
  opening_balance_minor: number; actual_closing_minor: number; forecast_closing_minor: number;
  actual_inflow_minor: number; actual_outflow_minor: number; overdue_planned_minor: number;
  opening_confirmed: boolean; period_before_opening: boolean;
  daily: Array<{date: string; actual_balance_minor: number; forecast_balance_minor: number}>;
};
async function request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}/api/v1/cashflow${path}`, {
    method, headers: apiHeaders(body !== undefined), body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({})) as { detail?: unknown };
    throw new Error(typeof error.detail === "string" ? error.detail : "Не удалось сохранить движение денег. Проверьте данные и доступ.");
  }
  return response.json() as Promise<T>;
}
export const getCashflow = (start: string, end: string) => request<CashReport>(`?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`);
export const createCashEntry = (body: unknown) => request<CashEntry>("/entries", "POST", body);
export const changeCashEntry = (id: number, status: string, actual_date?: string) => request<CashEntry>(`/entries/${id}`, "PATCH", { status, actual_date });
