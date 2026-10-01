import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ProductEconomicsList, abcClasses, periodLengthDays } from "./ProductEconomicsList";
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
    expect(screen.getAllByRole("checkbox")).toHaveLength(18);
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


describe("abcClasses (Pareto 80/95)", () => {
  it("cuts at 80% and 95% of the positive total, the item that reaches the border stays in its class", () => {
    expect(abcClasses([50, 30, 10, 5, 5], "revenue")).toEqual(["A", "A", "B", "B", "C"]); // before: 0,50,80,90,95 of 100
    expect(abcClasses([5, 50, 5, 30, 10], "revenue")).toEqual(["B", "A", "C", "A", "B"]); // ties 5 and 5: the earlier input row ranks first (before = 90), the later one is at 95
  });
  it("handles a single item, ties (stable by input order), empty and all-zero lists", () => {
    expect(abcClasses([10], "revenue")).toEqual(["A"]);
    expect(abcClasses([20, 20, 20, 20, 20], "revenue")).toEqual(["A", "A", "A", "A", "B"]); // 80 is not < 80
    expect(abcClasses([3, 3, 3, 1], "profit")).toEqual(["A", "A", "A", "B"]);
    expect(abcClasses([], "revenue")).toEqual([]);
    expect(abcClasses([0, 0], "revenue")).toEqual(["C", "C"]);
    expect(abcClasses([0, 0], "profit")).toEqual(["none", "none"]);
  });
  it("marks loss-makers and zero profit separately, never as C; negative revenue is C", () => {
    expect(abcClasses([100, -20, 0, 50], "profit")).toEqual(["A", "loss", "none", "A"]); // 150 total: before 0 and 100 < 120
    expect(abcClasses([100, -20, 0, 50], "revenue")).toEqual(["A", "C", "C", "A"]);
  });
});

describe("periodLengthDays", () => {
  it("knows presets and inclusive custom ranges, and gives up otherwise", () => {
    expect(periodLengthDays({ id: "today" })).toBe(1);
    expect(periodLengthDays({ id: "30d" })).toBe(30);
    expect(periodLengthDays({ id: "custom", start: "2026-09-01", end: "2026-09-30" })).toBe(30);
    expect(periodLengthDays({ id: "custom", start: "2026-09-05", end: "2026-09-05" })).toBe(1);
    expect(periodLengthDays({ id: "custom", start: "2026-09-05", end: "2026-09-01" })).toBeNull();
    expect(periodLengthDays({ id: "custom" })).toBeNull();
  });
});

describe("ABC, capitalization and GMROI columns", () => {
  const set = [
    row({ nmId: "1", product: "Один", revenue: 800, margin: 100, stock: 12, unitCost: 100 }),
    row({ nmId: "2", product: "Два", revenue: 150, margin: -50, stock: 10, unitCost: 0 }),
    row({ nmId: "3", product: "Три", revenue: 50, margin: 0, stock: 0, unitCost: 40 }),
  ];
  const show = (periodDays?: number, names: string[] = []) => {
    render(<ProductEconomicsList items={set} onOpen={() => undefined} periodDays={periodDays} />);
    fireEvent.click(screen.getByRole("button", { name: "Таблица" }));
    if (names.length) fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
    for (const name of names) fireEvent.click(screen.getByRole("checkbox", { name }));
  };
  const cells = (col: string) => {
    const idx = screen.getAllByRole("columnheader").findIndex((h) => h.textContent?.startsWith(col));
    return screen.getAllByRole("row").slice(1).map((r) => r.querySelectorAll("td")[idx]);
  };

  it("is hidden by default", () => {
    show(30);
    const heads = screen.getAllByRole("columnheader").map((h) => h.textContent);
    for (const n of ["ABC по выручке", "ABC по прибыли", "Капитализация, ₽", "GMROI"]) expect(heads).not.toContain(n);
    expect(document.querySelector(".product-abc-summary")).toBeNull();
  });

  it("shows letters with accessible text, documents thresholds, and a summary line", () => {
    show(30, ["ABC по выручке", "ABC по прибыли"]);
    const rev = cells("ABC по выручке");
    expect(rev.map((c) => c.textContent)).toEqual(["A", "B", "C"]); // 800 | 150 -> 950 | 50
    expect(rev[0].getAttribute("aria-label")).toBe("Класс A по выручке: входит в первые 80%");
    expect(cells("ABC по прибыли").map((c) => c.textContent)).toEqual(["A", "убыток", "—"]);
    expect(cells("ABC по прибыли")[1].getAttribute("aria-label")).toContain("Убыток");
    expect(screen.getByRole("columnheader", { name: /ABC по выручке/ }).getAttribute("title")).toContain("80%");
    const lines = Array.from(document.querySelectorAll(".product-abc-summary")).map((e) => e.textContent);
    expect(lines).toEqual([
      "ABC по выручке — A: 1 SKU = 80% выручки · B: 1 · C: 1",
      "ABC по прибыли — A: 1 SKU = 100% прибыли · B: 0 · C: 0 · убыток: 1",
    ]);
  });

  it("capitalization = stock x unit cost, a dash without cost", () => {
    show(30, ["Капитализация, ₽"]);
    expect(cells("Капитализация").map((c) => c.textContent?.replace(/\s/g, " "))).toEqual(["1 200 ₽", "—", "0 ₽"]);
  });

  it("GMROI = margin x 365 / days / capitalization", () => {
    show(30, ["GMROI"]);
    // 100*365/30 = 1216.67; / (12*100) = 1.0139 -> 1,01; no cost -> dash; zero stock (capitalization 0) -> dash
    expect(cells("GMROI").map((c) => c.textContent)).toEqual(["1,01", "—", "—"]);
  });

  it("GMROI is unavailable without a known period length", () => {
    show(undefined);
    fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
    const box = screen.getByRole("checkbox", { name: "GMROI" }) as HTMLInputElement;
    expect(box.disabled).toBe(true);
    expect(box.closest("label")!.getAttribute("title")).toContain("длина периода неизвестна");
  });

  it("negative margin gives negative GMROI: -200*365/30/(12*100) = -2,03", () => {
    render(<ProductEconomicsList items={[row({ margin: -200 })]} onOpen={() => undefined} periodDays={30} />);
    fireEvent.click(screen.getByRole("button", { name: "Таблица" }));
    fireEvent.click(screen.getByRole("button", { name: "Колонки" })); fireEvent.click(screen.getByRole("checkbox", { name: "GMROI" }));
    expect(cells("GMROI")[0].textContent).toBe("-2,03");
    expect(cells("GMROI")[0].className).toContain("negative");
  });

  it("sorts by ABC (A first), and exports letters and raw numbers to CSV", async () => {
    let blob: Blob | undefined;
    URL.createObjectURL = vi.fn((b: Blob) => { blob = b; return "blob:x"; });
    URL.revokeObjectURL = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    show(30, ["ABC по выручке", "ABC по прибыли", "Капитализация, ₽", "GMROI"]);
    fireEvent.click(screen.getByRole("button", { name: /^ABC по прибыли/ }));
    expect(screen.getAllByRole("row").slice(1).map((r) => r.querySelector("strong")?.textContent)).toEqual(["Один", "Три", "Два"]); // A, —, убыток
    fireEvent.click(screen.getByRole("button", { name: /Скачать CSV/ }));
    const lines = (await blob!.text()).replace(/^﻿/, "").trim().split("\r\n");
    expect(lines[0].endsWith("ABC по выручке;ABC по прибыли;Капитализация, ₽;GMROI")).toBe(true);
    expect(lines[1].endsWith(";A;A;1200;1,01")).toBe(true);
    expect(lines[2].endsWith(";C;—;0;")).toBe(true);
    expect(lines[3].endsWith(";B;убыток;;")).toBe(true);
    click.mockRestore();
  });
});
