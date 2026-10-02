import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FbsOrdersPanel, fmtAge, fmtPct } from "./FbsOrdersPanel";

const state = (o: Record<string, unknown> = {}) => ({ last_run_at: "2026-09-10T10:00:00Z", last_success_at: "2026-09-10T10:00:00Z", last_error: null, min_interval_seconds: 300, retry_after_seconds: 0, has_marketplace_scope: true, overdue_hours: 24, orders_total: 2, ...o });
const order = (o: Record<string, unknown>) => ({ order_id: 1, nm_id: 5, article: "ART-1", created_at: "2026-09-10T08:00:00Z", price: 1000, warehouse_id: 7, supply_id: null, supplier_status: "new", wb_status: "waiting", state: "processing", state_label: "Сборка", age_hours: 12, overdue: false, ...o });
const orders = (items = [order({}), order({ order_id: 2, overdue: true, age_hours: 50 })]) => ({ items, total: items.length, limit: 50, offset: 0, days: 14, overdue_hours: 24, warehouses: [{ id: 7, count: 2 }] });
const stats = (o: Record<string, unknown> = {}) => ({ days: 14, total: 2, by_status: [{ key: "processing", label: "Сборка", count: 2 }, { key: "delivered", label: "Выкуплен", count: 0 }], final_count: 0, buyout_pct: null, avg_delivery_hours: null, avg_delivery_sample: 0, overdue_count: 1, by_day: [{ day: "2026-09-10", total: 2, delivered: 0, cancelled: 0 }], estimate_note: "оценка", ...o });

function stub(routes: Record<string, () => { status?: number; body: unknown }>) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const key = Object.keys(routes).find((k) => (k.startsWith("POST ") ? "POST" : "GET") === method && url.includes(k.replace("POST ", "")));
    const r = key ? routes[key]() : { status: 404, body: {} };
    const status = r.status ?? 200;
    return { ok: status < 400, status, json: async () => r.body, text: async () => JSON.stringify(r.body) };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}
const base = { "/fbs/state": () => ({ body: state() }), "/fbs/orders": () => ({ body: orders() }), "/fbs/analytics": () => ({ body: stats() }) };

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("FbsOrdersPanel", () => {
  it("formats age and percent, null as a dash", () => {
    expect(fmtAge(null)).toBe("—");
    expect(fmtAge(30)).toBe("30 ч");
    expect(fmtAge(50)).toBe("2 д 2 ч");
    expect(fmtPct(null)).toBe("—");
    expect(fmtPct(40)).toBe("40 %");
  });

  it("is collapsed and fetches nothing until opened", async () => {
    const fn = stub(base);
    render(<FbsOrdersPanel canWrite />);
    expect(fn).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "FBS-заказы WB" }));
    await screen.findAllByText("ART-1");
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it("marks overdue orders with text and shows dashes when not computable", async () => {
    stub(base);
    render(<FbsOrdersPanel canWrite />);
    await userEvent.click(screen.getByRole("button", { name: "FBS-заказы WB" }));
    expect(await screen.findByText("⚠ просрочен")).toBeTruthy();
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2); // buyout and avg time
    expect(screen.getByText("нет завершённых")).toBeTruthy();
  });

  it("shows a calm message when the token lacks the category and disables refresh", async () => {
    stub({ ...base, "/fbs/state": () => ({ body: state({ has_marketplace_scope: false }) }) });
    render(<FbsOrdersPanel canWrite />);
    await userEvent.click(screen.getByRole("button", { name: "FBS-заказы WB" }));
    expect(await screen.findByText(/категории «Маркетплейс»/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Обновить" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("hides refresh for read-only roles", async () => {
    stub(base);
    render(<FbsOrdersPanel canWrite={false} />);
    await userEvent.click(screen.getByRole("button", { name: "FBS-заказы WB" }));
    await screen.findAllByText("ART-1");
    expect(screen.queryByRole("button", { name: "Обновить" })).toBeNull();
  });

  it("handles a too-soon 429 without an error page", async () => {
    stub({ ...base, "POST /fbs/refresh": () => ({ status: 429, body: { detail: "Обновление возможно раз в 5 мин. Повторите через 120 с.", state: state({ retry_after_seconds: 120 }) } }) });
    render(<FbsOrdersPanel canWrite />);
    await userEvent.click(screen.getByRole("button", { name: "FBS-заказы WB" }));
    await screen.findAllByText("ART-1");
    await userEvent.click(screen.getByRole("button", { name: "Обновить" }));
    expect(await screen.findByText(/раз в 5 мин/)).toBeTruthy();
    expect((screen.getByRole("button", { name: /подождите/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows an error state when loading fails and an empty state with no orders", async () => {
    stub({ ...base, "/fbs/orders": () => ({ status: 500, body: {} }) });
    render(<FbsOrdersPanel canWrite />);
    await userEvent.click(screen.getByRole("button", { name: "FBS-заказы WB" }));
    expect(await screen.findByText(/данные недоступны/)).toBeTruthy();
    cleanup();
    stub({ ...base, "/fbs/state": () => ({ body: state({ orders_total: 0 }) }), "/fbs/orders": () => ({ body: orders([]) }) });
    render(<FbsOrdersPanel canWrite />);
    await userEvent.click(screen.getByRole("button", { name: "FBS-заказы WB" }));
    await waitFor(() => expect(screen.getByText(/FBS-заказов пока нет/)).toBeTruthy());
  });

  it("passes filters to the API", async () => {
    const fn = stub(base);
    render(<FbsOrdersPanel canWrite />);
    await userEvent.click(screen.getByRole("button", { name: "FBS-заказы WB" }));
    await screen.findAllByText("ART-1");
    await userEvent.click(screen.getByLabelText("Только просроченные"));
    await waitFor(() => expect(fn.mock.calls.some(([u]) => String(u).includes("/fbs/orders") && String(u).includes("overdue=true"))).toBe(true));
  });
});
