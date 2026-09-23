import "@testing-library/jest-dom/vitest";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { DashboardDataState } from "./data-source";
import type { PageId } from "./contracts";
import { getActionPlanItems, updateActionPlanItem, financeExportUrl, adaptFinanceReport, getFbsSupplyBarcode, getFbsSupplyDetails, sendCustomerChatMessage, getAnalyticsDashboard, getReportDelivery, getReportOperations, getProductionExecution, saveProductionExecution, closeProductionExecution, createProcurementOperation, getProcurementOperations } from "./data-source";

const mockedDashboard: DashboardDataState = {
  refreshing: false,
  connection: { mode: "live", label: "WB API активно", detail: "Тестовая сессия" },
  navItems: [
    { id: "today", label: "Сегодня", iconKey: "dashboard", count: 1 },
    { id: "stock", label: "Склад", iconKey: "stock", count: 1 },
    { id: "products", label: "Товары", iconKey: "products" },
    { id: "control", label: "Контроль", iconKey: "control" },
    { id: "settings", label: "Настройки", iconKey: "settings" },
  ],
  pageMeta: {
    today: { section: "Сегодня", title: "Сегодня", search: "поиск" },
    stock: { section: "Склад", title: "Склад", search: "поиск" },
    ads: { section: "Реклама", title: "Реклама", search: "поиск" },
    finance: { section: "Финансы", title: "Финансы", search: "поиск" },
    production: { section: "Производство", title: "Производство", search: "поиск" },
    supply: { section: "Поставки", title: "Поставки", search: "поиск" },
    assistant: { section: "AI-агенты", title: "AI", search: "поиск" },
    reports: { section: "Отчеты", title: "Отчеты", search: "поиск" },
    products: { section: "Товары", title: "Товары", search: "поиск" },
    control: { section: "Контроль", title: "Контроль", search: "поиск" },
    settings: { section: "Настройки", title: "Настройки", search: "поиск" },
  },
  mobileNav: ["today", "stock", "products", "control", "settings"] as PageId[],
  sync: { state: "fresh", source: "mock", updatedAt: "2026-09-12T00:00:00Z", lagMinutes: 0 },
  todayMetrics: [],
  stockMetrics: [],
  adsMetrics: [],
  actions: [],
  inventory: [],
  reorderPlan: [],
  stockHero: { primary: "0", primaryNote: "", secondary: "0", secondaryNote: "", tone: "info" },
  stockCoverage: [],
  stockRules: [],
  sellerWarehouses: [],
  adsHero: { title: "", description: "", tone: "info" },
  campaigns: [],
  adsFunnel: [],
  creativeSignals: [],
  financeMetrics: [],
  financeLedger: [],
  financeReport: { rows: [] },
  expenseSplit: [],
  cashWindows: [],
  productionMetrics: [],
  productionStages: [],
  productionTasks: [],
  materialNeeds: [],
  supplyMetrics: [],
  supplyShipments: [],
  supplyNewOrders: [],
  acceptanceCoefficients: [],
  transitTariffs: [],
  supplyErrors: {},
  supplierPulse: [],
  assistantMetrics: [],
  automations: [],
  agentCandidates: [],
  agentHistory: [],
  reportMetrics: [],
  reportCards: [],
  exportItems: [],
  chartBars: [0, 1],
  chartBarsWeek: [0, 1],
  productEconomics: [
    {
      nmId: "SKU-1",
      article: "sku-1",
      product: "Товар для проверки",
      orders: 12,
      sales: 10,
      revenue: 3000,
      buyout: 0,
      adSpend: 200,
      drr: 5.5,
      stock: 15,
      dailySales: 1.2,
      daysOfStock: 2.0,
      unitCost: 140,
      margin: 860,
    },
  ],
  productPrices: [],
  productPriceErrors: [],
  productCards: [],
  productCardErrors: [],
    customerQuality: null,
  dataQuality: {
    summary: {},
    checks: [
      {
        Модуль: "Склад",
        Проверка: "Проверка FIFO",
        Статус: "Внимание",
        Детали: "Нет данных по последним партиям",
        "Что сделать": "Сверьте входящие заказы и остатки",
      },
    ],
    reliability: [],
    fifo_guard: {},
  },
  procurementOrders: [],
  refresh: vi.fn(async () => undefined),
};

const defaultChecks = [
  {
    Модуль: "Склад",
    Проверка: "Проверка FIFO",
    Статус: "Внимание",
    Детали: "Нет данных по последним партиям",
    "Что сделать": "Сверьте входящие заказы и остатки",
  },
];

const setChecks = (checks: Record<string, unknown>[]) => {
  mockedDashboard.dataQuality!.checks = checks;
};

vi.mock("./data-source", async () => {
  const actual = await vi.importActual<typeof import("./data-source")>("./data-source");
  return {
    ...(actual as object),
    useDashboardData: () => mockedDashboard,
    getActionPlanItems: vi.fn().mockResolvedValue([]),
    getDashboardSession: vi.fn().mockResolvedValue({
      user: { id: "u1", name: "demo", role: "owner" },
      tenant: { id: "t1", name: "Demo tenant" },
      mode: "mock",
    }),
    updateActionPlanItem: vi.fn().mockResolvedValue({ id: "a", title: "", sku: "", status: "created" }),
    createTenantBackup: vi.fn().mockResolvedValue({ id: 1, name: "auto", status: "ok" }),
    createActionPlanItem: vi.fn().mockResolvedValue({ id: "a", status: "created" }),
    applyFifoReconciliation: vi.fn().mockResolvedValue({ ok: true }),
    applyIncidentLoss: vi.fn().mockResolvedValue({ ok: true }),
    closeProductionExecution: vi.fn().mockResolvedValue({ ok: true }),
    createIncidentCase: vi.fn().mockResolvedValue({ ok: true }),
    createIncidentCompensation: vi.fn().mockResolvedValue({ ok: true }),
    createProcurementOperation: vi.fn().mockResolvedValue({ id: 1, status: "ok" }),
    createProcurementPayment: vi.fn().mockResolvedValue({ ok: true }),
    createProductionShift: vi.fn().mockResolvedValue({ saved: 1, status: "Ок" }),
    createReportDefinition: vi.fn().mockResolvedValue({}),
    deleteReportDefinition: vi.fn().mockResolvedValue({ ok: true }),
    downloadReportPdf: vi.fn().mockResolvedValue({}),
    downloadSavedReport: vi.fn().mockResolvedValue({}),
    generateQuickReport: vi.fn().mockResolvedValue({}),
    generateReportDefinition: vi.fn().mockResolvedValue({}),
    getFifoReconciliation: vi.fn().mockResolvedValue({ preview: [], status: {}, guard: {}, wb_context: {}, runs: [] }),
    getIncidentOperations: vi.fn().mockResolvedValue({ cases: [], summary: {} }),
    getMaterialInventory: vi.fn().mockResolvedValue({ items: [], fifo: [], opening_rate_rub_m: 0 }),
    getProcurementOperations: vi.fn().mockResolvedValue({ orders: [], items: [] }),
    getFbsSupplyDetails: vi.fn().mockResolvedValue({ supplyId: "WB-123", orders: [{ orderId: "O-1" }] }),
    getFbsSupplyBarcode: vi.fn().mockResolvedValue({ barcode: "base64-data", type: "png" }),
    sendCustomerChatMessage: vi.fn().mockResolvedValue({ ok: true }),
    getProductionExecution: vi.fn().mockResolvedValue({}),
    getProductionNorms: vi.fn().mockResolvedValue({ items: [] }),
    getProductCostSettings: vi.fn().mockResolvedValue({ items: [] }),
    getReportDelivery: vi.fn().mockResolvedValue({ configured: false, email_configured: false }),
    getReportOperations: vi.fn().mockResolvedValue({ definitions: [], runs: [], metric_labels: {} }),
    getSuppliers: vi.fn().mockResolvedValue({ items: [] }),
    getTenantBackups: vi.fn().mockResolvedValue({ backups: [] }),
    getTenantSettings: vi.fn().mockResolvedValue({
      sync_interval_minutes: 60,
      initial_history_days: 30,
      agent_interval_minutes: 60,
      wb_connected: true,
      ozon_connected: false,
    }),
    getWBTokenStatus: vi.fn().mockResolvedValue({ connected: true, scopes: ["Статистика"], missing_scopes: [], scope_metadata_available: false, read_only: false, expires_at: null }),
    getTenantSyncHistory: vi.fn().mockResolvedValue({ runs: [] }),
    getTenantSyncStatus: vi.fn().mockResolvedValue({ status: "idle" }),
    initializeMaterialFifo: vi.fn().mockResolvedValue({ result: { created: 0, meters: 0, materials: 0, warnings: [] }, fifo: [], opening_rate_rub_m: 0 }),
    postSupplyReceipt: vi.fn().mockResolvedValue({}),
    refreshAgentRecommendations: vi.fn().mockResolvedValue({}),
    requestTelegramLinkCode: vi.fn().mockResolvedValue({}),
    saveProductionExecution: vi.fn().mockResolvedValue({ ok: true }),
    saveSupplier: vi.fn().mockResolvedValue({}),
    startTenantSync: vi.fn().mockResolvedValue(undefined),
    updateProductCostSettings: vi.fn().mockResolvedValue({}),
    updateProductionNorms: vi.fn().mockResolvedValue({}),
    updateMaterialInventory: vi.fn().mockResolvedValue({}),
    updateTenantSettings: vi.fn().mockResolvedValue({}),
    getRuntimeStatus: vi.fn().mockResolvedValue([{ enabled: false, jobs: {} }, { sections: {} }]),
  };
});

describe("operational detail drawer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setChecks(defaultChecks);
    mockedDashboard.financeReport = { rows: [] };
    mockedDashboard.supplyShipments = [];
    mockedDashboard.procurementOrders = [];
    mockedDashboard.refresh = vi.fn(async () => undefined);
    vi.mocked(getProcurementOperations).mockResolvedValue({ orders: [], items: [] });
    mockedDashboard.customerQuality = null;
    mockedDashboard.productCardErrors = [];
    mockedDashboard.productPriceErrors = [];
    if (!Element.prototype.scrollIntoView) {
      Element.prototype.scrollIntoView = vi.fn();
    }
    window.history.replaceState({}, "", "/?view=products");
  });

  it("opens a product card drawer on click", async () => {
    const { default: App } = await import("./App");
    const user = userEvent.setup();
    render(<App />);

    const productCard = await screen.findByRole("button", { name: /Товар для проверки/i });
    await user.click(productCard);

    expect(screen.getByRole("heading", { name: /Товар для проверки/i })).toBeInTheDocument();
    expect(screen.getByText(/Артикул: SKU-1/i)).toBeInTheDocument();
    expect(screen.getByText(/Выручка:/i)).toBeInTheDocument();
  });

  it("enables receipt of a newly created purchase after refresh without remounting supplies", async () => {
    const order = { id: 41, supplier_name: "QA supplier", status: "Запланировано", outstanding_amount: 376.5 };
    const item = { id: 42, order_id: 41, product_name: "QA product", quantity: 3, posted_quantity: 0 };
    vi.mocked(getProcurementOperations)
      .mockResolvedValueOnce({ orders: [], items: [] })
      .mockResolvedValue({ orders: [order], items: [item] });
    vi.mocked(createProcurementOperation).mockResolvedValueOnce({ id: 41, status: "Запланировано" });
    mockedDashboard.refresh = vi.fn(async () => { mockedDashboard.procurementOrders = [order]; });
    window.history.replaceState({}, "", "/?view=supply");
    const { default: App } = await import("./App");
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Создать заявку" }));
    const form = within(screen.getByRole("dialog"));
    await user.type(form.getByLabelText("Поставщик"), "QA supplier");
    await user.type(form.getByLabelText("Артикул поставщика"), "QA-41");
    await user.type(form.getByLabelText("Товар"), "QA product");
    await user.clear(form.getByLabelText("Количество"));
    await user.type(form.getByLabelText("Количество"), "3");
    await user.clear(form.getByLabelText("Цена за единицу"));
    await user.type(form.getByLabelText("Цена за единицу"), "125.5");
    await user.click(form.getByRole("button", { name: "Подтвердить" }));

    await screen.findByText("Заявка #41");
    expect(createProcurementOperation).toHaveBeenCalledWith(expect.objectContaining({ quantity: 3, unitPrice: 125.5 }));
    const receiptButton = screen.getByRole("button", { name: "Провести приёмку" });
    await waitFor(() => expect(receiptButton).toBeEnabled());
    await user.click(receiptButton);
    const receipt = within(screen.getByRole("dialog", { name: "Провести приёмку" }));
    expect(receipt.getByLabelText("Принято единиц")).toHaveValue(3);
    expect(receipt.getByRole("option", { name: /QA product/ })).toHaveValue("42");
  });

  it("keeps refreshed purchase items when an older request finishes later", async () => {
    let resolveInitial!: (data: Awaited<ReturnType<typeof getProcurementOperations>>) => void;
    const initial = new Promise<Awaited<ReturnType<typeof getProcurementOperations>>>((resolve) => { resolveInitial = resolve; });
    const order = { id: 41, supplier_name: "QA supplier", status: "Запланировано", outstanding_amount: 376.5 };
    const item = { id: 42, order_id: 41, product_name: "QA product", quantity: 3, posted_quantity: 0 };
    vi.mocked(getProcurementOperations)
      .mockReturnValueOnce(initial)
      .mockResolvedValue({ orders: [order], items: [item] });
    window.history.replaceState({}, "", "/?view=supply");
    const { default: App } = await import("./App");
    const { rerender } = render(<App />);
    await waitFor(() => expect(getProcurementOperations).toHaveBeenCalledTimes(1));

    mockedDashboard.procurementOrders = [order];
    rerender(<App />);
    const receiptButton = await screen.findByRole("button", { name: "Провести приёмку" });
    await waitFor(() => expect(receiptButton).toBeEnabled());

    await act(async () => { resolveInitial({ orders: [], items: [] }); });
    expect(receiptButton).toBeEnabled();
  });

  it("keeps account menu links actionable through the gateway", async () => {
    const { default: App } = await import("./App");
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: /Текущий аккаунт: Demo tenant/i }));

    expect(screen.getByRole("menuitem", { name: /Подписка и оплата/i })).toHaveAttribute("href", expect.stringMatching(/\/billing$/));
    expect(screen.getByRole("menuitem", { name: /Аккаунт/i })).toHaveAttribute("href", expect.stringMatching(/\/account$/));
    expect(screen.getByRole("menuitem", { name: /Документы/i })).toHaveAttribute("href", expect.stringMatching(/\/offer$/));
    expect(screen.getByRole("menuitem", { name: /Поддержка/i })).toHaveAttribute("href", expect.stringMatching(/\/contacts$/));
  });

  it("shows a Content API error instead of an empty cards state", async () => {
    mockedDashboard.productCardErrors = ["/api/v1/products/cards: HTTP 403"];
    window.history.replaceState({}, "", "/?view=products");
    const { default: App } = await import("./App");
    render(<App />);

    expect(await screen.findByText("Карточки WB недоступны: /api/v1/products/cards: HTTP 403")).toBeInTheDocument();
    expect(screen.getByText("После исправления доступа обновите раздел.")).toBeInTheDocument();
  });

  it("shows a delivery error instead of silently disabling report channels", async () => {
    vi.mocked(getReportDelivery).mockRejectedValueOnce(new Error("SMTP не настроен"));
    window.history.replaceState({}, "", "/?view=reports");
    const { default: App } = await import("./App");
    render(<App />);

    expect(await screen.findByText("Настройки доставки недоступны: SMTP не настроен")).toBeInTheDocument();
  });

  it("explains where a new Telegram link code must be used", async () => {
    vi.mocked(getReportDelivery).mockResolvedValueOnce({ configured: true, email_configured: true, email: "client@example.com", telegram_configured: true, telegram_linked: false, telegram_bot_username: "marketshelper_support_bot" });
    const { requestTelegramLinkCode } = await import("./data-source");
    vi.mocked(requestTelegramLinkCode).mockResolvedValueOnce({ code: "RAUQVV", ttl_minutes: 15, deep_link: "https://t.me/marketshelper_support_bot?start=link_RAUQVV", bot_username: "marketshelper_support_bot" });
    window.history.replaceState({}, "", "/?view=reports");
    const { default: App } = await import("./App");
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Привязать Telegram" }));
    expect(await screen.findByText("Как привязать Telegram:")).toBeInTheDocument();
    expect(screen.getByText("@marketshelper_support_bot")).toBeInTheDocument();
    expect(screen.getByText(/оператору или в личные сообщения его отправлять не нужно/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "«Аккаунт»" })).toHaveAttribute("href", expect.stringMatching(/\/account$/));
  });

  it("shows report and analytics errors instead of false empty states", async () => {
    vi.mocked(getReportOperations).mockRejectedValueOnce(new Error("API расписаний недоступно"));
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Analytics timeout")));
    window.history.replaceState({}, "", "/?view=reports");
    const { default: App } = await import("./App");
    render(<App />);

    expect(await screen.findByText("Расписания недоступны: API расписаний недоступно")).toBeInTheDocument();
    expect(await screen.findByText("Аналитика WB недоступна: Analytics timeout")).toBeInTheDocument();
    expect(screen.getByText("После восстановления доступа обновите аналитику.")).toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("shows customer-quality access errors instead of empty counters", async () => {
    mockedDashboard.customerQuality = { rating: {}, unread: {}, feedbacks: [], questions: [], quarantine: [], returns: {}, return_history: [], chats: [], errors: ["/api/v1/dashboard/customer-quality: HTTP 403"] };
    window.history.replaceState({}, "", "/?view=products");
    const { default: App } = await import("./App");
    render(<App />);

    expect(await screen.findByText("Часть данных WB недоступна: /api/v1/dashboard/customer-quality: HTTP 403")).toBeInTheDocument();
  });

  it("shows prices access errors instead of an empty prices state", async () => {
    mockedDashboard.productPriceErrors = ["/api/v1/products/prices: HTTP 403"];
    window.history.replaceState({}, "", "/?view=products");
    const { default: App } = await import("./App");
    render(<App />);

    expect(await screen.findByText("Цены WB недоступны: /api/v1/products/prices: HTTP 403")).toBeInTheDocument();
  });

  it("opens an FBS supply and requests its QR code", async () => {
    mockedDashboard.supplyShipments = [{ id: "WB-123", supplier: "Поставка WB", status: "активна", eta: "сегодня", amount: "WB FBS", tone: "info" }];
    window.history.replaceState({}, "", "/?view=supply");
    const { default: App } = await import("./App");
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Открыть поставку WB" }));
    expect(getFbsSupplyDetails).toHaveBeenCalledWith("WB-123");
    expect(await screen.findByRole("heading", { name: "Поставка WB-123" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Получить QR / стикер" }));
    expect(getFbsSupplyBarcode).toHaveBeenCalledWith("WB-123");
  });

  it("persists edited shift execution before closing the shift", async () => {
    const task = {
      task_key: "shift:sku-1",
      task_type: "production",
      task_date: "2026-09-18",
      stage: "Пошив",
      nm_id: 101,
      supplier_article: "SKU-1",
      product_name: "Товар для проверки",
      planned_units: 2,
      actual_units: 0,
      status: "Не начато",
      note: "",
    };
    vi.mocked(getProductionExecution).mockResolvedValue({ tasks: [task] });
    vi.mocked(saveProductionExecution).mockResolvedValue({ saved: 1, status: "saved" });
    vi.mocked(closeProductionExecution).mockResolvedValue({ posted: 1, units: 1, skipped: 0 });
    window.history.replaceState({}, "", "/?view=production");
    const { default: App } = await import("./App");
    const user = userEvent.setup();
    render(<App />);

    const actual = await screen.findByLabelText("Факт, ед.");
    await user.clear(actual);
    await user.type(actual, "1");
    await user.selectOptions(screen.getByDisplayValue("Не начато"), "Изготовлено");
    await user.click(screen.getByRole("button", { name: "Закрыть смену" }));
    await user.click(screen.getByRole("button", { name: "Подтвердить" }));

    expect(saveProductionExecution).toHaveBeenCalledWith(expect.arrayContaining([
      expect.objectContaining({ task_key: "shift:sku-1", actual_units: 1, status: "Изготовлено" }),
    ]));
    expect(closeProductionExecution).toHaveBeenCalledWith("2026-09-18");
    expect(vi.mocked(saveProductionExecution).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(closeProductionExecution).mock.invocationCallOrder[0]);
  });

  it.each(["Проверить остатки", "Сверка FIFO"])("does not complete %s just by opening it", async (title) => {
    const item = { id: "release-task", title, sku: "SKU", source: "dashboard", status: "created" as const, priority: "medium" as const, created_at: "2026-09-17", updated_at: "2026-09-17" };
    vi.mocked(getActionPlanItems).mockResolvedValueOnce([item]);
    vi.mocked(updateActionPlanItem).mockImplementationOnce(async (_id, patch) => ({ ...item, ...patch }));
    const { default: App } = await import("./App");
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Открыть план задач" }));
    await user.click(await screen.findByRole("button", { name: "В работу" }));
    expect(updateActionPlanItem).toHaveBeenCalledWith("release-task", { status: "in_progress" });
    expect(vi.mocked(updateActionPlanItem).mock.calls.some(([, patch]) => patch.status === "done")).toBe(false);
  });

  it("preserves exact custom dates in finance download", () => {
    const url = financeExportUrl({ id: "custom", start: "2026-05-01", end: "2026-05-07" });
    expect(url).toContain("period=custom&start=2026-05-01&end=2026-05-07");
  });

  it("reads measurement penalties and reversals rather than treating them as zero", () => {
    const report = adaptFinanceReport(undefined, { rows: [] }, {
      deductions: [{ bonusSumm: 245.5 }],
      measurement_penalties: [{ penaltyAmount: 400.25, reversalAmount: 100 }, { penaltyAmount: 20 }],
      paid_storage: [{ warehousePrice: 77.5 }, { warehousePrice: 22.5 }],
      errors: [],
    });
    expect(report.wbCosts?.deductions).toBe(245.5);
    expect(report.wbCosts?.measurementPenalties).toBe(320.25);
    expect(report.wbCosts?.paidStorage).toBe(100);
  });

  it("loads the WB analytics contract without turning provider errors into zeros", async () => {
    const response = { period: { start: "2026-09-01", end: "2026-09-07" }, sales_funnel: { stages: [{ key: "orders", label: "Заказы", value: 5 }], totals: { orders: 5 }, products: [], source: "WB Analytics v3" }, stock_history: [{ date: "2026-09-07", quantity: 42, rows: 3 }], errors: {}, partial: false };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => response }));
    await expect(getAnalyticsDashboard("7d")).resolves.toEqual(response);
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/v1/dashboard/analytics?period=7d"), expect.any(Object));
    vi.unstubAllGlobals();
  });

  it("sends a buyer chat reply with the chat signature", async () => {
    mockedDashboard.customerQuality = { rating: {}, unread: {}, feedbacks: [], questions: [], quarantine: [], returns: {}, return_history: [], chats: [{ chatId: "chat-1", replySign: "1:chat-signature", lastMessage: { text: "Здравствуйте" } }], errors: [] };
    window.history.replaceState({}, "", "/?view=products");
    const { default: App } = await import("./App");
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Ответить" }));
    const editor = document.querySelector("dialog textarea") as HTMLTextAreaElement;
    expect(editor).toBeTruthy();
    await user.clear(editor);
    await user.type(editor, "Готовы помочь");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(sendCustomerChatMessage).toHaveBeenCalledWith("1:chat-signature", "Готовы помочь");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));
  });

  it("explains a normal one-day WB lag instead of marking the report as broken", async () => {
    mockedDashboard.financeReport = {
      rows: [{ label: "Логистика", amount: -100 }],
      complete: false,
      lastDate: "16.09.2026",
      calculationEnd: "16.09.2026",
      lagDays: 1,
    };
    window.history.replaceState({}, "", "/?view=finance");
    const { default: App } = await import("./App");
    render(<App />);

    expect(await screen.findByText("WB обновляет данные")).toBeInTheDocument();
    expect(screen.getByText(/WB ещё не закрыл последний день/i)).toBeInTheDocument();
    expect(screen.queryByText("отчёт неполный")).not.toBeInTheDocument();
  });

  it("opens control-check card drawer on Enter key", async () => {
    window.history.replaceState({}, "", "/?view=control");
    const { default: App } = await import("./App");
    const user = userEvent.setup();
    render(<App />);

    const checkButton = await screen.findByRole("button", { name: /Проверка FIFO/i });
    checkButton.focus();
    await user.keyboard("{Enter}");

    expect(screen.getByRole("heading", { name: /Проверка FIFO/i })).toBeInTheDocument();
    expect(screen.getByText("Подробнее: Нет данных по последним партиям")).toBeInTheDocument();
    expect(screen.getByText(/^Рекомендуемое действие: Сверьте входящие заказы и остатки$/i)).toBeInTheDocument();
  });

  it("navigates from a control check to production norms settings section", async () => {
    window.history.replaceState({}, "", "/?view=control");
    setChecks([
      {
        Модуль: "Контроль",
        Проверка: "Нормы производства",
        Статус: "Внимание",
        Детали: "Нормы для новых партий не заданы",
        "Что сделать": "Откройте раздел норм производства",
      },
    ]);

    const { default: App } = await import("./App");
    const user = userEvent.setup();
    render(<App />);

    const checkCard = await screen.findByRole("button", { name: /Нормы производства/i });
    await user.click(checkCard);
    await user.click(await screen.findByRole("button", { name: /^Открыть раздел$/i }));

    expect(window.location.href).toContain("view=settings");
    expect(await screen.findByRole("heading", { name: /Нормы материала/i })).toBeInTheDocument();
  });

  it("navigates from a control check to product-costs settings section", async () => {
    window.history.replaceState({}, "", "/?view=control");
    setChecks([
      {
        Модуль: "Контроль",
        Проверка: "Себестоимость",
        Статус: "Внимание",
        Детали: "Не обновлена себестоимость по новому товару",
        "Что сделать": "Проверьте карточки себестоимость",
      },
    ]);

    const { default: App } = await import("./App");
    const user = userEvent.setup();
    render(<App />);

    const checkCard = await screen.findByRole("button", { name: /Себестоимость/i });
    await user.click(checkCard);
    await user.click(await screen.findByRole("button", { name: /^Открыть раздел$/i }));

    expect(window.location.href).toContain("view=settings");
    expect(await screen.findByRole("heading", { name: /Себестоимость товаров/i })).toBeInTheDocument();
  });
});
