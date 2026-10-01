import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { ProductsView } from "./App";
import * as ds from "./data-source";

vi.mock("./data-source", async (load) => ({
  ...await load<typeof import("./data-source")>(),
  getPriceUploadHistory: vi.fn(async () => []),
  getProductsPrevious: vi.fn(),
}));
afterEach(cleanup);

const row = (nmId: string, revenue: number, margin: number, daysOfStock: number | null): ds.ProductEconomicsRow => ({
  nmId, article: `A${nmId}`, product: `Товар ${nmId}`, orders: 1, sales: 1, revenue, buyout: 100, adSpend: 0, drr: 0, stock: 1,
  dailySales: 1, daysOfStock, unitCost: 1, margin, marginPct: 0,
});
// revenue 1500, profit 150 => margin 10%, 1 SKU at risk, 2 SKU
const products = [row("1", 1000, 100, 3), row("2", 500, 50, 30)];
const view = () => render(<ProductsView period={{ id: "30d" }} products={products} prices={[]} productCards={[]} productCardErrors={[]} productPriceErrors={[]}
  customerQuality={null} onRefresh={vi.fn(async () => {})} setPeriod={vi.fn()} onOpenProduct={vi.fn()} />);
const prev = (p: Partial<NonNullable<ds.ProductsPreviousTotals["totals"]>> | null): ds.ProductsPreviousTotals => ({
  start: "2026-08-01", end: "2026-08-30", hasData: !!p,
  totals: p ? { revenue: 1000, marginPct: 8, atRisk: 2, skuCount: 2, ...p } : null,
});
const nb = (s: string | null) => (s ?? "").replace(/ | /g, " ");

describe("Products KPI deltas", () => {
  it("renders tiles immediately and adds deltas when the previous period arrives", async () => {
    vi.mocked(ds.getProductsPrevious).mockResolvedValue(prev({}));
    view();
    expect(screen.getByText("Выручка")).toBeVisible();
    await waitFor(() => expect(screen.getByText(/\+500 ₽ \(\+50%\) к прошлому периоду/, { normalizer: nb })).toBeVisible());
    expect(screen.getByText("+2 п.п. к прошлому периоду")).toBeVisible();
    expect(screen.getByText("−1 SKU к прошлому периоду")).toBeVisible(); // risk 2 -> 1: fewer is good
    expect(document.querySelectorAll(".metric-change.good")).toHaveLength(3); // revenue, margin, risk; SKU count is neutral
  });
  it("shows no extra text when the request fails", async () => {
    vi.mocked(ds.getProductsPrevious).mockRejectedValue(new Error("HTTP 500"));
    const { container } = view();
    await new Promise((r) => setTimeout(r, 20));
    expect(container.querySelector(".metric-change")).toBeNull();
    expect(screen.queryByText(/ошибк/i)).toBeNull();
  });
  it("says there is no previous data when the previous period is empty or zero", async () => {
    vi.mocked(ds.getProductsPrevious).mockResolvedValue(prev(null));
    const { container } = view();
    await waitFor(() => expect(container.querySelectorAll(".metric-change")).toHaveLength(4));
    for (const el of container.querySelectorAll(".metric-change")) expect(el).toHaveTextContent("нет данных за прошлый период");
  });
  it("zero previous revenue: no percentage, margin says no data", async () => {
    vi.mocked(ds.getProductsPrevious).mockResolvedValue(prev({ revenue: 0, marginPct: 0, atRisk: 0, skuCount: 2 }));
    const { container } = view();
    await waitFor(() => expect(container.querySelectorAll(".metric-change")).toHaveLength(4));
    const texts = [...container.querySelectorAll(".metric-change")].map((e) => e.textContent);
    expect(texts[0]).toBe("нет данных за прошлый период");
    expect(texts[1]).toBe("нет данных за прошлый период");
    expect(texts[2]).toBe("+1 SKU к прошлому периоду");
    expect(container.querySelectorAll(".metric-change.bad")).toHaveLength(1);
  });
});
