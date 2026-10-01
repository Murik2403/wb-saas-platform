import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProductsHeatmap, cellAlpha, formatCell, heatmapCsv } from "./ProductsHeatmap";
import * as ds from "./data-source";
import type { ProductsHeatmap as Heatmap } from "./data-source";

const data: Heatmap = {
  metric: "revenue", label: "Выручка, ₽", unit: "rub", start: "2026-09-01", end: "2026-09-03", bucket_days: 1,
  dates: ["2026-09-01", "2026-09-02", "2026-09-03"],
  rows: [
    { nm_id: 20, vendor_code: "B", name: "Брюки", total: 200, values: [0, 0, 200] },
    { nm_id: 10, vendor_code: "A", name: "Алые футболки", total: 80, values: [150, 0, -70] },
  ],
  other: { count: 2, total: 5, values: [1, 2, 2] },
  column_totals: [151, 2, 132], grand_total: 285, min: -70, max: 200, campaign_level_total: null,
};

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("cellAlpha / formatCell", () => {
  it("keeps zero quiet, scales globally and caps the strength", () => {
    expect(cellAlpha(0, 100)).toBe(0);
    expect(cellAlpha(5, 0)).toBe(0);
    expect(cellAlpha(Number.NaN, 100)).toBe(0);
    expect(cellAlpha(100, 100)).toBeCloseTo(0.5);
    expect(cellAlpha(-100, 100)).toBeCloseTo(0.5);
    expect(cellAlpha(500, 100)).toBeCloseTo(0.5);
    expect(cellAlpha(50, 100)).toBeCloseTo(0.3);
  });
  it("renders a dash for missing numbers and a true minus for negatives", () => {
    expect(formatCell(null, "rub")).toBe("—");
    expect(formatCell(Number.NaN, "pcs")).toBe("—");
    expect(formatCell(-70, "rub")).toBe("−70");
    expect(formatCell(1.5, "pcs")).toBe("1,5");
  });
});

describe("heatmapCsv", () => {
  it("exports every row, the other row and the totals row", () => {
    const lines = heatmapCsv(data).replace("﻿", "").trim().split("\r\n");
    expect(lines[0]).toBe("Артикул WB;Артикул продавца;Товар;01.09;02.09;03.09;Итого");
    expect(lines[1]).toBe("20;B;Брюки;0;0;200;200");
    expect(lines[3]).toBe(";;Прочие (2);1;2;2;5");
    expect(lines[4]).toBe(";;Итого;151;2;132;285");
  });
});

describe("ProductsHeatmap", () => {
  it("fetches nothing until opened, then loads the chosen metric and shows numbers", async () => {
    const spy = vi.spyOn(ds, "getProductsHeatmap").mockResolvedValue(data);
    render(<ProductsHeatmap period={{ id: "30d" }} />);
    expect(spy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Тепловая карта/ }));
    await screen.findByRole("table");
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy.mock.calls[0][0]).toBe("orders");
    expect(screen.getByTitle("Алые футболки, 2026-09-03: −70").textContent).toBe("−70");
    expect(screen.getByText(/Прочие товары \(2\)/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/Показатель/), { target: { value: "ad_spend" } });
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    expect(spy.mock.calls[1][0]).toBe("ad_spend");
  });

  it("sorts by name on request", async () => {
    vi.spyOn(ds, "getProductsHeatmap").mockResolvedValue(data);
    render(<ProductsHeatmap period={{ id: "7d" }} />);
    fireEvent.click(screen.getByRole("button", { name: /Тепловая карта/ }));
    await screen.findByRole("table");
    const first = () => screen.getAllByRole("rowheader")[0].textContent ?? "";
    expect(first()).toContain("Брюки");
    fireEvent.change(screen.getByLabelText(/Сортировка/), { target: { value: "name" } });
    expect(first()).toContain("Алые футболки");
  });

  it("shows error and empty states", async () => {
    const spy = vi.spyOn(ds, "getProductsHeatmap").mockRejectedValueOnce(new Error("HTTP 500"));
    render(<ProductsHeatmap period={{ id: "7d" }} />);
    fireEvent.click(screen.getByRole("button", { name: /Тепловая карта/ }));
    expect((await screen.findByRole("alert")).textContent).toContain("HTTP 500");
    spy.mockResolvedValueOnce({ ...data, rows: [], other: null, min: 0, max: 0 });
    fireEvent.change(screen.getByLabelText(/Показатель/), { target: { value: "sales" } });
    expect(await screen.findByText(/данных нет/)).toBeTruthy();
  });

  it("ignores a stale response after the metric changed", async () => {
    let resolveFirst: (v: Heatmap) => void = () => {};
    const spy = vi.spyOn(ds, "getProductsHeatmap")
      .mockImplementationOnce(() => new Promise((r) => { resolveFirst = r; }))
      .mockResolvedValueOnce({ ...data, label: "Свежие", rows: [{ ...data.rows[0], name: "Новый" }], other: null });
    render(<ProductsHeatmap period={{ id: "7d" }} />);
    fireEvent.click(screen.getByRole("button", { name: /Тепловая карта/ }));
    fireEvent.change(screen.getByLabelText(/Показатель/), { target: { value: "sales" } });
    await screen.findByText("Новый");
    resolveFirst({ ...data, rows: [{ ...data.rows[0], name: "Устаревший" }] });
    await Promise.resolve();
    expect(screen.queryByText("Устаревший")).toBeNull();
    expect(spy).toHaveBeenCalledTimes(2);
  });
});
