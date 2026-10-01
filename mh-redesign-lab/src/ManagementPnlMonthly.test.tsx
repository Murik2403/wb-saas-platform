import { describe, expect, it } from "vitest";
import { comparePnl } from "./ManagementPnlCompare";
import { buildMonthlyPnlCsv, directExpenses, shareOfRealization } from "./ManagementPnlMonthly";
import type { MonthlyPnl, MonthlyPnlLines } from "./data-source";

const aug: MonthlyPnlLines = { realization: 1000, spp_discount: -100, sales: 900, cost: 100, logistics: 100, commission: 100, acquiring: 20, penalties: 10, storage: 20, advertising: 50, other: 31, result: 469, units: 1, report_rows: 2 };
const sep: MonthlyPnlLines = { realization: 1500, spp_discount: -100, sales: 1400, cost: 300, logistics: 75, commission: 250, acquiring: 30, penalties: 0, storage: 0, advertising: 70, other: 20, result: 655, units: 3, report_rows: 1 };
const total = Object.fromEntries(Object.keys(aug).map((k) => [k, aug[k as keyof MonthlyPnlLines] + sep[k as keyof MonthlyPnlLines]])) as MonthlyPnlLines;
const data: MonthlyPnl = {
  period: { start: "2026-08-01", end: "2026-09-30" }, data_status: "ok", data_through: "2026-09-05", note: "n",
  months: [{ ...aug, month: "2026-08", start: "2026-08-01", end: "2026-08-31", partial: false, data_incomplete: false }, { ...sep, month: "2026-09", start: "2026-09-01", end: "2026-09-30", partial: false, data_incomplete: true }],
  total,
};

describe("comparePnl", () => {
  it("shows no baseline instead of dividing by zero", () => {
    for (const previous of [0, null, undefined]) expect(comparePnl(100, previous)).toMatchObject({ hasBaseline: false, tone: "neutral", text: "нет данных за прошлый период" });
    expect(comparePnl(null, 100).hasBaseline).toBe(false);
  });
  it("is good when profit grows and bad when costs grow", () => {
    expect(comparePnl(120, 100, "up")).toMatchObject({ tone: "good" });
    expect(comparePnl(120, 100, "up").text).toContain("+20 ₽ (+20%)");
    expect(comparePnl(120, 100, "down").tone).toBe("bad");
    expect(comparePnl(80, 100, "up").tone).toBe("bad");
    expect(comparePnl(80, 100, "down").tone).toBe("good");
    expect(comparePnl(120, 100, "none").tone).toBe("neutral");
  });
  it("uses the absolute previous value so a smaller loss counts as growth", () => {
    const result = comparePnl(-50, -100, "up");
    expect(result.tone).toBe("good");
    expect(result.text).toContain("+50 ₽ (+50%)");
  });
});

describe("monthly P&L helpers", () => {
  it("shares are relative to realization and absent without it", () => {
    expect(shareOfRealization(250, 1000)).toBe(25);
    expect(shareOfRealization(250, 0)).toBeNull();
  });
  it("direct expenses are the eight cost lines", () => {
    expect(directExpenses(aug)).toBe(431);
    expect(aug.sales - directExpenses(aug)).toBe(aug.result);
  });
  it("exports BOM, semicolons, comma decimals and a total column", () => {
    const csv = buildMonthlyPnlCsv(data);
    expect(csv.startsWith("﻿")).toBe(true);
    const lines = csv.trim().split("\r\n");
    expect(lines[0]).toBe("Показатель;авг. 2026, ₽;авг. 2026, % от реализации;сент. 2026, ₽;сент. 2026, % от реализации;Итого, ₽;Итого, % от реализации");
    expect(lines.find((l) => l.startsWith("Реализация"))).toBe("Реализация (цена после скидки продавца);1000,00;100,0;1500,00;100,0;2500,00;100,0");
    expect(lines.find((l) => l.startsWith("Себестоимость"))).toBe("Себестоимость;-100,00;-10,0;-300,00;-20,0;-400,00;-16,0");
  });
});
