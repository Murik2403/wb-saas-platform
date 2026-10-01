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

  it("sorts the table by column, toggles direction, and leaves tiles in API order", () => {
    const three = [row({ nmId: "1", product: "Б", margin: 100, marginPct: 10 }), row({ nmId: "2", product: "А", margin: 300, marginPct: 30 }), row({ nmId: "3", product: "В", margin: -50, marginPct: -5 })];
    render(<ProductEconomicsList items={three} onOpen={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "Таблица" }));
    const names = () => screen.getAllByRole("row").slice(1).map((r) => r.querySelector("strong")?.textContent);

    expect(names()).toEqual(["Б", "А", "В"]); // API order until a header is clicked
    fireEvent.click(screen.getByRole("button", { name: /Расчётная прибыль/ }));
    expect(names()).toEqual(["А", "Б", "В"]); // numbers: high to low first
    expect(screen.getByRole("columnheader", { name: /Расчётная прибыль/ }).getAttribute("aria-sort")).toBe("descending");
    fireEvent.click(screen.getByRole("button", { name: /Расчётная прибыль/ }));
    expect(names()).toEqual(["В", "Б", "А"]);
    fireEvent.click(screen.getByRole("button", { name: /^Товар/ }));
    expect(names()).toEqual(["А", "Б", "В"]); // text: A-Z first

    fireEvent.click(screen.getByRole("button", { name: "Плитка" }));
    expect(screen.getAllByRole("button", { name: /Б|А|В/ }).length).toBeGreaterThan(0);
    const tileOrder = Array.from(document.querySelectorAll(".product-economics-card-heading > div > strong")).map((e) => e.textContent);
    expect(tileOrder).toEqual(["Б", "А", "В"]);
  });
});

describe("ProductEconomicsList table tools", () => {
  const open = () => {
    render(<ProductEconomicsList items={items} onOpen={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "Таблица" }));
  };
  const headers = () => screen.getAllByRole("columnheader").map((h) => h.textContent?.replace(/[▲▼]/g, "").trim());
  const defaults = ["Товар", "Выручка", "Расчётная прибыль", "Маржа", "ДРР", "Запас", "Динамика выручки"];
  const pick = (name: string) => { fireEvent.click(screen.getByRole("button", { name: "Колонки" })); fireEvent.click(screen.getByRole("checkbox", { name })); };

  it("keeps today's columns by default, product cannot be unchecked", () => {
    open();
    expect(headers()).toEqual(defaults);
    fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
    expect((screen.getByRole("checkbox", { name: "Товар и артикул" }) as HTMLInputElement).disabled).toBe(true);
    expect(screen.getAllByRole("checkbox")).toHaveLength(14);
  });

  it("toggles columns, sorts by an extra column, persists and resets", () => {
    open();
    pick("Заказы, шт.");
    expect(headers()).toEqual([...defaults, "Заказы, шт."]);
    expect(JSON.parse(window.localStorage.getItem("mh.products.columns")!)).toContain("orders");
    fireEvent.click(screen.getByRole("button", { name: "Заказы, шт." }));
    expect(screen.getByRole("columnheader", { name: /Заказы/ }).getAttribute("aria-sort")).toBe("descending");

    cleanup();
    open();
    expect(headers()).toContain("Заказы, шт."); // restored from storage

    fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
    fireEvent.click(screen.getByRole("button", { name: "Сбросить" }));
    expect(headers()).toEqual(defaults);
  });

  it("falls back to defaults on garbage or blocked storage", () => {
    for (const bad of ["{oops", '"x"', '{"a":1}', "null"]) {
      window.localStorage.setItem("mh.products.columns", bad);
      open();
      expect(headers()).toEqual(defaults);
      cleanup();
    }
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    render(<ProductEconomicsList items={items} onOpen={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "Таблица" }));
    pick("Выкуп, %");
    expect(headers()).toContain("Выкуп, %");
    vi.restoreAllMocks();
  });

  it("exports visible columns in the current order as CSV", async () => {
    let blob: Blob | undefined;
    URL.createObjectURL = vi.fn((b: Blob) => { blob = b; return "blob:x"; });
    URL.revokeObjectURL = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    open();
    pick("Выкуп, %");
    fireEvent.click(screen.getByRole("button", { name: /^Маржа/ })); // high to low: 30 then -20
    fireEvent.click(screen.getByRole("button", { name: /Скачать CSV/ }));
    expect(click).toHaveBeenCalled();
    const text = await blob!.text();
    const lines = text.replace(/^﻿/, "").trim().split("\r\n");
    expect(lines[0]).toBe("Товар;Артикул;Выручка;Расчётная прибыль;Маржа;ДРР;Запас;Выкуп, %");
    expect(lines[1]).toBe("Салфетки на стол;NAP-1;1000;300;30;5;12 шт. · 12.0 дн.;80");
    expect(lines[2].startsWith("Коврик;NAP-2;1000;-200;-20;5;")).toBe(true);
    click.mockRestore();
  });
});

describe("revenue trend column", () => {
  const trend = { start: "2026-09-01", end: "2026-09-03", bucketDays: 1, dates: ["2026-09-01", "2026-09-02", "2026-09-03"], series: { "1": { revenue: [100, 300, 200], units: [1, 3, 2] } } };

  it("draws a sparkline per SKU once the trend has loaded and a dash for SKUs without sales", () => {
    render(<ProductEconomicsList items={items} onOpen={() => undefined} trend={trend} />);
    fireEvent.click(screen.getByRole("button", { name: "Таблица" }));
    const charts = screen.getAllByRole("img");
    expect(charts).toHaveLength(1);
    expect(charts[0].getAttribute("aria-label")).toContain("итого 600");
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);          // second SKU has no series
  });

  it("shows only dashes while the trend is still loading, and the header is not a sort button", () => {
    render(<ProductEconomicsList items={items} onOpen={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "Таблица" }));
    expect(screen.queryAllByRole("img")).toHaveLength(0);
    expect(screen.getByRole("columnheader", { name: "Динамика выручки" }).querySelector("button")).toBeNull();
  });

  it("is left out of the CSV export", () => {
    let csv = "";
    const originalBlob = globalThis.Blob;
    vi.stubGlobal("Blob", class { constructor(parts: BlobPart[]) { csv = String(parts[0]); } });
    URL.createObjectURL = vi.fn(() => "blob:x"); URL.revokeObjectURL = vi.fn();
    render(<ProductEconomicsList items={items} onOpen={() => undefined} trend={trend} />);
    fireEvent.click(screen.getByRole("button", { name: "Таблица" }));
    fireEvent.click(screen.getByRole("button", { name: /Скачать CSV/ }));
    expect(csv).not.toContain("Динамика");
    vi.stubGlobal("Blob", originalBlob);
  });
});

