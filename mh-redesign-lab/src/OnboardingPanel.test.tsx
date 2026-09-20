import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OnboardingPanel } from "./OnboardingPanel";
import { getActivationSummary, getFirstGrowthReport, recordFirstReportViewed, type ActivationSummary, type FirstGrowthReport } from "./data-source";

vi.mock("./data-source", () => ({ getActivationSummary: vi.fn(), getFirstGrowthReport: vi.fn(), recordFirstReportViewed: vi.fn() }));
const summary: ActivationSummary = { schema_version: 1, is_demo: false, wb_connected: true, sync: { state: "success", complete: true, successful_full_sync_at: "2026-09-18" }, selected_product: { nm_id: 123, cost_configured: true, has_financial_rows: true }, ready_for_report: true, next_step: "open_report", first_value_verified: false, first_value_at: null };
const report: FirstGrowthReport = { nm_id: 123, product: "Тестовый товар", period: { start: "2026-08-19", end: "2026-09-17" }, revenue: 2400, sales: 2, unit_cost: 420, margin: 1100, margin_pct: 45.8, limitation: "Налоги и внешние расходы не включены", report_revision: "a".repeat(64) };
const props = {
  connected: true, runs: [{ id: 1, started_at: "2026-09-18", finished_at: "2026-09-18", status: "success", details: "orders: 2, sales: 2, financial_rows: 2" }],
  costs: [{ nm_id: 123, product_name: "Тестовый товар", supplier_article: "test", cost_per_wb_unit: 420, updated_at: "2026-09-18", note: "" }],
  days: 30, busy: false, token: "", onTokenChange: vi.fn(), onConnect: vi.fn(), onDaysChange: vi.fn(), onSync: vi.fn(), onEditCost: vi.fn(),
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getActivationSummary).mockImplementation(async (nmId) => ({ ...summary, ready_for_report: Boolean(nmId), next_step: nmId ? "open_report" : "select_product" }));
  vi.mocked(recordFirstReportViewed).mockResolvedValue({ first_value_verified: true, first_value_at: "2026-09-18" });
});

describe("first report onboarding", () => {
  it("records only after the requested real report has loaded and rendered", async () => {
    let resolveReport!: (value: FirstGrowthReport) => void;
    vi.mocked(getFirstGrowthReport).mockReturnValue(new Promise((resolve) => { resolveReport = resolve; }));
    render(<OnboardingPanel {...props} />);
    expect(recordFirstReportViewed).not.toHaveBeenCalled();
    await userEvent.selectOptions(screen.getByLabelText("Товар для первого отчёта"), "123");
    await waitFor(() => expect(screen.getByRole("button", { name: "Открыть первый отчёт" })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: "Открыть первый отчёт" }));
    expect(recordFirstReportViewed).not.toHaveBeenCalled();
    resolveReport(report);
    expect(await screen.findByRole("heading", { name: "Первый отчёт: Тестовый товар" })).toBeVisible();
    await waitFor(() => expect(recordFirstReportViewed).toHaveBeenCalledWith(report));
  });

  it("preselects the first sold SKU after a successful sync when none is selected", async () => {
    vi.mocked(getActivationSummary).mockResolvedValue({ ...summary, selected_product: { nm_id: null, cost_configured: false, has_financial_rows: false }, ready_for_report: false, next_step: "cost", recommended_product: { nm_id: 123, product_name: "Тестовый товар", supplier_article: "test", latest_sale_at: "2026-09-18" } });
    render(<OnboardingPanel {...props} />);
    await waitFor(() => expect(screen.getByLabelText("Товар для первого отчёта")).toHaveValue("123"));
    expect(screen.getByText("Предвыбран первый товар с продажами за загруженный период.")).toBeVisible();
  });

  it("keeps the failed sync checklist visible and report disabled", async () => {
    vi.mocked(getActivationSummary).mockResolvedValue({ ...summary, sync: { state: "error", complete: false, successful_full_sync_at: null }, ready_for_report: false, next_step: "sync" });
    render(<OnboardingPanel {...props} runs={[{ ...props.runs[0], status: "error" }]} />);
    expect(await screen.findByText(/Последняя загрузка завершилась с ошибкой/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Открыть первый отчёт" })).toBeDisabled();
    expect(recordFirstReportViewed).not.toHaveBeenCalled();
  });

  it("does not call a zero-cost catalogue row a completed step", async () => {
    vi.mocked(getActivationSummary).mockResolvedValue({ ...summary, selected_product: { nm_id: 123, cost_configured: false, has_financial_rows: true }, ready_for_report: false, next_step: "cost" });
    render(<OnboardingPanel {...props} costs={[{ ...props.costs[0], cost_per_wb_unit: 0 }]} />);
    await userEvent.selectOptions(screen.getByLabelText("Товар для первого отчёта"), "123");
    expect(await screen.findByRole("button", { name: "Указать стоимость" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Открыть первый отчёт" })).toBeDisabled();
    expect(recordFirstReportViewed).not.toHaveBeenCalled();
  });

  it("does not record a report load failure", async () => {
    vi.mocked(getFirstGrowthReport).mockRejectedValue(new Error("Нет расчёта по реализации"));
    render(<OnboardingPanel {...props} />);
    await userEvent.selectOptions(screen.getByLabelText("Товар для первого отчёта"), "123");
    await waitFor(() => expect(screen.getByRole("button", { name: "Открыть первый отчёт" })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: "Открыть первый отчёт" }));
    expect(await screen.findByText("Нет расчёта по реализации")).toBeVisible();
    expect(recordFirstReportViewed).not.toHaveBeenCalled();
  });
});
