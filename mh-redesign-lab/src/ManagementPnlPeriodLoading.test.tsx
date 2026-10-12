import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ManagementPnlMonthly } from "./ManagementPnlMonthly";
import { ManagementPnlSummary } from "./ManagementPnlSummary";
import { getManagementPnl, getManagementPnlMonthly, type ManagementPnl, type MonthlyPnl } from "./data-source";

vi.mock("./data-source", () => ({ getManagementPnl: vi.fn(), getManagementPnlMonthly: vi.fn() }));

const period = { start: "2026-09-01", end: "2026-09-30" };
const lines = { realization: 1234, spp_discount: 0, sales: 1234, cost: 34, logistics: 0, commission: 0, acquiring: 0, penalties: 0, storage: 0, advertising: 0, other: 0, result: 1200, units: 1, report_rows: 1 };
const monthly: MonthlyPnl = { period, data_status: "ok", data_through: "2026-09-30", note: "WB", months: [{ ...lines, ...period, month: "2026-09", partial: false, data_incomplete: false }], total: lines };
const summary: ManagementPnl = { period, data_status: "ok", revenue_rub: 1234, units: 1, accounting_profit_rub: 1200, confirmed_external_expenses_rub: 0, management_profit_rub: 1200, excluded_unconfirmed_rub: 0, excluded_wb_commission_rub: 0, unknown_source_rub: 0, unknown_allocation_count: 0, expenses: { included: [], excluded: [], unknown_source: [] }, attestation: "confirmed_by_tenant", note: "" };

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

beforeEach(() => { vi.mocked(getManagementPnl).mockReset(); vi.mocked(getManagementPnlMonthly).mockReset(); });
afterEach(cleanup);

describe("financial period isolation", () => {
  it("hides the previous monthly table and CSV when the new 90-day period fails", async () => {
    const next = deferred<MonthlyPnl>();
    vi.mocked(getManagementPnlMonthly).mockResolvedValueOnce(monthly).mockReturnValueOnce(next.promise);
    const view = render(<ManagementPnlMonthly period={{ id: "30d" }} />);
    await screen.findByRole("table");
    expect(screen.getByRole("button", { name: "Скачать CSV" })).toBeTruthy();
    view.rerender(<ManagementPnlMonthly period={{ id: "90d" }} />);
    expect(screen.queryByRole("table")).toBeNull();
    await act(async () => { next.reject(new Error("90d: timeout")); });
    expect(screen.getByRole("alert").textContent).toContain("90d: timeout");
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("button", { name: "Скачать CSV" })).toBeNull();
    expect(screen.queryByText("Нет финансовых данных WB за выбранный период.")).toBeNull();
  });

  it("hides the previous summary and ready badge while loading and after a new period fails", async () => {
    const next = deferred<ManagementPnl>();
    vi.mocked(getManagementPnl).mockResolvedValueOnce(summary).mockRejectedValueOnce(new Error("optional comparison unavailable")).mockReturnValueOnce(next.promise);
    const view = render(<ManagementPnlSummary period={{ id: "30d" }} />);
    await screen.findByText("расчёт готов");
    view.rerender(<ManagementPnlSummary period={{ id: "90d" }} />);
    expect(screen.queryByText("расчёт готов")).toBeNull();
    expect(screen.queryAllByText("1 200 ₽")).toHaveLength(0);
    await act(async () => { next.reject(new Error("90d: timeout")); });
    expect(screen.getByRole("alert").textContent).toContain("90d: timeout");
    expect(screen.queryAllByText("1 200 ₽")).toHaveLength(0);
    expect(screen.queryByText("расчёт готов")).toBeNull();
  });

  it("ignores a monthly response arriving after its period was abandoned", async () => {
    const old = deferred<MonthlyPnl>();
    vi.mocked(getManagementPnlMonthly).mockReturnValueOnce(old.promise).mockResolvedValueOnce({ ...monthly, months: [], data_status: "no_financial_data" });
    const view = render(<ManagementPnlMonthly period={{ id: "30d" }} />);
    view.rerender(<ManagementPnlMonthly period={{ id: "90d" }} />);
    await screen.findByText("Нет финансовых данных WB за выбранный период.");
    await act(async () => { old.resolve(monthly); });
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("button", { name: "Скачать CSV" })).toBeNull();
  });

  it("ignores an abandoned summary response and does not request its comparison", async () => {
    const old = deferred<ManagementPnl>();
    const comparison = deferred<ManagementPnl>();
    vi.mocked(getManagementPnl).mockReturnValueOnce(old.promise).mockResolvedValueOnce({ ...summary, management_profit_rub: 2500 }).mockReturnValueOnce(comparison.promise);
    const view = render(<ManagementPnlSummary period={{ id: "30d" }} />);
    view.rerender(<ManagementPnlSummary period={{ id: "90d" }} />);
    await screen.findByText("2 500 ₽");
    await act(async () => { old.resolve(summary); });
    expect(screen.getByText("2 500 ₽")).toBeTruthy();
    expect(getManagementPnl).toHaveBeenCalledTimes(3);
  });
});
