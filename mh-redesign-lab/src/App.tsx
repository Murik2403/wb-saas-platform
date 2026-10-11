import { ThemeToggle } from "./ThemeToggle";
import { secondaryActions } from "./CabinetSupplementary";
import { useDialogFocus } from "./dialog-focus";
import { currentShop } from "./selected-shop";
import { ShopContextBoundary } from "./ShopContextBoundary";
import { WorkspaceTabs } from "./WorkspaceTabs";
import { MarketplaceBoundary, MarketplaceSwitch } from "./MarketplaceBoundary";
import { isNativeApp } from "./app-shell";
import { useEffect, useRef, useState } from "react";
import { AlertTriangle, ArrowUpRight, BarChart3, Bot, CalendarDays, CheckCircle2, ClipboardCheck, ChevronRight, Command, Factory, Filter, Download, LayoutDashboard, LucideIcon, Megaphone, Menu, Package, PackageCheck, PackageX, Plus, RefreshCw, Search, ChevronDown, ShieldCheck, SlidersHorizontal, House, LogOut, Truck, WalletCards, X } from "lucide-react";
import type { ActionRecommendation, DashboardMetric, IconKey, InventoryWatchRow, MaterialNeed, NavItem, PageId, ProductionStage, ProductionTask, ReportCard, ReorderItem, SupplyShipment, StockCoverageRow, StockRule } from "./contracts";
import { applyFifoReconciliation, applyIncidentLoss, createActionPlanItem, createIncidentCase, createIncidentCompensation, createProcurementOperation, createProductionShift, createTenantBackup, downloadReportPdf, generateQuickReport, getActionPlanItems, getDashboardSession, getFifoReconciliation, getIncidentOperations, getProductionNorms, getTenantSyncStatus, refreshAgentRecommendations, startTenantSync, updateActionPlanItem, useDashboardData, type ActionPlanItem, type DashboardPeriod, type DashboardPeriodId, type DashboardSession, type DashboardViewModel, type FifoReconciliationData, type IncidentOperations, type ProductEconomicsRow, type ProductionNorm } from "./data-source";
import { navItems as fallbackNavItems } from "./mock-dashboard";
import { LazyPanel, lazyNamed } from "./LazyPanel";
import { InventoryTable, MetricCard, MetricGrid, SectionHeader, Stat, StatusPill, askConfirm, askText, cx, dashboardPeriodOptions, defaultCustomPeriod, gatewayUrlFor } from "./cabinet-shared";
import type { ComponentProps } from "react";
const SelectedShopSettings = lazyNamed(() => import("./SelectedShopSettings"), "SelectedShopSettings");
const SharedWarehouse = lazyNamed(() => import("./SharedWarehouse"), "SharedWarehouse");
const SelfBuyoutsPanel = lazyNamed(() => import("./SelfBuyoutsPanel"), "SelfBuyoutsPanel");
const CashflowPanel = lazyNamed(() => import("./CashflowPanel"), "CashflowPanel");
const MigrationImport = lazyNamed(() => import("./MigrationImport"), "MigrationImport");
const OzonCabinetShell = lazyNamed(() => import("./OzonCabinetShell"), "OzonCabinetShell");

const iconByKey: Record<IconKey, LucideIcon> = {
  dashboard: LayoutDashboard,
  stock: Package,
  ads: Megaphone,
  finance: WalletCards,
  production: Factory,
  supply: Truck,
  assistant: Bot,
  reports: BarChart3,
  products: PackageCheck,
  control: ClipboardCheck,
  settings: SlidersHorizontal,
  packageX: PackageX,
  arrowUpRight: ArrowUpRight,
};

function isPageId(value: string | null): value is PageId {
  return fallbackNavItems.some((item) => item.id === value);
}

function getInitialPage(): PageId {
  const url = new URL(window.location.href);
  const requestedPage = url.searchParams.get("view") ?? window.location.hash.replace("#", "");
  return isPageId(requestedPage) ? requestedPage : "today";
}

function isDashboardPeriodId(value: string | null): value is DashboardPeriodId {
  return dashboardPeriodOptions.some((option) => option.id === value);
}

type InstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};

type DrawerDetail = {
  title: string;
  subtitle: string;
  facts: Array<string>;
  page?: PageId;
  target?: string;
  actionLabel?: string;
};

function getInitialDashboardPeriod(): DashboardPeriod {
  const url = new URL(window.location.href);
  const id = url.searchParams.get("period");
  if (id === "custom") {
    const start = url.searchParams.get("start") ?? undefined;
    const end = url.searchParams.get("end") ?? undefined;
    return start && end ? { id, start, end } : defaultCustomPeriod();
  }
  return isDashboardPeriodId(id) ? { id } : { id: "30d" };
}

function recommendationDestination(action: ActionRecommendation): { page: PageId; target?: string } {
  const context = `${action.title} ${action.issue} ${action.owner}`.toLocaleLowerCase("ru-RU");
  if (action.iconKey === "ads" || action.iconKey === "arrowUpRight" || /реклам|ставк|ctr|фото|бид/.test(context)) {
    return { page: "ads", target: "ads-campaigns" };
  }
  if (action.iconKey === "packageX" || /запас|закуп|пополн|постав/.test(context)) {
    return { page: "supply", target: "supply-orders" };
  }
  if (/производ|смен|парт/.test(context)) {
    return { page: "production", target: "production-shift" };
  }
  return { page: "products" };
}

function App() {
  const [activeNav, setActiveNav] = useState<PageId>(getInitialPage);
  const [dashboardPeriod, setDashboardPeriod] = useState<DashboardPeriod>(getInitialDashboardPeriod);
  const dashboard = useDashboardData(activeNav, dashboardPeriod);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [trendPeriod, setTrendPeriod] = useState<"day" | "week">("day");
  const [toast, setToast] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [planItems, setPlanItems] = useState<ActionPlanItem[]>([]);
  const [planOpen, setPlanOpen] = useState(false);
  const [planFilter, setPlanFilter] = useState<"all" | ActionPlanItem["status"]>("all");
  const planExecutionLock = useRef(false);
  const [planBusyId, setPlanBusyId] = useState<string | null>(null);
  const [session, setSession] = useState<DashboardSession | null>(null);
  const [operation, setOperation] = useState<"procurement" | "production" | null>(null);
  const [operationItem, setOperationItem] = useState("");
  const [pendingFifoPlanId, setPendingFifoPlanId] = useState<string | null>(null);
  const [pendingPlanOperationId, setPendingPlanOperationId] = useState<string | null>(null);
  const [operationBusy, setOperationBusy] = useState(false);
  const [fifoOpen, setFifoOpen] = useState(false);
  const [fifoData, setFifoData] = useState<FifoReconciliationData | null>(null);
  const [fifoBusy, setFifoBusy] = useState(false);
  const [actionDetail, setActionDetail] = useState<{ title: string; description: string; page: PageId; target?: string } | null>(null);
  const [drawerDetail, setDrawerDetail] = useState<DrawerDetail | null>(null);
  const [pendingScrollTarget, setPendingScrollTarget] = useState<string | null>(null);
  const [installPrompt, setInstallPrompt] = useState<InstallPromptEvent | null>(null);
  const [sessionMenuOpen, setSessionMenuOpen] = useState(false);
  const sessionShellRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const capturePrompt = (event: Event) => {
      event.preventDefault();
      setInstallPrompt(event as InstallPromptEvent);
    };
    const clearPrompt = () => setInstallPrompt(null);
    window.addEventListener("beforeinstallprompt", capturePrompt);
    window.addEventListener("appinstalled", clearPrompt);
    return () => {
      window.removeEventListener("beforeinstallprompt", capturePrompt);
      window.removeEventListener("appinstalled", clearPrompt);
    };
  }, []);

  const installApp = async () => {
    if (!installPrompt) return;
    await installPrompt.prompt();
    const choice = await installPrompt.userChoice;
    if (choice.outcome === "accepted") setInstallPrompt(null);
  };

  const getGatewayUrl = gatewayUrlFor;

  const getLogoutUrl = () => {
    return getGatewayUrl("/logout");
  };

  const handleLogout = () => {
    setSessionMenuOpen(false);
    window.location.assign(getLogoutUrl());
  };

  const goToHomePage = () => {
    setSessionMenuOpen(false);
    selectPage("today");
  };

  useEffect(() => {
    if (!toast) return undefined;
    const timeoutId = window.setTimeout(() => setToast(null), 4500);
    return () => window.clearTimeout(timeoutId);
  }, [toast]);

  useEffect(() => {
    const closeMenu = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (sessionShellRef.current && target && !sessionShellRef.current.contains(target)) {
        setSessionMenuOpen(false);
      }
    };
    if (!sessionMenuOpen) return undefined;
    window.addEventListener("mousedown", closeMenu);
    return () => window.removeEventListener("mousedown", closeMenu);
  }, [sessionMenuOpen]);

  useEffect(() => {
    void getActionPlanItems().then((items) => {
      setPlanItems(items);
      window.localStorage.removeItem("mh-planned-items");
    }).catch(() => undefined);
  }, []);

  useEffect(() => {
    void getDashboardSession().then(setSession).catch(() => undefined);
  }, []);

  useEffect(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("period", dashboardPeriod.id);
    if (dashboardPeriod.id === "custom") {
      if (dashboardPeriod.start) url.searchParams.set("start", dashboardPeriod.start);
      if (dashboardPeriod.end) url.searchParams.set("end", dashboardPeriod.end);
    } else {
      url.searchParams.delete("start");
      url.searchParams.delete("end");
    }
    window.history.replaceState(null, "", url);
  }, [dashboardPeriod]);

  const activePlanItems = planItems.filter((item) => item.status !== "done" && item.status !== "cancelled");
  const activeMeta = dashboard.pageMeta[activeNav];
  const topAction = dashboard.actions[0];
  const normalizedQuery = searchQuery.trim().toLocaleLowerCase("ru-RU");
  const searchResults = normalizedQuery.length > 1
    ? [
        ...dashboard.navItems.map((item) => ({
          kind: "Раздел",
          label: item.label,
          detail: dashboard.pageMeta[item.id].title,
          page: item.id,
        })),
        ...dashboard.inventory.map((item) => ({
          kind: "SKU",
          label: `${item.sku} · ${item.name}`,
          detail: `Запас ${item.stock} · ${item.signal}`,
          page: "stock" as PageId,
        })),
        ...dashboard.campaigns.map((item) => ({
          kind: "Кампания",
          label: item.name,
          detail: `${item.acos} ACoS · ${item.signal}`,
          page: "ads" as PageId,
        })),
      ]
        .filter((result) => `${result.kind} ${result.label} ${result.detail}`.toLocaleLowerCase("ru-RU").includes(normalizedQuery))
        .slice(0, 7)
    : [];

  const handleRefresh = async () => {
    await dashboard.refresh();
    if (dashboard.connection.mode === "live" || dashboard.connection.mode === "stale") {
      setToast("Данные обновлены");
    }
  };

  useEffect(() => {
    if (!pendingScrollTarget) return undefined;
    const target = pendingScrollTarget;
    const giveUpAt = performance.now() + 6000; // the page chunk may still be loading: keep the target until the element appears
    let frame = 0;
    const seek = () => {
      const element = document.getElementById(target);
      if (!element) {
        if (performance.now() < giveUpAt) frame = window.requestAnimationFrame(seek);
        else setPendingScrollTarget(null);
        return;
      }
      element.scrollIntoView({ behavior: "smooth", block: "start" });
      element.classList.add("section-focus");
      window.setTimeout(() => element.classList.remove("section-focus"), 1200);
      setPendingScrollTarget(null);
    };
    frame = window.requestAnimationFrame(seek);
    return () => window.cancelAnimationFrame(frame);
  }, [activeNav, pendingScrollTarget]);

  const selectPage = (id: PageId, target?: string) => {
    setActiveNav(id);
    setMobileMenuOpen(false);
    if (target) setPendingScrollTarget(target);
    const url = new URL(window.location.href);
    url.searchParams.set("view", id);
    window.history.replaceState(null, "", url);
  };

  const selectSearchResult = (page: PageId) => {
    selectPage(page);
    setSearchQuery("");
  };

  const runTenantSync = async () => {
    await startTenantSync();
    for (let attempt = 0; attempt < 60; attempt += 1) {
      const status = await getTenantSyncStatus();
      if (status.status === "running") {
        setToast("Синхронизация WB выполняется");
        await new Promise((resolve) => window.setTimeout(resolve, 5000));
        continue;
      }
      if (status.status !== "success" && status.status !== "partial") {
        throw new Error(status.details || "Синхронизация завершилась с ошибкой");
      }
      await dashboard.refresh();
      setToast(status.status === "partial" ? "Данные обновлены частично. Подробности — в журнале синхронизации." : "Синхронизация завершена, данные обновлены");
      return status.status;
    }
    throw new Error("Синхронизация продолжается в фоне. Задача остаётся в работе; проверьте результат позднее.");
  };

  const setPlanStatus = async (item: ActionPlanItem, status: ActionPlanItem["status"], result?: string) => {
    const updated = await updateActionPlanItem(item.id, { status, ...(result ? { result } : {}) });
    setPlanItems((items) => items.map((current) => current.id === item.id ? updated : current));
    return updated;
  };

  const executePlanItem = async (item: ActionPlanItem) => {
    if (planExecutionLock.current || item.status === "done" || item.status === "cancelled") return;
    planExecutionLock.current = true;
    setPlanBusyId(item.id);
    const normalized = `${item.title} ${item.sku}`.toLocaleLowerCase("ru-RU");
    try {
      if (normalized.includes("копи") || normalized.includes("резервн")) {
        if (session?.mode === "demo" || session?.user.role !== "owner") {
          setToast("Резервные копии доступны владельцу кабинета.");
          return;
        }
        const backup = await createTenantBackup();
        await dashboard.refresh();
        await setPlanStatus(item, "done", `Резервная копия ${backup.name} создана.`);
        setToast(`Резервная копия создана: ${backup.name}`);
        return;
      }
      if (normalized.includes("синхрон") || normalized.includes("устар")) {
        await setPlanStatus(item, "in_progress");
        const status = await runTenantSync();
        await setPlanStatus(item, status === "success" ? "done" : "blocked", status === "success" ? "Синхронизация WB завершена, данные обновлены." : "Синхронизация частичная. Проверьте журнал перед завершением задачи.");
        return;
      }
      if (normalized.includes("fifo")) {
        const data = await getFifoReconciliation();
        setFifoData(data);
        setFifoOpen(true);
        setPendingFifoPlanId(item.id);
        setPlanOpen(false);
        await setPlanStatus(item, "in_progress");
        setToast("Сверка FIFO открыта");
        return;
      }
      if (normalized.includes("отчет") || normalized.includes("экспорт") || normalized.includes("pdf")) {
        await downloadReportPdf(item.title || "Операционный отчёт", 30);
        await setPlanStatus(item, "done", "PDF отчёта сформирован и скачан.");
        setToast("Отчёт сформирован и скачан");
        return;
      }
      if (normalized.includes("заказ") || normalized.includes("поставщ") || normalized.includes("докуп") || normalized.includes("заявк")) {
        setOperationItem(item.sku);
        setOperation("procurement");
        setPendingPlanOperationId(item.id);
        setPlanOpen(false);
        await setPlanStatus(item, "in_progress");
        setToast("Открыта форма заявки. После сохранения операция будет выполнена.");
        return;
      }
      if (normalized.includes("производ") || normalized.includes("смен") || normalized.includes("парт")) {
        setOperationItem(item.sku || dashboard.productionTasks[0]?.sku || "");
        setOperation("production");
        setPendingPlanOperationId(item.id);
        setPlanOpen(false);
        await setPlanStatus(item, "in_progress");
        setToast("Открыта форма производственной смены. После сохранения операция будет выполнена.");
        return;
      }

      // Navigation starts a task; only a recorded operation or explicit result completes it.
      if (normalized.includes("провер") || normalized.includes("контрол")) {
        selectPage("control");
        setActionDetail({ title: `Контроль: ${item.sku || item.title}`, description: "Проверка открыта в разделе контроля качества данных.", page: "control" });
        setPlanOpen(false);
        await setPlanStatus(item, "in_progress");
        setToast("Проверка открыта. После проверки укажите результат в плане задач.");
        return;
      }

      handleAction(item.title, item.sku);
      setPlanOpen(false);
      await setPlanStatus(item, "in_progress");
      setToast(`${item.title}: задача в работе`);
    } catch (error) {
      setToast(error instanceof Error ? error.message : "Задача не выполнена");
    } finally {
      planExecutionLock.current = false;
      setPlanBusyId(null);
    }
  };

  const handleAction = (label: string, itemLabel: string) => {
    const normalizedLabel = label.toLocaleLowerCase("ru-RU");
    const recommendation = dashboard.actions.find((action) => action.title === label && action.sku === itemLabel);
    if (recommendation) {
      const destination = recommendationDestination(recommendation);
      selectPage(destination.page, destination.target);
      return;
    }
    if (label === "Налоги") {
      selectPage("finance", "tax-reconciliation");
      return;
    }
    if (label === "Производственные нормы") {
      selectPage("settings", "production-norms");
      return;
    }
    if (label === "Остатки сырья") {
      selectPage("settings", "material-inventory");
      return;
    }
    if (label === "Рабочая смена") {
      selectPage("production", "production-shift");
      return;
    }
    if (normalizedLabel === "реклама сегодня") {
      selectPage("ads");
      setActionDetail({ title: `Реклама: ${itemLabel}`, description: "Открыта рекламная аналитика. Здесь можно проверить кампании, воронку и товары в ставках за выбранный период.", page: "ads", target: "ads-campaigns" });
      return;
    }
    if (normalizedLabel === "заказы сегодня" || normalizedLabel === "выкупы сегодня") {
      selectPage("finance");
      setActionDetail({ title: `Продажи: ${itemLabel}`, description: "Открыт финансовый контур с заказами, выкупами и движением денег за выбранный период.", page: "finance" });
      return;
    }
    if (normalizedLabel.includes("смен")) {
      setOperationItem(dashboard.productionTasks[0]?.sku ?? itemLabel);
      setOperation("production");
      return;
    }
    if (label === "Контроль") {
      selectPage("control");
      setActionDetail({ title: `Контроль: ${itemLabel}`, description: "Открыта проверка качества данных. Выберите карточку, чтобы перейти к источнику проблемы и исправить её.", page: "control", target: "control-checks" });
      return;
    }
    if (label === "Отгрузка" || label === "Поставка" || label === "Поставщик") {
      selectPage("supply");
      setActionDetail({ title: `Поставки: ${itemLabel}`, description: "Открыт раздел поставок. Здесь можно создать заявку, выбрать поставщика, изменить статус и провести приёмку.", page: "supply", target: "supply-orders" });
      return;
    }
    if (label === "Партия") {
      selectPage("production");
      setActionDetail({ title: `Производство: ${itemLabel}`, description: "Открыт производственный контур. В рабочей смене доступны план, факт, статус и закрытие партии.", page: "production", target: "production-shift" });
      return;
    }
    if (label === "Медиаплан") {
      selectPage("ads");
      const normalizedItem = itemLabel.toLocaleLowerCase("ru-RU");
      const target = normalizedItem.includes("показ") || normalizedItem.includes("клик") || normalizedItem.includes("заказ")
        ? "ads-funnel"
        : normalizedItem.includes("товар") || normalizedItem.includes("sku") || normalizedItem.includes("cremo") || normalizedItem.includes("new")
          ? "ads-signals"
          : "ads-campaigns";
      setActionDetail({ title: `Реклама: ${itemLabel}`, description: "Показатель выбран. Кнопка ниже прокрутит к соответствующим данным в рекламном разделе.", page: "ads", target });
      return;
    }
    if (label === "Открыть сверку FIFO" || itemLabel === "Сверка FIFO") {
      setFifoBusy(true);
      void getFifoReconciliation().then((data) => {
        setFifoData(data);
        setFifoOpen(true);
      }).catch((error) => setToast(error instanceof Error ? error.message : "Не удалось открыть FIFO-сверку")).finally(() => setFifoBusy(false));
      return;
    }
    if (label === "Синхронизировать") {
      setOperationBusy(true);
      void runTenantSync().catch((error) => setToast(error instanceof Error ? error.message : "Не удалось запустить синхронизацию")).finally(() => setOperationBusy(false));
      return;
    }
    if (label === "Экспорт" || label === "Отчет") {
      setOperationBusy(true);
      void downloadReportPdf(itemLabel === "отчеты" ? "Операционный отчёт" : itemLabel, 30).then(() => setToast("PDF собран сервером")).catch(() => setToast("Не удалось собрать PDF")).finally(() => setOperationBusy(false));
      return;
    }
    if (label === "Заявка") {
      setOperationItem(itemLabel);
      setOperation("procurement");
      return;
    }
    if (label === "Агент") {
      setOperationBusy(true);
      void refreshAgentRecommendations().then((result) => {
        setToast(`Рекомендации обновлены: ${result.pending} ждут решения`);
        return dashboard.refresh();
      }).catch((error) => setToast(error instanceof Error ? error.message : "Не удалось обновить рекомендации")).finally(() => setOperationBusy(false));
      return;
    }
    if (label === "Смена") {
      setOperationItem(dashboard.productionTasks[0]?.sku ?? "");
      setOperation("production");
      return;
    }
    const itemKey = `${label}:${itemLabel}`;
    const normalizedAction = label.toLocaleLowerCase("ru-RU");
    if (normalizedAction.includes("синхрон") || normalizedAction.includes("устар")) {
      handleAction("Синхронизировать", itemLabel);
      return;
    }
    if (normalizedAction.includes("заказ") || normalizedAction.includes("поставщ") || normalizedAction.includes("докуп")) {
      setOperationItem(itemLabel);
      setOperation("procurement");
      return;
    }
    if (normalizedAction.includes("производ") || normalizedAction.includes("ускор") || normalizedAction.includes("парт") || normalizedAction.includes("обновить факт") || normalizedAction.includes("просроч")) {
      setOperationItem(itemLabel);
      setOperation("production");
      return;
    }

    const existing = planItems.find((item) => item.source_action_id === itemKey);
    if (existing) {
      setToast(existing.status === "done" ? `${label}: ${itemLabel} уже выполнено` : `${itemLabel} уже в плане`);
      return;
    }

    setToast(`${label}: ${itemLabel} добавлено в план`);
    void createActionPlanItem({
      sourceActionId: itemKey,
      title: label,
      sku: itemLabel,
    }).then((created) => {
      const item = created.item ?? {
        id: created.id,
        title: label,
        sku: itemLabel,
        status: created.status,
        priority: "medium" as const,
        source_action_id: itemKey,
      };
      setPlanItems((current) => [item, ...current.filter((currentItem) => currentItem.id !== item.id)]);
      setToast(item.status === "done" ? `${label}: ${itemLabel} выполнено` : `${label}: ${itemLabel} добавлено в план`);
    }).catch(() => {
      setToast(`${label}: ${itemLabel} не удалось добавить в план`);
    });
  };

  const submitOperation = async (form: HTMLFormElement) => {
    const values = new FormData(form);
    setOperationBusy(true);
    try {
      if (operation === "procurement") {
        const result = await createProcurementOperation({
          supplierName: String(values.get("supplier") ?? ""),
          supplierArticle: String(values.get("supplierArticle") ?? ""),
          productName: String(values.get("product") ?? ""),
          quantity: Number(values.get("quantity") ?? 0),
          unitPrice: Number(values.get("unitPrice") ?? 0),
          status: String(values.get("status") ?? "Запланировано"),
          orderDate: String(values.get("orderDate") ?? ""),
          paymentDueDate: String(values.get("paymentDueDate") ?? ""),
          expectedDate: String(values.get("expectedDate") ?? ""),
          currency: String(values.get("currency") ?? "RUB"),
          exchangeRate: Number(values.get("exchangeRate") ?? 1),
          note: String(values.get("note") ?? ""),
        });
        setToast(`Заявка #${result.id} создана в backend`);
        if (pendingPlanOperationId) {
          const planItem = planItems.find((item) => item.id === pendingPlanOperationId);
          if (planItem) await setPlanStatus(planItem, "done", `Заявка #${result.id} создана.`);
          setPendingPlanOperationId(null);
        }
      } else if (operation === "production") {
        const result = await createProductionShift({
          sku: String(values.get("sku") ?? ""),
          productName: String(values.get("product") ?? ""),
          plannedUnits: Number(values.get("quantity") ?? 0),
          taskDate: String(values.get("taskDate") ?? ""),
        });
        setToast(`Смена сохранена: ${result.saved} задача`);
        if (pendingPlanOperationId) {
          const planItem = planItems.find((item) => item.id === pendingPlanOperationId);
          if (planItem) await setPlanStatus(planItem, "done", `Производственная смена сохранена (${result.saved} задач).`);
          setPendingPlanOperationId(null);
        }
      }
      setOperation(null);
      await dashboard.refresh();
    } catch (error) {
      setToast(error instanceof Error ? error.message : "Операция не выполнена");
    } finally {
      setOperationBusy(false);
    }
  };

  const openCardDetail = (detail: DrawerDetail) => setDrawerDetail(detail);
  const closeCardDetail = () => setDrawerDetail(null);
  const formatMoney = (value: number, unit = "₽"): string => `${Math.round(value).toLocaleString("ru-RU")} ${unit}`;
  const formatCount = (value: number) => Math.round(value).toLocaleString("ru-RU");

  const openProductDetail = (item: ProductEconomicsRow) => {
    const marginPct = Number.isFinite(item.marginPct ?? NaN) ? (item.marginPct ?? 0) : item.revenue > 0 ? (item.margin / item.revenue) * 100 : 0;
    openCardDetail({
      title: item.product || item.article || item.nmId,
      subtitle: item.article || `WB ${item.nmId}`,
      facts: [
        `Артикул: ${item.nmId || "не задан"}`,
        `Количество заказов: ${formatCount(item.orders)}`,
        `Выкупов: ${formatCount(item.sales)}`,
        `Выручка: ${formatMoney(item.revenue)}`,
        `Реклама: ${formatMoney(item.adSpend)}`,
        `Результат по учтённым затратам: ${formatMoney(item.margin)}`,
        `Расчётная маржинальность: ${marginPct.toFixed(1)}% (по доступным расходам WB и оценке себестоимости)`,
        `DРР: ${item.drr.toFixed(1)}%`,
        `Запас: ${formatCount(item.stock)} шт.`,
        item.daysOfStock === null ? "Прогноз запаса: нет данных" : `Прогноз запаса: ${item.daysOfStock.toFixed(1)} дн.`,
      ],
      page: "products",
    });
  };

  const openControlCheckDetail = (row: Record<string, unknown>) => {
    const module = String(row.Модуль ?? "Контроль");
    const name = String(row.Проверка ?? "Проверка");
    const status = String(row.Статус ?? "—");
    const details = String(row.Детали ?? "Не переданы подробности.");
    const action = String(row["Что сделать"] ?? "Перейдите в раздел контроля для шага исправления.");
    const normalized = `${name} ${module}`.toLocaleLowerCase("ru-RU");
    const openPage = normalized.includes("сверка сырья") || normalized.includes("fifo") ? "settings" : normalized.includes("реклам") ? "ads" : normalized.includes("запас") || normalized.includes("склад") ? "stock" : normalized.includes("продаж") || normalized.includes("финанс") ? "finance" : normalized.includes("норм") ? "settings" : normalized.includes("себестоимость") || normalized.includes("стоимость") || normalized.includes("маржа") ? "settings" : "control";
    const target =
      openPage === "settings"
        ? normalized.includes("норм") ? "production-norms" : normalized.includes("себестоимость") || normalized.includes("стоимость") ? "product-costs" : undefined
        : openPage === "control" ? "control-checks" : openPage === "ads" ? "ads-campaigns" : undefined;
    openCardDetail({
      title: name,
      subtitle: module,
      facts: [
        `Статус: ${status}`,
        `Подробнее: ${details}`,
        `Рекомендуемое действие: ${action}`,
      ],
      page: openPage,
      target,
      actionLabel: "Открыть раздел",
    });
  };

  const openStockRowDetail = (row: InventoryWatchRow) => {
    openCardDetail({
      title: `Запас ${row.sku}`,
      subtitle: row.name,
      facts: [
        `Запас: ${row.stock}`,
        `Маржа: ${row.margin}`,
        `Сигнал: ${row.signal}`,
      ],
      page: "stock",
      target: "stock-coverage",
      actionLabel: "Открыть разбор",
    });
  };

  const openReorderDetail = (item: ReorderItem) => {
    openCardDetail({
      title: `Приоритет пополнения`,
      subtitle: item.name,
      facts: [
        `SKU: ${item.sku}`,
        `Дней запас: ${item.days}`,
        `Требуемое количество: ${item.qty}`,
        `Склад: ${item.warehouse}`,
        `Риск: ${item.risk}`,
      ],
      page: "stock",
      target: "stock-reorder-plan",
      actionLabel: "Открыть план пополнения",
    });
  };

  const openCoverageRowDetail = (row: StockCoverageRow) => {
    openCardDetail({
      title: `Запас по SKU`,
      subtitle: row.label,
      facts: [
        `Подробности: ${row.detail}`,
        `Запас: ${row.value}`,
      ],
      page: "stock",
      target: "stock-coverage",
      actionLabel: "Показать запас",
    });
  };

  const openRuleDetail = (row: StockRule) => {
    openCardDetail({
      title: "Правило пополнения",
      subtitle: row.id,
      facts: [
        `Название: ${row.title}`,
        `Описание: ${row.description || "не указано"}`,
      ],
      page: "supply",
      target: "supply-orders",
      actionLabel: "Перейти в поставки",
    });
  };

  const openProductionStageDetail = (stage: ProductionStage) => {
    openCardDetail({
      title: `Этап: ${stage.label}`,
      subtitle: stage.status,
      facts: [stage.value],
      page: "production",
      target: "production-shift",
      actionLabel: "Открыть производство",
    });
  };

  const openProductionTaskDetail = (task: ProductionTask) => {
    openCardDetail({
      title: task.title || `Задача ${task.sku}`,
      subtitle: `SKU: ${task.sku}`,
      facts: [
        `План: ${task.plan}`,
        `Факт: ${task.actual}`,
        `Статус: ${task.status}`,
      ],
      page: "production",
      target: "production-shift",
      actionLabel: "Открыть смену",
    });
  };

  const openMaterialNeedDetail = (item: MaterialNeed) => {
    openCardDetail({
      title: `Материал: ${item.name}`,
      subtitle: item.status,
      facts: [
        `Нужно: ${item.need}`,
        `Запас: ${item.stock}`,
        `Статус: ${item.tone}`,
      ],
      page: "supply",
      target: "supply-orders",
      actionLabel: "Открыть поставки",
    });
  };

  const openSupplyShipmentDetail = (shipment: SupplyShipment) => {
    openCardDetail({
      title: `Поставка ${shipment.id}`,
      subtitle: shipment.supplier,
      facts: [
        `ETA: ${shipment.eta}`,
        `Сумма: ${shipment.amount}`,
        `Статус: ${shipment.status}`,
      ],
      page: "supply",
      target: "supply-orders",
      actionLabel: "Открыть поставку",
    });
  };

  return (
    <div className="app-shell">
      <aside className="side-rail" aria-label="Основная навигация">
        <div className="brand-row">
          <div className="brand-mark">
            <Command size={18} />
          </div>
          <div>
            <p className="brand-title">MARKETSHELPER</p>
            <p className="brand-subtitle">операционный центр</p>
          </div>
        </div>

        <nav className="nav-list">
          {dashboard.navItems.map((item) => (
            <NavButton
              active={activeNav === item.id}
              item={item}
              key={item.id}
              onSelect={() => selectPage(item.id)}
            />
          ))}
        </nav>

        <div className={cx("side-status", dashboard.connection.mode)} role="status">
          <div className="status-dot" />
          <div>
            <p>{dashboard.connection.label}</p>
            <span>{dashboard.connection.detail}</span>
          </div>
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <button
            className="icon-button mobile-only"
            type="button"
            aria-expanded={mobileMenuOpen}
            aria-controls={mobileMenuOpen ? "mobile-navigation-dialog" : undefined}
            aria-haspopup="dialog"
            aria-label="Открыть меню"
            onClick={() => setMobileMenuOpen((open) => !open)}
          >
            <Menu size={20} />
          </button>

          <div className="topbar-title">
            <span>{activeMeta.section}</span>
            <h1>{activeMeta.title}</h1>
          </div>

          <div className="search-shell">
            <label className="search-box">
              <Search size={18} />
              <input
                type="search"
                value={searchQuery}
                placeholder={activeMeta.search}
                onChange={(event) => setSearchQuery(event.target.value)}
                aria-label="Поиск по разделам, товарам и кампаниям"
              />
            </label>
            {normalizedQuery.length > 1 ? (
              <div className="search-results" role="listbox" aria-label="Результаты поиска">
                {searchResults.length ? searchResults.map((result) => (
                  <button
                    key={`${result.kind}-${result.label}`}
                    type="button"
                    role="option"
                    onClick={() => selectSearchResult(result.page)}
                  >
                    <span>{result.kind}</span>
                    <strong>{result.label}</strong>
                    <small>{result.detail}</small>
                  </button>
                )) : <p>Ничего не найдено</p>}
              </div>
            ) : null}
          </div>

          <div className="topbar-actions">
            <ThemeToggle />
            {installPrompt ? <button className="secondary-button install-button" aria-label="Установить приложение" title="Установить приложение" type="button" onClick={() => void installApp()}><Download size={17} /><span>Установить</span></button> : null}
            <button
              className="secondary-button home-button" aria-label="На главную" title="На главную"
              type="button"
              onClick={() => {
                setMobileMenuOpen(false);
                selectPage("today");
              }}
            >
              <House size={17} />
              <span>На главную</span>
            </button>
            <button
              className="plan-counter"
              type="button"
              onClick={() => setPlanOpen(true)}
              aria-label="Открыть план задач" title="Открыть план задач"
            >
              <CheckCircle2 size={16} />
              <span>{activePlanItems.length ? `План ${activePlanItems.length}` : "План задач"}</span>
            </button>
            {session ? (
              <div className="session-shell" ref={sessionShellRef}>
                <button
                  className="session-chip"
                  type="button"
                  title={`Аккаунт: ${session.tenant.name}`} aria-label={`Текущий аккаунт: ${session.tenant.name}`}
                  aria-expanded={sessionMenuOpen}
                  onClick={() => setSessionMenuOpen((open) => !open)}
                >
                  <span className="session-avatar">{session.user.name.slice(0, 1)}</span>
                  <span>{session.user.name}</span>
                  <small>{session.tenant.name}</small>
                  <ChevronDown size={14} />
                </button>
                {sessionMenuOpen ? (
                  <div className="session-menu" role="menu" aria-label="Действия аккаунта">
                    <span className="eyebrow">Аккаунт</span>
                    <button type="button" onClick={goToHomePage}>
                      <strong>На главную</strong>
                      <small>Перейти в сводный обзор</small>
                    </button>
                    {isNativeApp() ? null : <a href={getGatewayUrl("/billing")} role="menuitem"><strong>Подписка и оплата</strong><small>Статус тарифа и история платежей</small></a>}
                    <a href={getGatewayUrl("/account")} role="menuitem"><strong>Аккаунт и удаление</strong><small>Пароль, email, удаление аккаунта</small></a>
                    <a href={getGatewayUrl("/offer")} role="menuitem"><strong>Документы</strong><small>Оферта и политика конфиденциальности</small></a>
                    <a href={getGatewayUrl("/contacts")} role="menuitem"><strong>Поддержка</strong><small>Контакты и помощь по кабинету</small></a>
                    <button className="session-logout" type="button" onClick={handleLogout}>
                      <LogOut size={16} />
                      <strong>Выйти</strong>
                      <small>Завершить сеанс и вернуться к входу</small>
                    </button>
                  </div>
                ) : null}
              </div>
            ) : null}
            <button
              aria-label={dashboard.refreshing ? "Обновляем данные" : "Обновить данные"} title="Обновить данные" className={cx("primary-button", dashboard.refreshing && "is-refreshing")}
              type="button"
              disabled={dashboard.refreshing}
              onClick={() => void handleRefresh()}
            >
              <RefreshCw size={17} />
              <span>{dashboard.refreshing ? "Обновляем" : "Обновить"}</span>
            </button>
          </div>
        </header>

        {dashboard.connection.mode !== "live" ? <DataBanner connection={dashboard.connection} /> : null}
        {dashboard.dataWarning && (activeNav === "finance" || activeNav === "products" || activeNav === "production") ? <p className="data-banner stale" role="status">{dashboard.dataWarning}</p> : null}

        {mobileMenuOpen ? (
          <MobileNavigationDialog items={dashboard.navItems} activeNav={activeNav} onSelect={selectPage} onClose={()=>setMobileMenuOpen(false)} />
        ) : null}

        {activeNav === "today" ? (
          <TodayView
            actions={dashboard.actions}
            chartBars={dashboard.chartBars}
            chartBarsWeek={dashboard.chartBarsWeek}
            inventory={dashboard.inventory}
            onAction={handleAction}
            period={trendPeriod}
            setPeriod={setTrendPeriod}
            target={pendingScrollTarget}
            todayMetrics={dashboard.todayMetrics}
            topAction={topAction}
          />
        ) : null}

        {activeNav === "stock" ? (
          <><LazyPanel label="общий склад"><SharedWarehouse/></LazyPanel><StockView
            inventory={dashboard.inventory}
            onAction={handleAction}
            onOpenStockRow={openStockRowDetail}
            onOpenReorder={openReorderDetail}
            onOpenCoverage={openCoverageRowDetail}
            onOpenRule={openRuleDetail}
            reorderPlan={dashboard.reorderPlan}
            stockCoverage={dashboard.stockCoverage}
            stockHero={dashboard.stockHero}
            stockMetrics={dashboard.stockMetrics}
            stockRules={dashboard.stockRules}
            sellerWarehouses={dashboard.sellerWarehouses}
            target={pendingScrollTarget}
          /></>
        ) : null}
        {activeNav === "ads" ? (
          <AdsView
            adsFunnel={dashboard.adsFunnel}
            adsHero={dashboard.adsHero}
            adsMetrics={dashboard.adsMetrics}
            campaigns={dashboard.campaigns}
            creativeSignals={dashboard.creativeSignals}
            onAction={handleAction}
            period={dashboardPeriod}
            setPeriod={setDashboardPeriod}
            target={pendingScrollTarget}
          />
        ) : null}

        {activeNav === "finance" ? (
          <><FinanceView target={pendingScrollTarget} cashflow={<LazyPanel label="движение денег"><CashflowPanel compact role={session?.user.role ?? "viewer"} /></LazyPanel>}
            selfBuyouts={<LazyPanel label="самовыкупы"><SelfBuyoutsPanel role={session?.user.role ?? "viewer"} period={dashboardPeriod} confirm={askConfirm} products={dashboard.productEconomics.map((p) => ({ nmId: p.nmId, article: p.article, product: p.product }))} /></LazyPanel>}
            cashWindows={dashboard.cashWindows}
            expenseSplit={dashboard.expenseSplit}
            financeLedger={dashboard.financeLedger}
            financeReport={dashboard.financeReport}
            financeMetrics={dashboard.financeMetrics}
            onAction={handleAction}
            period={dashboardPeriod}
            setPeriod={setDashboardPeriod}
          />
          </>
        ) : null}

        {activeNav === "production" ? (
          <ProductionView
            materialNeeds={dashboard.materialNeeds}
            onAction={handleAction}
            onRefresh={dashboard.refresh}
            productionMetrics={dashboard.productionMetrics}
            productionStages={dashboard.productionStages}
            productionTasks={dashboard.productionTasks}
            onOpenProductionStage={openProductionStageDetail}
            onOpenProductionTask={openProductionTaskDetail}
            onOpenMaterialNeed={openMaterialNeedDetail}
          />
        ) : null}

        {activeNav === "supply" ? (
          <SupplyView
            onAction={handleAction}
            onRefresh={dashboard.refresh}
            session={session}
            procurementOrders={dashboard.procurementOrders}
            supplierPulse={dashboard.supplierPulse}
            supplyMetrics={dashboard.supplyMetrics}
            supplyShipments={dashboard.supplyShipments}
            supplyNewOrders={dashboard.supplyNewOrders}
            acceptanceCoefficients={dashboard.acceptanceCoefficients}
            transitTariffs={dashboard.transitTariffs}
            supplyErrors={dashboard.supplyErrors}
            onOpenShipment={openSupplyShipmentDetail}
            target={pendingScrollTarget}
          />
        ) : null}

        {activeNav === "assistant" ? (
          <AssistantView
            agentCandidates={dashboard.agentCandidates}
            agentHistory={dashboard.agentHistory}
            actions={dashboard.actions}
            assistantMetrics={dashboard.assistantMetrics}
            automations={dashboard.automations}
            onAction={handleAction}
            onRefresh={dashboard.refresh}
          />
        ) : null}

        {activeNav === "reports" ? (
          <ReportsView
            canLinkTelegram={session?.mode !== "demo" && session?.user.role === "owner"}
            exportItems={dashboard.exportItems}
            onAction={handleAction}
          onQuickReport={async (name, days, metrics) => { await generateQuickReport(name, days, metrics); setToast(`PDF за ${days} дней поставлен в очередь`); }}
            reportCards={dashboard.reportCards}
            reportMetrics={dashboard.reportMetrics}
          />
        ) : null}

        {activeNav === "products" ? (
          <ProductsView
            period={dashboardPeriod}
            products={dashboard.productEconomics}
            productCards={dashboard.productCards}
            productCardErrors={dashboard.productCardErrors}
            prices={dashboard.productPrices}
            productPriceErrors={dashboard.productPriceErrors}
            customerQuality={dashboard.customerQuality}
            onRefresh={dashboard.refresh}
            setPeriod={setDashboardPeriod}
            onOpenProduct={openProductDetail}
            target={pendingScrollTarget}
          />
        ) : null}

        {activeNav === "control" ? (
          <ControlView
            data={dashboard.dataQuality}
            target={pendingScrollTarget}
            onNavigate={selectPage}
            onOpenFifo={() => handleAction("Открыть сверку FIFO", "")}
            onOpenCheck={(row) => openControlCheckDetail(row)}
          />
        ) : null}
        {activeNav === "settings" ? ((currentShop()?.id??0)!==0 ? <LazyPanel label="настройки магазина"><SelectedShopSettings/></LazyPanel> : <SettingsViewV2 canManage={session?.mode !== "demo" && session?.user.role === "owner"} canWriteOperations={session?.mode !== "demo" && (session?.user.role === "owner" || session?.user.role === "manager")} sectionTarget={pendingScrollTarget} onSynced={dashboard.refresh} migration={<LazyPanel label="перенос данных"><MigrationImport role={session?.user.role ?? "viewer"} onRefresh={dashboard.refresh} /></LazyPanel>} />) : null}

        {toast ? (
          <div className="toast" role="status" aria-live="polite">
            <CheckCircle2 size={17} />
            <span>{toast}</span>
            <button className="toast-close" type="button" aria-label="Закрыть уведомление" onClick={() => setToast(null)}><X size={16} /></button>
          </div>
        ) : null}
        {operation ? <OperationDialog
          busy={operationBusy}
          item={operationItem}
          productName={dashboard.productionTasks.find((task) => task.sku === operationItem)?.title ?? ""}
          kind={operation}
          onClose={() => setOperation(null)}
          onSubmit={(form) => void submitOperation(form)}
        /> : null}
        {fifoOpen && fifoData ? <FifoDialog
          busy={fifoBusy}
          data={fifoData}
          onClose={() => setFifoOpen(false)}
          onApply={async (note) => {
            setFifoBusy(true);
            try {
              const result = await applyFifoReconciliation(note);
              setToast(result.run_id ? `FIFO-сверка №${result.run_id} проведена` : "Безопасных строк для проведения нет");
              if (result.run_id && pendingFifoPlanId) {
                const item = planItems.find((row) => row.id === pendingFifoPlanId);
                if (item) await setPlanStatus(item, "done", `FIFO-сверка №${result.run_id} проведена.`);
                setPendingFifoPlanId(null);
              }
              setFifoData(await getFifoReconciliation());
              await dashboard.refresh();
            } catch (error) {
              setToast(error instanceof Error ? error.message : "FIFO-сверка не выполнена");
            } finally {
              setFifoBusy(false);
            }
          }}
        /> : null}
        {actionDetail ? <ActionDetailDialog detail={actionDetail} onClose={() => setActionDetail(null)} onNavigate={selectPage} /> : null}
        {drawerDetail ? <DetailDrawer detail={drawerDetail} onClose={() => setDrawerDetail(null)} onNavigate={selectPage} /> : null}
      </main>

      {planOpen ? (
        <PlanDrawer
          filter={planFilter}
          items={planItems}
          onClose={() => setPlanOpen(false)}
          onFilterChange={setPlanFilter}
          onOpenFifo={() => handleAction("Открыть сверку FIFO", "")}
          onExecute={executePlanItem}
          canExecute={(item) => {
            const text = `${item.title} ${item.sku}`.toLocaleLowerCase("ru-RU");
            return !(text.includes("копи") || text.includes("резервн")) || (session?.mode !== "demo" && session?.user.role === "owner");
          }}
          busyId={planBusyId}
          onUpdate={(id, patch) => {
            void updateActionPlanItem(id, patch)
              .then((updated) => setPlanItems((items) => items.map((item) => item.id === id ? updated : item)))
              .catch(() => setToast("Не удалось обновить задачу"));
          }}
        />
      ) : null}

      <nav className="bottom-nav" aria-label="Мобильная навигация">
        {dashboard.mobileNav.map((id) => {
          const item = dashboard.navItems.find((navItem) => navItem.id === id)!;
          const Icon = item.id === "today" ? House : iconByKey[item.iconKey];
          return (
            <button
              className={cx(activeNav === item.id && "active")}
              key={item.id}
              type="button"
              onClick={() => selectPage(item.id)}
            >
              <Icon size={20} />
              <span>{item.id === "today" ? "Главная" : item.label}</span>
            </button>
          );
        })}
        {session ? (
          <button
            className="session-bottom-button"
            type="button"
            onClick={handleLogout}
          >
            <LogOut size={20} />
            <span>Выход</span>
          </button>
        ) : null}
      </nav>
    </div>
  );
}

function MobileNavigationDialog({items,activeNav,onSelect,onClose}:{items:NavItem[];activeNav:PageId;onSelect:(page:PageId)=>void;onClose:()=>void}){
  const dialogRef=useDialogFocus(onClose);
  return <div className="mobile-navigation-overlay" onMouseDown={event=>{if(event.target===event.currentTarget)onClose();}}>
    <section className="mobile-menu" id="mobile-navigation-dialog" role="dialog" aria-modal="true" aria-labelledby="mobile-navigation-title" ref={dialogRef}>
      <div className="mobile-menu-heading">
        <h2 id="mobile-navigation-title">Все разделы</h2>
        <button type="button" className="icon-button" aria-label="Закрыть меню" onClick={onClose}><X size={20}/></button>
      </div>
      <nav aria-label="Разделы кабинета">{items.map(item=><NavButton active={activeNav===item.id} item={item} key={item.id} onSelect={()=>onSelect(item.id)}/>)}</nav>
      <MarketplaceSwitch className="mobile-menu-marketplace" />
    </section>
  </div>;
}

function NavButton({
  active,
  item,
  onSelect,
}: {
  active: boolean;
  item: NavItem;
  onSelect: () => void;
}) {
  const Icon = iconByKey[item.iconKey];

  return (
    <button
      className={cx("nav-item", active && "active")}
      onClick={onSelect}
      type="button"
      aria-current={active ? "page" : undefined}
    >
      <Icon size={18} />
      <span>{item.label}</span>
      {item.count ? <strong>{item.count}</strong> : null}
    </button>
  );
}

function PlanDrawer({
  filter,
  items,
  onClose,
  onFilterChange,
  onOpenFifo,
  onExecute,
  canExecute,
  busyId,
  onUpdate,
}: {
  filter: "all" | ActionPlanItem["status"];
  items: ActionPlanItem[];
  onClose: () => void;
  onFilterChange: (filter: "all" | ActionPlanItem["status"]) => void;
  onOpenFifo: () => void;
  onExecute: (item: ActionPlanItem) => Promise<void>;
  canExecute: (item: ActionPlanItem) => boolean;
  busyId: string | null;
  onUpdate: (id: string, patch: Partial<Pick<ActionPlanItem, "status" | "assignee" | "due_at" | "priority" | "result">>) => void;
}) {
  const dialogRef=useDialogFocus(onClose);
  const activeItems = items.filter((item) => item.status !== "done" && item.status !== "cancelled");
  const visibleItems = filter === "all" ? activeItems : items.filter((item) => item.status === filter);
  const statusLabels: Record<"all" | ActionPlanItem["status"], string> = {
    all: "Все",
    created: "Новые",
    in_progress: "В работе",
    done: "Готово",
    blocked: "Нужны данные",
    cancelled: "Отменены",
  };

  return (
    <>
      <button className="drawer-backdrop" type="button" aria-label="Закрыть план задач" onClick={onClose} />
      <aside className="plan-drawer" role="dialog" aria-modal="true" ref={dialogRef} aria-labelledby="task-plan-title">
        <div className="drawer-header">
          <div>
            <span className="eyebrow">Операционный контур</span>
            <h2 id="task-plan-title">План задач</h2>
            <p>{activeItems.length ? `${activeItems.length} задач в очереди` : "Очередь пока пуста"}</p>
          </div>
          <button className="secondary-button drawer-close-button" type="button" aria-label="Закрыть план задач" onClick={onClose}>
            <X size={18} />
            <span>Закрыть</span>
          </button>
        </div>
        <div className="plan-filters" role="tablist" aria-label="Фильтр задач">
          {(Object.keys(statusLabels) as Array<"all" | ActionPlanItem["status"]>).map((status) => (
            <button
              className={cx(filter === status && "selected")}
              key={status}
              type="button"
              role="tab"
              aria-selected={filter === status}
              onClick={() => onFilterChange(status)}
            >
              {statusLabels[status]}
            </button>
          ))}
        </div>
        <div className="plan-list">
          {visibleItems.length ? visibleItems.map((item) => (
            <article className="plan-item" key={item.id}>
              <div className="plan-item-copy">
                <span>{item.sku}</span>
                <strong>{item.title}</strong>
                <small>{item.executor ?? item.assignee ?? "Не назначен"} · {item.priority === "critical" ? "Критичный" : item.priority === "high" ? "Высокий" : item.priority === "low" ? "Низкий" : "Средний"}</small>
              </div>
              <StatusPill tone={item.status === "done" ? "good" : item.status === "cancelled" || item.status === "blocked" ? "danger" : item.status === "in_progress" ? "info" : "warn"}>
                {statusLabels[item.status]}
              </StatusPill>
              <div className="plan-item-actions">
                {item.status === "created" || item.status === "in_progress" ? (
                  <button className="secondary-button" type="button" disabled={busyId === item.id || !canExecute(item)} title={!canExecute(item) ? "Резервные копии доступны владельцу кабинета" : undefined} onClick={() => void onExecute(item)}>
                    {busyId === item.id ? "Выполняем…" : item.status === "in_progress" ? "Продолжить" : "В работу"}
                  </button>
                ) : null}
                {item.status === "blocked" ? (
                  <button className="secondary-button" type="button" disabled={busyId === item.id || !canExecute(item)} title={!canExecute(item) ? "Резервные копии доступны владельцу кабинета" : undefined} onClick={() => void onExecute(item)}>
                    {busyId === item.id ? "Выполняем…" : "Запустить снова"}
                  </button>
                ) : null}
                {item.status === "in_progress" ? (
                  <button className="primary-button" type="button" disabled={busyId === item.id} onClick={() => void askText("Что выполнено? Укажите результат ручного выполнения задачи:").then((result) => { if (result?.trim()) void onUpdate(item.id, { status: "done", result: `Выполнено вручную: ${result.trim()}` }); })}>
                    <CheckCircle2 size={16} />Подтвердить вручную
                  </button>
                ) : null}
                {item.status !== "done" && item.status !== "cancelled" ? (
                  <button className="ghost-button" type="button" disabled={busyId === item.id} onClick={() => void askConfirm("Отменить эту задачу? Её история сохранится в фильтре «Отменены».").then((confirmed) => { if (confirmed) void onUpdate(item.id, { status: "cancelled" }); })}>
                    Отменить
                  </button>
                ) : null}
                {item.title.toLocaleLowerCase("ru-RU").includes("fifo") && item.status !== "cancelled" ? <button className="secondary-button" type="button" onClick={onOpenFifo}>Открыть FIFO</button> : null}
              </div>
              <details className="plan-details">
                <summary>Детали</summary>
                <div className="plan-detail-grid">
                  <label>Исполнитель<select value={item.assignee ?? ""} onChange={(event) => onUpdate(item.id, { assignee: event.target.value })}><option value="">AI-исполнитель</option><option value="Мурад">Мурад</option><option value="Команда закупок">Команда закупок</option><option value="Маркетинг">Маркетинг</option></select></label>
                  <label>Приоритет<select value={item.priority ?? "medium"} onChange={(event) => onUpdate(item.id, { priority: event.target.value as ActionPlanItem["priority"] })}><option value="low">Низкий</option><option value="medium">Средний</option><option value="high">Высокий</option><option value="critical">Критичный</option></select></label>
                  <label>Срок<input type="date" value={item.due_at?.slice(0, 10) ?? ""} onChange={(event) => onUpdate(item.id, { due_at: event.target.value || null })} /></label>
                </div>
                {item.result ? <p className="plan-result"><strong>Результат:</strong> {item.result}</p> : null}
                {item.history?.length ? <div className="plan-history"><span>История</span>{item.history.slice(-3).reverse().map((entry, index) => <small key={`${entry.at}-${index}`}>{statusLabels[entry.status]} · {new Date(entry.at).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</small>)}</div> : null}
              </details>
            </article>
          )) : <p className="plan-empty">В этом фильтре задач нет.</p>}
        </div>
      </aside>
    </>
  );
}

function DataBanner({ connection }: { connection: DashboardViewModel["connection"] }) {
  return (
    <div className={cx("data-banner", "connection-banner", connection.mode)}>
      <AlertTriangle size={16} />
      <span>{connection.label}</span>
      <p>{connection.detail}</p>
    </div>
  );
}

function ActionDetailDialog({
  detail,
  onClose,
  onNavigate,
}: {
  detail: { title: string; description: string; page: PageId; target?: string };
  onClose: () => void;
  onNavigate: (page: PageId, target?: string) => void;
}) {
  const dialogRef=useDialogFocus(onClose);
  return (
    <div className="drawer-backdrop operation-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="plan-drawer operation-dialog action-detail-dialog" role="dialog" aria-modal="true" ref={dialogRef} aria-labelledby="action-detail-title">
        <div className="drawer-header">
          <div><span className="eyebrow">детализация</span><h2 id="action-detail-title">{detail.title}</h2><p>{detail.description}</p></div>
          <button className="icon-button" type="button" aria-label="Закрыть" onClick={onClose}><X size={18} /></button>
        </div>
        <div className="operation-actions"><button className="secondary-button" type="button" onClick={() => { onClose(); onNavigate(detail.page, detail.target); }}>{detail.target ? "Показать данные" : "Перейти к разделу"}</button><button className="ghost-button" type="button" onClick={onClose}>Закрыть</button></div>
      </section>
    </div>
  );
}

export function TodayView({
  actions,
  chartBars,
  chartBarsWeek,
  inventory,
  onAction,
  period,
  setPeriod,
  target,
  todayMetrics,
  topAction,
}: {
  actions: ActionRecommendation[];
  chartBars: number[];
  chartBarsWeek: number[];
  inventory: InventoryWatchRow[];
  onAction: (label: string, itemLabel: string) => void;
  period: "day" | "week";
  setPeriod: (value: "day" | "week") => void;
  target?: string | null;
  todayMetrics: DashboardMetric[];
  topAction?: ActionRecommendation;
}) {
  const hasTopAction = Boolean(topAction);
  const [showAllActions, setShowAllActions] = useState(false);
  const remainingActions = secondaryActions(actions, topAction);
  const heroInQueue = remainingActions.length !== actions.length;
  const heroTitle = topAction?.owner ?? "Активных сигналов нет";
  const heroDescription = topAction
    ? `${topAction.issue}${topAction.impact ? `. Потенциальный эффект: ${topAction.impact}.` : ""}`
    : "API не передал рекомендации для текущего периода.";

  const overview = (
    <>
      <MetricGrid metrics={todayMetrics} onMetricClick={(metric) => onAction(metric.label, metric.label)} />
      <section className="hero-strip" aria-label="Главный сигнал">
        <div>
          <div className="eyebrow">
            <ShieldCheck size={16} />
            оперативный сигнал
          </div>
          <h2>{heroTitle}</h2>
          <p>{heroDescription}</p>
        </div>
        {hasTopAction && topAction && (
          <div className="hero-actions">
            <button className="secondary-button" type="button" onClick={() => onAction(topAction.title === "Сверка FIFO" ? "Открыть сверку FIFO" : topAction.title, topAction.sku)}>
              <Plus size={17} />
              <span>{topAction.title === "Сверка FIFO" ? "Открыть FIFO" : "Открыть действие"}</span>
            </button>
            <button className="ghost-button" type="button" onClick={() => onAction("Контроль", topAction.sku)}>
              <CalendarDays size={17} />
              <span>Контроль</span>
            </button>
          </div>
        )}
      </section>

      <div className="content-grid">
        <section className="work-section" id="stock-reorder-plan">
          <SectionHeader kicker="Очередь решений" title="Что требует действия">
            <span className="section-tag">
              <Filter size={16} />
              {actions.length} в очереди
            </span>
          </SectionHeader>

          {heroInQueue && !showAllActions ? <p className="form-hint">Главное действие показано выше. Остальные — в очереди ниже.</p> : null}
          <div className="action-list">
            {(showAllActions ? actions : remainingActions.slice(0, 3)).map((item) => (
              <ActionRow item={item} key={item.id} onAction={onAction} />
            ))}
          </div>
          {(remainingActions.length > 3 || heroInQueue) ? <button className="action-more-button" type="button" onClick={() => setShowAllActions((value) => !value)}>
            {showAllActions ? "Свернуть очередь" : `Показать всю очередь · ${actions.length}`}
          </button> : null}
        </section>

        <aside className="insight-column">
          <RevenuePanel chartBars={chartBars} chartBarsWeek={chartBarsWeek} period={period} setPeriod={setPeriod} />
          <button className="ghost-button" type="button" onClick={() => onAction("Партия", "следующая партия")}>Открыть производство</button>
        </aside>
      </div>

    </>
  );
  return (
    <div className="workspace-view">
      <WorkspaceTabs
        ariaLabel="Разделы страницы Сегодня"
        storageKey="mh.tab.today"
        tabs={TODAY_TABS}
        target={target}
        targetMap={TODAY_TARGETS}
        keepMounted={["incidents"]}
        panels={{
          overview,
          stock: <InventoryTable inventory={inventory} onOpen={(row) => onAction("Контроль", row.sku)} />,
          incidents: <StockIncidentsPanelV2 />,
        }}
      />
    </div>
  );
}

const TODAY_TABS = [{ id: "overview", label: "Сегодня" }, { id: "stock", label: "Остатки и маржа" }, { id: "incidents", label: "Инциденты" }] as const;
const TODAY_TARGETS: Record<string, string> = { "stock-reorder-plan": "overview", "inventory-watch": "stock", "wb-incidents": "incidents" };


function StockIncidentsPanel() {
  const [data, setData] = useState<IncidentOperations | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const refresh = async () => { try { setData(await getIncidentOperations()); } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось загрузить инциденты"); } };
  useEffect(() => { void refresh(); }, []);
  const submitCase = async (form: HTMLFormElement) => { const values = new FormData(form); setBusy(true); setError(null); try { await createIncidentCase({ incident_name: String(values.get("incident_name")), incident_date: String(values.get("incident_date")), warehouse_label: String(values.get("warehouse_label")), document_ref: String(values.get("document_ref")), note: String(values.get("note")) }); form.reset(); await refresh(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось создать инцидент"); } finally { setBusy(false); } };
  const submitCompensation = async (form: HTMLFormElement) => { const values = new FormData(form); setBusy(true); setError(null); try { await createIncidentCompensation(Number(values.get("case_id")), { amount_rub: Number(values.get("amount_rub")), compensation_date: String(values.get("compensation_date")), source_ref: String(values.get("source_ref")), note: String(values.get("note")) }); form.reset(); await refresh(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось записать компенсацию"); } finally { setBusy(false); } };
  const submitLoss = async (form: HTMLFormElement) => { const values = new FormData(form); setBusy(true); setError(null); try { await applyIncidentLoss(Number(values.get("case_id")), { nm_id: Number(values.get("nm_id")), units: Number(values.get("units")), note: String(values.get("note")), confirmed: values.get("confirmed") === "on" }); form.reset(); await refresh(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Проведение утраты заблокировано"); } finally { setBusy(false); } };
  const summary = data?.summary;
  const today = new Date().toISOString().slice(0, 10);
  return <section className="work-section"><SectionHeader kicker="WB" title="Инциденты и компенсации"><AlertTriangle size={18} /></SectionHeader>{error ? <p className="operation-error">{error}</p> : null}<div className="metrics-grid incident-metrics"><MetricCard metric={{label:"Подтверждённая утрата",value:`${summary?.confirmed_loss_units ?? 0} ед.`,delta:"FIFO-слои",detail:"учтённые строки",tone:"warn"}}/><MetricCard metric={{label:"Компенсации",value:`${Math.round(summary?.compensation_rub ?? 0).toLocaleString("ru-RU")} ₽`,delta:"WB",detail:"зачисленные суммы",tone:"good"}}/><MetricCard metric={{label:"Непокрытая утрата",value:`${Math.round(summary?.unrecovered_loss_rub ?? 0).toLocaleString("ru-RU")} ₽`,delta:"контроль",detail:"после компенсаций",tone:"danger"}}/></div><details className="cabinet-disclosure"><summary>Создать инцидент или записать компенсацию</summary><div className="content-grid"><form className="settings-form" onSubmit={(event)=>{event.preventDefault();void submitCase(event.currentTarget);}}><strong>Новый инцидент</strong><input name="incident_name" required placeholder="Название инцидента"/><input name="incident_date" required type="date" defaultValue={today}/><input name="warehouse_label" placeholder="Склад WB"/><input name="document_ref" placeholder="Документ / основание"/><input name="note" placeholder="Примечание"/><button className="secondary-button" disabled={busy} type="submit">Создать карточку</button></form><form className="settings-form" onSubmit={(event)=>{event.preventDefault();void submitCompensation(event.currentTarget);}}><strong>Компенсация WB</strong><select className="filter-select" name="case_id" required>{data?.cases.map((item)=><option key={String(item.id)} value={String(item.id)}>#{String(item.id)} · {String(item.incident_name)}</option>)}</select><input name="amount_rub" min="0.01" required step="0.01" type="number" placeholder="Сумма, ₽"/><input name="compensation_date" required type="date" defaultValue={today}/><input name="source_ref" required placeholder="Документ компенсации"/><input name="note" placeholder="Примечание"/><button className="secondary-button" disabled={busy || !data?.cases.length} type="submit">Записать компенсацию</button></form></div></details>{data?.cases.length ? <div className="product-economics-table"><div className="control-head"><span>Дата</span><span>Инцидент</span><span>Склад</span><span>Статус</span><span>Результат</span></div>{data.cases.map((item) => <div className="control-row" key={String(item.id)}><span>{String(item.incident_date ?? "—")}</span><strong>{String(item.incident_name ?? "")}</strong><span>{String(item.warehouse_label ?? "—")}</span><StatusPill tone={String(item.status) === "closed" ? "good" : "warn"}>{String(item.status ?? "open")}</StatusPill><span>{Math.round(Number(item.incident_result_rub ?? 0)).toLocaleString("ru-RU")} ₽</span></div>)}</div> : <p className="plan-empty">Карточек инцидентов WB пока нет</p>}</section>;
}

function StockIncidentsPanelV2() {
  const [data, setData] = useState<IncidentOperations | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const refresh = async () => { try { setData(await getIncidentOperations()); } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось загрузить инциденты"); } };
  useEffect(() => { void refresh(); }, []);
  const submit = async (form: HTMLFormElement, action: () => Promise<unknown>) => { setBusy(true); setMessage(null); try { await action(); form.reset(); await refresh(); setMessage("Операция сохранена"); } catch (error) { setMessage(error instanceof Error ? error.message : "Операция отклонена"); } finally { setBusy(false); } };
  const cases = data?.cases ?? [];
  const today = new Date().toISOString().slice(0, 10);
  const caseOptions = cases.map((item) => <option key={String(item.id)} value={String(item.id)}>#{String(item.id)} · {String(item.incident_name)}</option>);
  return <section className="work-section" id="wb-incidents">
    <SectionHeader kicker="WB" title="Инциденты и компенсации"><AlertTriangle size={18} /></SectionHeader>
    {message ? <p className="operation-error" role="status">{message}</p> : null}
    <details className="cabinet-disclosure"><summary>Создать инцидент или записать компенсацию</summary><div className="incident-forms">
      <form className="incident-form" onSubmit={(event) => { event.preventDefault(); const values = new FormData(event.currentTarget); void submit(event.currentTarget, () => createIncidentCase({ incident_name: String(values.get("name")), incident_date: String(values.get("date")), warehouse_label: String(values.get("warehouse")), document_ref: String(values.get("document")), note: String(values.get("note")) })); }}>
        <h3>Новый инцидент</h3>
        <label><span>Название</span><input name="name" required placeholder="Например: недостача на складе" /></label>
        <label><span>Дата</span><input name="date" required type="date" defaultValue={today} /></label>
        <label><span>Склад WB</span><input name="warehouse" placeholder="Необязательно" /></label>
        <label><span>Документ / основание</span><input name="document" placeholder="Необязательно" /></label>
        <label><span>Примечание</span><input name="note" placeholder="Необязательно" /></label>
        <button className="secondary-button" disabled={busy} type="submit"><Plus size={16} />Создать карточку</button>
      </form>
      {cases.length ? <>
        <form className="incident-form" onSubmit={(event) => { event.preventDefault(); const values = new FormData(event.currentTarget); void submit(event.currentTarget, () => createIncidentCompensation(Number(values.get("case")), { amount_rub: Number(values.get("amount")), compensation_date: String(values.get("date")), source_ref: String(values.get("document")), note: String(values.get("note")) })); }}>
          <h3>Компенсация WB</h3>
          <label><span>Инцидент</span><select className="filter-select" name="case" required>{caseOptions}</select></label>
          <label><span>Сумма, ₽</span><input name="amount" min="0.01" required step="0.01" type="number" /></label>
          <label><span>Дата</span><input name="date" required type="date" defaultValue={today} /></label>
          <label><span>Документ компенсации</span><input name="document" required /></label>
          <label><span>Примечание</span><input name="note" placeholder="Необязательно" /></label>
          <button className="secondary-button" disabled={busy} type="submit"><WalletCards size={16} />Записать компенсацию</button>
        </form>
        <form className="incident-form" onSubmit={(event) => { event.preventDefault(); const values = new FormData(event.currentTarget); void submit(event.currentTarget, () => applyIncidentLoss(Number(values.get("case")), { nm_id: Number(values.get("nmId")), units: Number(values.get("units")), note: String(values.get("note")), confirmed: values.get("confirmed") === "on" })); }}>
          <h3>Подтверждённая утрата</h3>
          <label><span>Инцидент</span><select className="filter-select" name="case" required>{caseOptions}</select></label>
          <label><span>Артикул WB (nmID)</span><input name="nmId" min="1" required type="number" /></label>
          <label><span>Количество</span><input name="units" min="1" required type="number" /></label>
          <label><span>Примечание</span><input name="note" placeholder="Необязательно" /></label>
          <label className="checkbox-field"><input name="confirmed" required type="checkbox" /><span>Подтверждаю списание после проверки FIFO</span></label>
          <button className="primary-button" disabled={busy} type="submit"><ShieldCheck size={16} /><span>Провести утрату</span></button>
        </form>
      </> : <p className="incident-empty">Сначала создайте карточку инцидента. После этого здесь станут доступны компенсация WB и подтверждённая утрата.</p>}
    </div></details>
    {cases.length ? <div className="incident-history-list">{cases.map((item) => <article className="incident-history-card" key={String(item.id)}><div className="incident-history-heading"><div><span>{String(item.incident_date ?? "—")}</span><strong>{String(item.incident_name ?? "Без названия")}</strong></div><StatusPill tone={String(item.status) === "closed" ? "good" : "warn"}>{String(item.status ?? "open")}</StatusPill></div><div className="incident-history-meta"><span>Склад WB<strong>{String(item.warehouse_label ?? "не указан")}</strong></span><span>Результат<strong>{Math.round(Number(item.incident_result_rub ?? 0)).toLocaleString("ru-RU")} ₽</strong></span></div></article>)}</div> : null}
  </section>;
}

function DetailDrawer({
  detail,
  onClose,
  onNavigate,
}: {
  detail: DrawerDetail;
  onClose: () => void;
  onNavigate: (page: PageId, target?: string) => void;
}) {
  const dialogRef=useDialogFocus(onClose);
  return (
    <div className="drawer-backdrop detail-drawer-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="plan-drawer detail-drawer operation-dialog" role="dialog" aria-modal="true" ref={dialogRef} aria-labelledby="detail-drawer-title">
        <div className="drawer-header">
          <div>
            <span className="eyebrow">детали</span>
            <h2 id="detail-drawer-title">{detail.title}</h2>
            <p>{detail.subtitle}</p>
          </div>
          <button className="icon-button" type="button" aria-label="Закрыть" onClick={onClose}><X size={18} /></button>
        </div>
        <div className="detail-facts">
          {detail.facts.map((fact) => <p key={fact}>{fact}</p>)}
        </div>
        <div className="operation-actions">
          <button
            className="secondary-button"
            type="button"
            onClick={() => {
              onClose();
              onNavigate(detail.page ?? "today", detail.target);
            }}
          >
            {detail.actionLabel || "Открыть раздел"}
          </button>
          <button className="ghost-button" type="button" onClick={onClose}>Закрыть</button>
        </div>
      </section>
    </div>
  );
}

function FifoDialog({
  busy,
  data,
  onApply,
  onClose,
}: {
  busy: boolean;
  data: FifoReconciliationData;
  onApply: (note: string) => Promise<void>;
  onClose: () => void;
}) {
  const dialogRef=useDialogFocus(()=>{if(!busy)onClose();});
  const [confirmed, setConfirmed] = useState(false);
  const [note, setNote] = useState("");
  const guardReason = String(data.guard.reason ?? "Проверка статуса FIFO");
  const allowed = Boolean(data.guard.manual_reconciliation_allowed);
  const safeUnits = Number(data.guard.safe_reconciliation_units ?? 0);
  const blockedExplanation = safeUnits <= 0
    ? "Безопасных локальных строк нет: все расхождения относятся к WB-контуру. Их нельзя проводить этой кнопкой."
    : "Локальная сверка ожидает повторной проверки данных перед проведением.";

  return (
    <div className="drawer-backdrop operation-backdrop" role="presentation">
      <section className="plan-drawer operation-dialog fifo-dialog" role="dialog" aria-modal="true" ref={dialogRef} aria-labelledby="fifo-dialog-title">
        <div className="drawer-header">
          <div><span className="eyebrow">FIFO</span><h2 id="fifo-dialog-title">Безопасная сверка</h2><p>{guardReason}</p></div>
          <button className="icon-button" type="button" aria-label="Закрыть FIFO-сверку" onClick={onClose}><X size={18} /></button>
        </div>
        <div className="plan-detail-grid">
          <Stat label="Диагностических строк" value={String(data.preview.length)} />
          <Stat label="Безопасно локально" value={`${safeUnits} ед.`} />
          <Stat label="Доступно на WB" value={`${Number(data.wb_context.available_units ?? 0)} ед.`} />
          <Stat label="Товар в пути WB" value={`${Number(data.wb_context.wb_transit_units ?? 0)} ед.`} />
        </div>
        <p className="plan-empty">WB-строки не попадут в проведение: этот экран выполняет только разрешённую локальную сверку.</p>
        <div className="fifo-preview-list">
          {data.preview.slice(0, 12).map((row, index) => (
            <div className="fifo-preview-row" key={`${String(row.nm_id ?? "row")}-${index}`}>
              <strong>{String(row.product_name ?? row.supplier_article ?? "Товар")}</strong>
              <span>{String(row.location_name ?? row.location ?? "локально")}</span>
              <em>{String(row.difference_units ?? 0)} ед.</em>
              <StatusPill tone={Number(row.safe_to_reconcile ?? 0) === 1 ? "good" : "warn"}>{Number(row.safe_to_reconcile ?? 0) === 1 ? "локально" : "WB заблокировано"}</StatusPill>
            </div>
          ))}
        </div>
        {allowed ? <>
          <label className="checkbox-field"><input checked={confirmed} disabled={busy} type="checkbox" onChange={(event) => setConfirmed(event.target.checked)} /><span>Подтверждаю безопасные локальные строки сверки</span></label>
          <label className="operation-field"><span>Примечание</span><input disabled={busy} placeholder="Причина локальной сверки" value={note} onChange={(event) => setNote(event.target.value)} /></label>
          <button className="primary-button" disabled={!confirmed || busy} type="button" onClick={() => void onApply(note)}><ShieldCheck size={17} /><span>{busy ? "Проводим" : "Провести локальную сверку"}</span></button>
        </> : <div className="fifo-blocked" role="status"><AlertTriangle size={18} /><div><strong>Проведение сейчас недоступно</strong><p>{blockedExplanation} {guardReason}</p></div></div>}
      </section>
    </div>
  );
}

function ReportCardRow({
  onAction,
  report,
}: {
  onAction: (label: string, itemLabel: string) => void;
  report: ReportCard;
}) {
  return (
    <article className={cx("report-row", report.tone)}>
      <div>
        <span>{report.period}</span>
        <h3>{report.title}</h3>
        <p>{report.detail}</p>
      </div>
      <StatusPill tone={report.tone}>{report.status}</StatusPill>
      <button className="secondary-button" type="button" onClick={() => onAction("Отчет", report.title)}>
        Открыть
      </button>
    </article>
  );
}

function ActionRow({
  item,
  onAction,
}: {
  item: ActionRecommendation;
  onAction: (label: string, itemLabel: string) => void;
}) {
  const Icon = iconByKey[item.iconKey];
  const actionLabel = item.title === "Сверка FIFO" ? "Открыть сверку FIFO" : item.title;

  return (
    <article className={cx("action-row", item.tone)}>
      <div className="action-icon">
        <Icon size={20} />
      </div>
      <div className="action-copy">
        <div>
          <span>{item.sku}</span>
          <strong>{item.title}</strong>
        </div>
        <p>{item.issue}</p>
        <div className="action-meta">
          {item.impact ? <span>{item.impact}</span> : null}
          <span>{item.due}</span>
        </div>
      </div>
      <div className="action-controls">
        <button className="secondary-button" title={actionLabel} type="button" onClick={() => onAction(actionLabel, item.sku)}>
          {item.title === "Сверка FIFO" ? "Открыть FIFO" : "Открыть действие"}
        </button>
        <button className="icon-button" type="button" aria-label="Открыть карточку" onClick={() => onAction(actionLabel, item.sku)}>
          <ChevronRight size={18} />
        </button>
      </div>
    </article>
  );
}

function RevenuePanel({
  chartBars,
  chartBarsWeek,
  period,
  setPeriod,
}: {
  chartBars: number[];
  chartBarsWeek: number[];
  period: "day" | "week";
  setPeriod: (value: "day" | "week") => void;
}) {
  const values = period === "day" ? chartBars : (chartBarsWeek.length ? chartBarsWeek : chartBars);
  const last = values.at(-1) ?? 0;
  const previous = values.at(-2) ?? 0;
  const total = values.reduce((sum, value) => sum + value, 0);
  const change = previous > 0 ? ((last - previous) / previous) * 100 : null;
  return (
    <section className="panel">
      <SectionHeader compact kicker="Деньги" title="Темп выручки">
        <div className="segmented" aria-label="Период">
          <button className={period === "day" ? "selected" : ""} type="button" onClick={() => setPeriod("day")}>
            День
          </button>
          <button className={period === "week" ? "selected" : ""} type="button" onClick={() => setPeriod("week")}>
            Неделя
          </button>
        </div>
      </SectionHeader>
      {values.length ? <MiniChart values={values} /> : <div className="plan-empty">Нет данных для графика</div>}
      {values.length ? <div className="trend-numbers"><span>{period === "day" ? "Последний час" : "Последний день"}<strong>{Math.round(last).toLocaleString("ru-RU")} ₽</strong></span><span>К предыдущему<strong className={change === null || change >= 0 ? "positive" : "negative"}>{change === null ? "—" : `${change >= 0 ? "+" : ""}${change.toFixed(1)}%`}</strong></span><span>{period === "day" ? "За день" : "За 7 дней"}<strong>{Math.round(total).toLocaleString("ru-RU")} ₽</strong></span></div> : null}
      <div className="chart-footer">
        <span>{values.length ? "Данные периода" : "API не передал динамику"}</span>
        <strong>{values.length ? (period === "day" ? "по часам" : "по дням") : "Нет данных"}</strong>
      </div>
    </section>
  );
}

function ProductionPanel({ hasData, onOpen }: { hasData: boolean; onOpen?: () => void }) {
  return (
    <section className="panel">
      <SectionHeader compact kicker="Производство" title="Следующая партия">
        {onOpen ? <button className="icon-button" type="button" aria-label="Открыть следующую партию" onClick={onOpen}><Factory size={20} /></button> : <Factory size={20} />}
      </SectionHeader>
      {hasData ? <div className="timeline" /> : <div className="plan-empty">Нет данных по производству</div>}
    </section>
  );
}

function MiniChart({ values }: { values: number[] }) {
  const max = Math.max(...values, 1);
  const formatChartValue = (value: number) => {
    const absolute = Math.abs(value);
    if (absolute >= 1_000_000) return `${(value / 1_000_000).toFixed(1).replace(".0", "")}м`;
    if (absolute >= 1_000) return `${Math.round(value / 1_000)}к`;
    return `${Math.round(value)}`;
  };

  return (
    <div className="mini-chart" aria-label="График темпа выручки">
      {values.map((value, index) => (
        <div className="mini-chart-bar" key={`${value}-${index}`}>
          <small title={`${Math.round(value).toLocaleString("ru-RU")} ₽`}>{formatChartValue(value)}</small>
          <span
            className={index > values.length - 4 ? "hot" : ""}
            style={{ height: `${Math.max(18, (value / max) * 100)}%` }}
          />
        </div>
      ))}
    </div>
  );
}

function Signal({
  active = false,
  done = false,
  label,
  value,
}: {
  active?: boolean;
  done?: boolean;
  label: string;
  value: string;
}) {
  return (
    <div className={cx("signal", done && "done", active && "active")}>
      <span />
      <div>
        <strong>{label}</strong>
        <p>{value}</p>
      </div>
    </div>
  );
}

function OperationDialog({
  busy,
  item,
  kind,
  onClose,
  onSubmit,
  productName,
}: {
  busy: boolean;
  item: string;
  kind: "procurement" | "production";
  onClose: () => void;
  onSubmit: (form: HTMLFormElement) => void;
  productName: string;
}) {
  const dialogRef=useDialogFocus(()=>{if(!busy)onClose();});
  const title = kind === "procurement" ? "Создать заявку" : "Собрать смену";
  const [norms, setNorms] = useState<ProductionNorm[]>([]);
  const [productionSku, setProductionSku] = useState(item);
  const [productionName, setProductionName] = useState(productName);
  const [productionPlan, setProductionPlan] = useState("1");

  useEffect(() => {
    if (kind !== "production") return;
    void getProductionNorms().then((result) => {
      const enabled = result.items.filter((norm) => Boolean(norm.enabled));
      setNorms(enabled);
      const selected = enabled.find((norm) => norm.supplier_article === item);
      if (selected) {
        setProductionName(selected.product_name || productName);
        setProductionPlan(String(Math.max(1, selected.min_batch || 1)));
      }
    }).catch(() => setNorms([]));
  }, [item, kind, productName]);

  const selectProductionSku = (value: string) => {
    setProductionSku(value);
    const selected = norms.find((norm) => norm.supplier_article === value);
    if (selected) {
      setProductionName(selected.product_name || value);
      setProductionPlan(String(Math.max(1, selected.min_batch || 1)));
    }
  };
  return (
    <div className="drawer-backdrop operation-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="plan-drawer operation-dialog" role="dialog" aria-modal="true" ref={dialogRef} aria-labelledby="operation-title">
        <div className="drawer-header">
          <div><span className="eyebrow">Операция кабинета</span><h2 id="operation-title">{title}</h2><p>Данные будут записаны в рабочую базу кабинета.</p></div>
          <button className="icon-button" type="button" aria-label="Закрыть" onClick={onClose}><X size={18} /></button>
        </div>
        <form className="operation-form" onSubmit={(event) => { event.preventDefault(); onSubmit(event.currentTarget); }}>
          {kind === "procurement" ? <>
            <label>Поставщик<input name="supplier" required placeholder="Название поставщика" /></label>
            <label>Артикул поставщика<input name="supplierArticle" required placeholder="Например, TSH-001" /></label>
            <label>Товар<input name="product" required placeholder="Название товара" /></label>
            <label>Количество<input name="quantity" required min="1" type="number" defaultValue="1" /></label>
            <label>Цена за единицу<input name="unitPrice" min="0" step="0.01" type="number" defaultValue="0" /></label>
            <label>Статус<select className="filter-select" defaultValue="Запланировано" name="status"><option>Запланировано</option><option>Заказано</option></select></label>
            <label>Дата заявки<input name="orderDate" required type="date" defaultValue={new Date().toISOString().slice(0, 10)} /></label>
            <label>Оплатить до<input name="paymentDueDate" type="date" /></label>
            <label>Ожидаемая поставка<input name="expectedDate" type="date" /></label>
            <label>Валюта<select className="filter-select" defaultValue="RUB" name="currency"><option>RUB</option><option>CNY</option><option>USD</option><option>EUR</option></select></label>
            <label>Курс к рублю<input name="exchangeRate" min="0.0001" step="0.0001" type="number" defaultValue="1" /></label>
            <label>Комментарий<input name="note" placeholder="Условия, контакт или примечание" /></label>
          </> : null}
          {kind === "production" ? <>
            <label>Артикул<input autoComplete="off" list="production-sku-options" name="sku" required value={productionSku} onChange={(event) => selectProductionSku(event.target.value)} placeholder="Выберите настроенный артикул" /><datalist id="production-sku-options">{norms.map((norm) => <option key={norm.nm_id} value={norm.supplier_article}>{norm.product_name}</option>)}</datalist></label>
            <label>Товар<input name="product" required value={productionName} onChange={(event) => setProductionName(event.target.value)} placeholder="Название товара" /></label>
            <label>План смены<input name="quantity" required min="1" type="number" value={productionPlan} onChange={(event) => setProductionPlan(event.target.value)} /></label>
            <label>Дата смены<input name="taskDate" required type="date" defaultValue={new Date().toISOString().slice(0, 10)} /></label>
          </> : null}
          <div className="operation-actions"><button className="ghost-button" type="button" onClick={onClose}>Отмена</button><button className="primary-button" disabled={busy} type="submit"><CheckCircle2 size={16} />{busy ? "Сохраняем" : "Подтвердить"}</button></div>
        </form>
      </section>
    </div>
  );
}

export default function SelectedShopApp(){return <MarketplaceBoundary wb={<ShopContextBoundary><App /></ShopContextBoundary>} ozon={session=><LazyPanel label="кабинет Ozon"><OzonCabinetShell session={session}/></LazyPanel>}/>;}

const SettingsViewV2Lazy = lazyNamed(() => import("./SettingsView"), "SettingsViewV2");
function SettingsViewV2(props: ComponentProps<typeof SettingsViewV2Lazy>) { return <LazyPanel label="Settings"><SettingsViewV2Lazy {...props} /></LazyPanel>; }
const ReportsViewLazy = lazyNamed(() => import("./ReportsView"), "ReportsView");
export function ReportsView(props: ComponentProps<typeof ReportsViewLazy>) { return <LazyPanel label="Reports"><ReportsViewLazy {...props} /></LazyPanel>; }
const FinanceViewLazy = lazyNamed(() => import("./FinanceView"), "FinanceView");
export function FinanceView(props: ComponentProps<typeof FinanceViewLazy>) { return <LazyPanel label="Finance"><FinanceViewLazy {...props} /></LazyPanel>; }
const SupplyViewLazy = lazyNamed(() => import("./SupplyView"), "SupplyView");
function SupplyView(props: ComponentProps<typeof SupplyViewLazy>) { return <LazyPanel label="Supply"><SupplyViewLazy {...props} /></LazyPanel>; }
const ProductsViewLazy = lazyNamed(() => import("./ProductsView"), "ProductsView");
export function ProductsView(props: ComponentProps<typeof ProductsViewLazy>) { return <LazyPanel label="Products"><ProductsViewLazy {...props} /></LazyPanel>; }
const AssistantViewLazy = lazyNamed(() => import("./AssistantView"), "AssistantView");
function AssistantView(props: ComponentProps<typeof AssistantViewLazy>) { return <LazyPanel label="Assistant"><AssistantViewLazy {...props} /></LazyPanel>; }
const ProductionViewLazy = lazyNamed(() => import("./ProductionView"), "ProductionView");
function ProductionView(props: ComponentProps<typeof ProductionViewLazy>) { return <LazyPanel label="Production"><ProductionViewLazy {...props} /></LazyPanel>; }
const StockViewLazy = lazyNamed(() => import("./StockView"), "StockView");
function StockView(props: ComponentProps<typeof StockViewLazy>) { return <LazyPanel label="Stock"><StockViewLazy {...props} /></LazyPanel>; }
const AdsViewLazy = lazyNamed(() => import("./AdsView"), "AdsView");
function AdsView(props: ComponentProps<typeof AdsViewLazy>) { return <LazyPanel label="Ads"><AdsViewLazy {...props} /></LazyPanel>; }
const ControlViewLazy = lazyNamed(() => import("./ControlView"), "ControlView");
export function ControlView(props: ComponentProps<typeof ControlViewLazy>) { return <LazyPanel label="Control"><ControlViewLazy {...props} /></LazyPanel>; }
