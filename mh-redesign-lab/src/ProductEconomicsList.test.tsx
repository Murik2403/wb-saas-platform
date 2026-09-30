import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ProductEconomicsList } from "./ProductEconomicsList";
import type { ProductEconomicsRow } from "./data-source";

const row = (over: Partial<ProductEconomicsRow>): ProductEconomicsRow => ({
  nmId: "1", article: "NAP-1", product: "Салфетки на стол", orders: 10, sales: 8, revenue: 1000, buyout: 80,
  adSpend: 50, drr: 5, stock: 12, dailySales: 1, daysOfStock: 12, unitCost: 100, margin: 300, marginPct: 30, ...over,
});
const items = [row({}), row({ nmId: "2", article: "NAP-2", product: "Коврик", margin: -200, marginPct: -20, daysOfStock: null })];

beforeEach(() => window.localStorage.clear());
afterEach(cleanup);

describe("ProductEconomicsList", () => {
  it("shows tiles by default and switches to table and list", () => {
    render(<ProductEconomicsList items={items} onOpen={() => undefined} />);
    expect(screen.getAllByRole("button", { name: /Салфетки на стол/ }).length).toBeGreaterThan(0);
    expect(screen.queryByRole("table")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Таблица" }));
    expect(screen.getByRole("table")).toBeTruthy();
    expect(screen.getAllByRole("row")).toHaveLength(3); // header + 2 products

    fireEvent.click(screen.getByRole("button", { name: "Список" }));
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("opens the product from every view", () => {
    const onOpen = vi.fn();
    render(<ProductEconomicsList items={items} onOpen={onOpen} />);
    for (const mode of ["Плитка", "Таблица", "Список"]) {
      fireEvent.click(screen.getByRole("button", { name: mode }));
      fireEvent.click(screen.getAllByRole("button", { name: /Коврик/ })[0]);
    }
    expect(onOpen).toHaveBeenCalledTimes(3);
    expect(onOpen).toHaveBeenLastCalledWith(items[1]);
  });

  it("remembers the choice and survives a broken localStorage", () => {
    const first = render(<ProductEconomicsList items={items} onOpen={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "Таблица" }));
    first.unmount();
    render(<ProductEconomicsList items={items} onOpen={() => undefined} />);
    expect(screen.getByRole("table")).toBeTruthy();
    cleanup();

    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    render(<ProductEconomicsList items={items} onOpen={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "Список" }));
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    vi.restoreAllMocks();
  });

  it("formats missing forecast and negative margin", () => {
    render(<ProductEconomicsList items={items} onOpen={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "Таблица" }));
    expect(screen.getByText(/прогноз недоступен/)).toBeTruthy();
    expect(screen.getByText("-20.0%").className).toContain("negative");
  });
});
