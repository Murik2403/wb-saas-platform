import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OnboardingPanel } from "./OnboardingPanel";
import { trackMetrikaGoal } from "./metrika";
vi.mock("./metrika", () => ({ trackMetrikaGoal: vi.fn() }));
import { getActivationSummary, getFirstGrowthReport, getTenantSyncHistory, recordFirstReportViewed, type ActivationSummary, type FirstGrowthReport } from "./data-source";

vi.mock("./data-source", () => ({ getActivationSummary: vi.fn(), getFirstGrowthReport: vi.fn(), getTenantSyncHistory: vi.fn(), recordFirstReportViewed: vi.fn() }));
const summary: ActivationSummary = { schema_version: 1, is_demo: false, wb_connected: true, sync: { state: "success", complete: true, successful_full_sync_at: "2026-09-18" }, selected_product: { nm_id: 123, cost_configured: true, has_financial_rows: true }, ready_for_report: true, next_step: "open_report", first_value_verified: false, first_value_at: null };
const report: FirstGrowthReport = { nm_id: 123, product: "Тестовый товар", period: { start: "2026-08-19", end: "2026-09-17" }, revenue: 2400, sales: 2, unit_cost: 420, margin: 1100, margin_pct: 45.8, limitation: "Налоги и внешние расходы не включены", report_revision: "a".repeat(64) };
const props = {
  canManageConnections: true, canWrite: true,
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
  it("manager can load history while viewer cannot submit the same action",async()=>{
    vi.mocked(getActivationSummary).mockResolvedValue({...summary,sync:{state:"error",complete:false,successful_full_sync_at:null},ready_for_report:false,next_step:"sync"});
    const view=render(<OnboardingPanel {...props} canManageConnections={false} canWrite />);
    const sync=await screen.findByRole("button",{name:"Загрузить историю"});
    expect(sync).toBeEnabled();await userEvent.click(sync);expect(props.onSync).toHaveBeenCalledOnce();
    view.rerender(<OnboardingPanel {...props} canManageConnections={false} canWrite={false}/>);
    expect(screen.getByRole("button",{name:"Загрузить историю"})).toBeDisabled();
    expect(screen.getByLabelText("Глубина первой загрузки, дней")).toBeDisabled();
  });
  it("manager may edit costs but cannot enter or submit an API token",async()=>{
    render(<OnboardingPanel {...props} connected={false} canManageConnections={false} />);
    expect(screen.getByRole("button",{name:"Проверить и сохранить"})).toBeDisabled();
    expect(screen.getByLabelText("WB API-токен для первого запуска")).toBeDisabled();
    await userEvent.click(screen.getByRole("button",{name:"Проверить и сохранить"}));
    expect(props.onConnect).not.toHaveBeenCalled();
    await userEvent.selectOptions(screen.getByLabelText("Товар для первого отчёта"),"123");
    await userEvent.click(screen.getByRole("button",{name:"Проверить стоимость"}));
    expect(props.onEditCost).toHaveBeenCalledWith(props.costs[0]);
  });
  it("missing permissions are fail-closed and changing permission clears a token draft",async()=>{
    const view=render(<OnboardingPanel {...props} connected={false} token="synthetic-draft" />);
    expect(screen.getByRole("button",{name:"Проверить и сохранить"})).toBeEnabled();
    view.rerender(<OnboardingPanel {...props} connected={false} token="synthetic-draft" canManageConnections={undefined} canWrite={undefined} />);
    expect(screen.getByRole("button",{name:"Проверить и сохранить"})).toBeDisabled();
    expect(screen.getByLabelText("WB API-токен для первого запуска")).toHaveValue("");
    await waitFor(()=>expect(props.onTokenChange).toHaveBeenCalledWith(""));
  });
  it("demo cannot connect, sync or edit costs even with incorrectly enabled parent flags",async()=>{
    vi.mocked(getActivationSummary).mockResolvedValue({...summary,is_demo:true});
    render(<OnboardingPanel {...props} connected={false}/>);
    await screen.findByText(/Это демонстрационные данные/);
    expect(screen.getByRole("button",{name:"Проверить и сохранить"})).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText("Товар для первого отчёта"),"123");
    expect(screen.getByRole("button",{name:"Проверить стоимость"})).toBeDisabled();
    expect(props.onConnect).not.toHaveBeenCalled();expect(props.onEditCost).not.toHaveBeenCalled();
  });
  it("hides a completed onboarding strip only after server verification when requested", async () => {
    vi.mocked(getActivationSummary).mockResolvedValue({ ...summary, first_value_verified: true });
    const { container } = render(<OnboardingPanel {...props} hideCompleted />);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
    expect(recordFirstReportViewed).not.toHaveBeenCalled();
  });
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


it("allows completed settings to reopen a verified report without automatic tracking", async () => {
  vi.mocked(getActivationSummary).mockImplementation(async (nmId) => ({ ...summary, first_value_verified: true, ready_for_report: Boolean(nmId) }));
  vi.mocked(getFirstGrowthReport).mockResolvedValue(report);
  render(<OnboardingPanel {...props} />);
  expect(await screen.findByText("Первый результат получен. Подключения и себестоимость можно изменить ниже.")).toBeVisible();
  expect(recordFirstReportViewed).not.toHaveBeenCalled();
  await userEvent.selectOptions(screen.getByLabelText("Товар для повторного отчёта"), "123");
  await waitFor(() => expect(screen.getByRole("button", { name: "Открыть проверенный отчёт" })).toBeEnabled());
  await userEvent.click(screen.getByRole("button", { name: "Открыть проверенный отчёт" }));
  await waitFor(() => expect(recordFirstReportViewed).toHaveBeenCalledWith(report));
  expect(trackMetrikaGoal).not.toHaveBeenCalled();
});

describe("first store load progress", () => {
  const runningRun = { id: 2, started_at: "2026-09-18", finished_at: null, status: "running", details: "progress: 3/7 sales" };
  const runningSummary = { ...summary, sync: { state: "running", complete: false, successful_full_sync_at: null }, ready_for_report: false, next_step: "sync" } as ActivationSummary;

  it("shows how far the load has got and which stage is running", async () => {
    vi.mocked(getActivationSummary).mockResolvedValue(runningSummary);
    vi.mocked(getTenantSyncHistory).mockResolvedValue({ runs: [runningRun] });
    render(<OnboardingPanel {...props} runs={[runningRun]} />);
    expect(await screen.findByText("Выполнено 3 из 7 этапов. Сейчас: финансовый отчёт.")).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("value")).toBe("3");
    expect(screen.queryByText(/Данные магазина загружены/)).toBeNull();
  });

  it("announces when a load the user was waiting for has finished", async () => {
    vi.mocked(getActivationSummary).mockResolvedValue(runningSummary);
    vi.mocked(getTenantSyncHistory).mockResolvedValue({ runs: [runningRun] });
    const { rerender } = render(<OnboardingPanel {...props} runs={[runningRun]} />);
    await screen.findByRole("progressbar");
    vi.mocked(getActivationSummary).mockResolvedValue(summary);
    rerender(<OnboardingPanel {...props} runs={props.runs} />);
    expect(await screen.findByText("Данные магазина загружены. Теперь выберите товар и проверьте себестоимость.")).toBeTruthy();
  });

  it("stays quiet when the page opens with the load already complete", async () => {
    render(<OnboardingPanel {...props} />);
    await screen.findByText("Первый результат на ваших данных");
    await waitFor(() => expect(getActivationSummary).toHaveBeenCalled());
    expect(screen.queryByText(/Данные магазина загружены/)).toBeNull();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });
});
