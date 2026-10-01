import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { ManagementPnlSummary } from "./ManagementPnlSummary";
import { getManagementPnl, type DashboardPeriod, type ManagementPnl } from "./data-source";

vi.mock("./data-source", () => ({ getManagementPnl: vi.fn() }));

const period = { id: "30d", start: "2026-09-01", end: "2026-09-30" } as DashboardPeriod;
const base: ManagementPnl = {
  period: { start: "2026-09-01", end: "2026-09-30" }, data_status: "ok", revenue_rub: 1000, units: 10,
  accounting_profit_rub: 500, confirmed_external_expenses_rub: 100, management_profit_rub: 400,
  excluded_unconfirmed_rub: 0, excluded_wb_commission_rub: 0, unknown_source_rub: 0, unknown_allocation_count: 0,
  expenses: { included: [], excluded: [], unknown_source: [] }, attestation: "confirmed_by_tenant", note: "",
};
const previous = { ...base, period: { start: "2026-08-02", end: "2026-08-31" }, management_profit_rub: 300, accounting_profit_rub: 250 };
delete (previous as Partial<ManagementPnl>).expenses;

beforeEach(() => vi.mocked(getManagementPnl).mockReset());
afterEach(cleanup);

describe("ManagementPnlSummary loading", () => {
  it("shows the main numbers first and asks for the previous-period comparison in a second request", async () => {
    let resolvePrevious!: (value: ManagementPnl) => void;
    vi.mocked(getManagementPnl)
      .mockResolvedValueOnce(base)
      .mockReturnValueOnce(new Promise((resolve) => { resolvePrevious = resolve; }));
    render(<ManagementPnlSummary period={period} />);

    await screen.findByText("400 ₽");                       // management profit is visible...
    expect(vi.mocked(getManagementPnl).mock.calls[0][2]).toBeUndefined();   // ...from the plain request
    await waitFor(() => expect(getManagementPnl).toHaveBeenCalledTimes(2));
    expect(vi.mocked(getManagementPnl).mock.calls[1][2]).toBe(true);        // comparison is a separate request
    expect(screen.queryByText(/нет данных за прошлый период/)).toBeNull();

    resolvePrevious({ ...base, previous } as ManagementPnl);
    await screen.findByText(/Сравнение с предыдущим периодом той же длины/);
  });

  it("stays silent and keeps the main numbers when the comparison request fails", async () => {
    vi.mocked(getManagementPnl).mockResolvedValueOnce(base).mockRejectedValueOnce(new Error("timeout"));
    render(<ManagementPnlSummary period={period} />);
    await screen.findByText("400 ₽");
    await waitFor(() => expect(getManagementPnl).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("400 ₽")).toBeTruthy();
  });

  it("still reports a failure of the main request", async () => {
    vi.mocked(getManagementPnl).mockRejectedValueOnce(new Error("Сервер недоступен"));
    render(<ManagementPnlSummary period={period} />);
    expect((await screen.findByRole("alert")).textContent).toContain("Сервер недоступен");
    expect(getManagementPnl).toHaveBeenCalledTimes(1);
  });
});
