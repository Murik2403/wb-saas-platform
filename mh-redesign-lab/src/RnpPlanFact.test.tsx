import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RnpPlanFact } from "./RnpPlanFact";
import type { Rnp, RnpDay, RnpItem } from "./rnp-data";
import type { ProductEconomicsRow } from "./data-source";

const day = (d: string, over: Partial<RnpDay> = {}): RnpDay => ({
  day: d, is_future: false, is_today: false, plan_orders: null, fact_orders: 3, fact_orders_sum: 3000, fact_sales: 2, fact_sales_sum: 2000,
  forecast_sales: 1.5, deviation_abs: null, deviation_pct: null, price_before: 1250, price_after: 1000, ad_spend: null, drr_pct: null, stock: null, ...over,
});
const item = (over: Partial<RnpItem> = {}): RnpItem => ({
  nm_id: 1, vendor_code: "A-1", current_price: 1250, current_discount: 20, current_final: 1000, stock_now: 45, stock_snapshot_at: null,
  margin_status: "ok", margin_note: "", breakeven_final: 875,
  days: [day("2026-09-08", { plan_orders: 2, deviation_abs: 1, deviation_pct: 50 }), day("2026-09-09", { plan_orders: 5, deviation_abs: -2, deviation_pct: -40 }),
    day("2026-09-10", { is_today: true }), day("2026-09-11", { is_future: true, fact_orders: null, fact_sales: null, fact_orders_sum: null, fact_sales_sum: null })],
  totals: { plan_orders: 7, fact_orders: 6, fact_orders_sum: 6000, fact_sales: 4, fact_sales_sum: 4000, forecast_sales: 6, deviation_abs: -1, deviation_pct: -14.3, ad_spend: null, drr_pct: null },
  plan_margin_pct: 12, forecast_margin_pct: 10, margin_gap_pp: -2, ...over,
});
const body = (items: RnpItem[], favorites = items.map((i) => i.nm_id)): Rnp => ({
  start: "2026-09-08", end: "2026-09-11", today: "2026-09-10", capped: false, max_days: 31, max_favorites: 10,
  forecast_note: "прогноз = среднее за последние 7 дней; акции, сезонность и остатки не учитываются", favorites, items, ads_note: "Реклама по артикулам не загружена",
});
const products = [{ nmId: "1", article: "A-1", product: "Скатерть", }, { nmId: "2", article: "B-2", product: "Салфетка" }] as unknown as ProductEconomicsRow[];
const ok = (data: unknown) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(data) });
const fail = (status: number, detail: string) => Promise.resolve({ ok: false, status, json: () => Promise.resolve({ detail }) });
const session = (role: string) => ({ user: { id: "1", name: "x", role }, tenant: { id: "t", name: "t" }, mode: "live" });

function mockApi(role: string, rnp: Rnp = body([item()]), puts: Array<{ url: string; body: unknown }> = [], putResult: () => Promise<unknown> = () => ok({})) {
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (init?.method === "PUT") { puts.push({ url, body: JSON.parse(String(init.body)) }); return putResult(); }
    if (url.includes("/api/v1/session")) return ok(session(role));
    return ok(rnp);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
const openIt = async () => { fireEvent.click(screen.getByRole("button", { name: "Развернуть" })); await screen.findByText("A-1"); };

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });
beforeEach(() => { window.localStorage.clear(); });

describe("RnpPlanFact", () => {
  it("is lazy, then shows the exact forecast definition and what the forecast is NOT", async () => {
    const fetchMock = mockApi("owner");
    render(<RnpPlanFact products={products} />);
    expect(fetchMock).not.toHaveBeenCalled();
    await openIt();
    expect(screen.getByRole("note")).toHaveTextContent("прогноз = среднее за последние 7 дней; акции, сезонность и остатки не учитываются");
    expect(screen.getByRole("note")).toHaveTextContent(/не учитывает сезонность, акции, отсутствие товара на складе и зависимость спроса от цены/);
    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/v1/dashboard/rnp?period=14d");
  });

  it("shows signed deviation with a symbol, dashes for missing values and a margin gap", async () => {
    mockApi("viewer");
    render(<RnpPlanFact products={products} />);
    await openIt();
    expect(screen.getAllByText("▲ +1").length).toBeGreaterThan(0);
    expect(screen.getAllByText("▼ −2").length).toBeGreaterThan(0);
    expect(screen.getByText("▼ −14,3%")).toBeInTheDocument();
    expect(screen.getByText("▼ −2 п.п.")).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(5);
    // viewer: read-only (no inputs, no pin box, no unpin)
    await waitFor(() => expect(screen.getByText(/Просмотр: закреплять/)).toBeInTheDocument());
    expect(screen.queryByRole("spinbutton")).toBeNull();
    expect(screen.queryByRole("button", { name: /Открепить/ })).toBeNull();
  });

  it("owner edits a plan cell inline: saves on blur only the changed day, shows the status and keeps focus", async () => {
    const puts: Array<{ url: string; body: unknown }> = [];
    mockApi("owner", body([item()]), puts);
    render(<RnpPlanFact products={products} />);
    await openIt();
    const input = await screen.findByLabelText(/План заказов A-1 на 10\.09/);
    input.focus();
    fireEvent.change(input, { target: { value: "7" } });
    fireEvent.blur(input);
    expect(await screen.findByText("✓ Сохранено")).toBeInTheDocument();
    expect(puts).toEqual([{ url: expect.stringContaining("/api/v1/dashboard/rnp/plan"), body: { nm_id: 1, plan: { "2026-09-10": 7 } } }]);
    expect((screen.getByLabelText(/План заказов A-1 на 10\.09/) as HTMLInputElement).value).toBe("7");
  });

  it("Enter saves, an empty cell clears the plan, an unchanged value sends nothing, an invalid one is rejected locally", async () => {
    const puts: Array<{ url: string; body: unknown }> = [];
    mockApi("manager", body([item()]), puts);
    render(<RnpPlanFact products={products} />);
    await openIt();
    const filled = await screen.findByLabelText(/План заказов A-1 на 08\.09/);
    fireEvent.change(filled, { target: { value: "" } });
    fireEvent.keyDown(filled, { key: "Enter" });
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0].body).toEqual({ nm_id: 1, plan: { "2026-09-08": null } });
    const same = screen.getByLabelText(/План заказов A-1 на 09\.09/);
    fireEvent.change(same, { target: { value: "5" } });
    fireEvent.blur(same);
    const bad = screen.getByLabelText(/План заказов A-1 на 11\.09/);
    fireEvent.change(bad, { target: { value: "100001" } });
    fireEvent.blur(bad);
    expect(await screen.findByText(/Целое число от 0 до 100\s?000/)).toBeInTheDocument();
    expect(puts).toHaveLength(1);
  });

  it("debounces typing into one save", async () => {
    const puts: Array<{ url: string; body: unknown }> = [];
    mockApi("owner", body([item()]), puts);
    render(<RnpPlanFact products={products} />);
    await openIt();
    const input = await screen.findByLabelText(/План заказов A-1 на 10\.09/);
    vi.useFakeTimers();
    fireEvent.change(input, { target: { value: "1" } });
    fireEvent.change(input, { target: { value: "12" } });
    expect(puts).toHaveLength(0);
    await act(async () => { await vi.advanceTimersByTimeAsync(900); });
    expect(puts).toHaveLength(1);
    expect(puts[0].body).toEqual({ nm_id: 1, plan: { "2026-09-10": 12 } });
  });

  it("shows a server error on the cell and keeps what the user typed", async () => {
    mockApi("owner", body([item()]), [], () => fail(422, "Сначала закрепите артикул"));
    render(<RnpPlanFact products={products} />);
    await openIt();
    const input = await screen.findByLabelText(/План заказов A-1 на 10\.09/);
    fireEvent.change(input, { target: { value: "9" } });
    fireEvent.blur(input);
    expect(await screen.findByRole("alert")).toHaveTextContent("Сначала закрепите артикул");
    expect((screen.getByLabelText(/План заказов A-1 на 10\.09/) as HTMLInputElement).value).toBe("9");
  });

  it("pins from the search list (max 10) and unpins", async () => {
    const puts: Array<{ url: string; body: unknown }> = [];
    mockApi("owner", body([item()]), puts);
    render(<RnpPlanFact products={products} />);
    await openIt();
    const box = await screen.findByLabelText(/Закрепить артикул \(1 из 10\)/);
    fireEvent.change(box, { target: { value: "салф" } });
    fireEvent.click(await screen.findByRole("button", { name: /B-2/ }));
    await waitFor(() => expect(puts[0]?.body).toEqual({ nm_ids: [1, 2] }));
    fireEvent.click(screen.getByRole("button", { name: "Открепить A-1" }));
    await waitFor(() => expect(puts[1]?.body).toEqual({ nm_ids: [] }));
  });

  it("saves the plan margin and shows the gap; empty state and error state are explicit", async () => {
    const puts: Array<{ url: string; body: unknown }> = [];
    mockApi("owner", body([item()]), puts);
    render(<RnpPlanFact products={products} />);
    await openIt();
    const margin = await screen.findByLabelText("Плановая маржа A-1, %");
    fireEvent.change(margin, { target: { value: "15" } });
    fireEvent.blur(margin);
    await waitFor(() => expect(puts[0]?.body).toEqual({ nm_id: 1, plan_margin_pct: 15 }));
    expect(await screen.findByText("▼ −5 п.п.")).toBeInTheDocument();
    cleanup();
    mockApi("owner", body([]));
    render(<RnpPlanFact products={products} />);
    fireEvent.click(screen.getByRole("button", { name: "Развернуть" }));
    expect(await screen.findByText(/Артикулы не закреплены/)).toBeInTheDocument();
    cleanup();
    vi.stubGlobal("fetch", vi.fn(() => fail(500, "boom")));
    render(<RnpPlanFact products={products} />);
    fireEvent.click(screen.getByRole("button", { name: "Развернуть" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить план-факт");
  });

  it("ignores a stale response when the period changes quickly", async () => {
    const slow = body([item({ vendor_code: "OLD" })]);
    const resolvers: Array<(v: unknown) => void> = [];
    vi.stubGlobal("fetch", vi.fn((url: string) => url.includes("/session") ? ok(session("owner")) : new Promise((resolve) => resolvers.push(resolve))));
    render(<RnpPlanFact products={products} />);
    fireEvent.click(screen.getByRole("button", { name: "Развернуть" }));
    await waitFor(() => expect(resolvers).toHaveLength(1));
    fireEvent.change(screen.getByLabelText("Период"), { target: { value: "30" } });
    await waitFor(() => expect(resolvers).toHaveLength(2));
    await act(async () => { resolvers[1]({ ok: true, status: 200, json: () => Promise.resolve(body([item({ vendor_code: "NEW" })])) }); });
    await act(async () => { resolvers[0]({ ok: true, status: 200, json: () => Promise.resolve(slow) }); });
    expect(await screen.findByText("NEW")).toBeInTheDocument();
    expect(screen.queryByText("OLD")).toBeNull();
  });
});
