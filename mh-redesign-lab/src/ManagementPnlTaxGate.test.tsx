import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ManagementPnlMonthly } from "./ManagementPnlMonthly";
import { getManagementPnlMonthly } from "./data-source";
import type { MonthlyPnl } from "./data-source";
vi.mock("./data-source", () => ({ getManagementPnlMonthly: vi.fn() }));
const lines = { realization: 100, spp_discount: 0, sales: 100, cost: 10, logistics: 0, commission: 0, acquiring: 0, penalties: 0, storage: 0, advertising: 0, other: 0, result: 90, units: 1, report_rows: 1 };
const tax = { status: "ok" as const, usn_base: 100, vat_out: 0, usn: 6, total: 6, profit_after_tax_estimate: 84, loss: false };
describe("monthly accountant review gate", () => {
  it.each([false, undefined])("hides rows and settings CTA with capability %s even if a payload contains taxes", async (enabled) => {
    const data: MonthlyPnl = { tax_estimates_enabled: enabled, period: { start: "2026-09-01", end: "2026-09-30" }, data_status: "ok", data_through: "2026-09-30", note: "WB", months: [{ ...lines, month: "2026-09", start: "2026-09-01", end: "2026-09-30", partial: false, data_incomplete: false, tax }], total: { ...lines, tax } };
    vi.mocked(getManagementPnlMonthly).mockResolvedValue(data);
    render(<ManagementPnlMonthly period={{ id: "30d" }} onOpenTaxSettings={vi.fn()} />);
    await screen.findByRole("table");
    expect(screen.queryByText("Налоги (оценка)")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "укажите режим" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Результат после налогов/)).not.toBeInTheDocument();
  });
});
