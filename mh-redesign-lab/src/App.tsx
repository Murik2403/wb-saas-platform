import { ThemeToggle } from "./ThemeToggle";
import { secondaryActions } from "./CabinetSupplementary";
import { plural, cabinetDate, cabinetText, checkPriority, wbAvailabilityMessage } from "./cabinet-copy";
import { SettingsWorkspace } from "./SettingsWorkspace";
import {createDialogFocusScope,useDialogFocus} from './dialog-focus';
import { downloadScoped, currentShop } from "./selected-shop";
import {fifoReasonLabel} from './fifo-reason';
import { SelectedShopSettings } from "./SelectedShopSettings";
import { SharedWarehouse } from './SharedWarehouse';
import { ShopContextBoundary } from "./ShopContextBoundary";
import { IncidentWorkflow } from "./IncidentWorkflow";
import { WorkcenterCalendar } from "./WorkcenterCalendar";
import { ProductionWorkspace } from "./ProductionWorkspace";
import { WorkspaceTabs } from "./WorkspaceTabs";
import { ManufacturingCosts } from "./ManufacturingCosts";
import { AdsAnalyticsPanel } from "./AdsAnalyticsPanel";
import { ProductEconomicsList, periodLengthDays } from "./ProductEconomicsList";
import { ProductsHeatmap } from "./ProductsHeatmap";
import { ProductGroupFilter, ProductGroupsPanel } from "./ProductGroupsPanel";
import { useGroupSelection, useProductGroups, ALL_PRODUCTS } from "./useProductGroups";
import { ProductCardsPanel } from "./ProductCardsPanel";
import { ShopQualityPanel } from "./ShopQualityPanel";
import { ProductPricesPanel } from "./ProductPricesPanel";
import { PriceAdvisorPanel } from "./PriceAdvisorPanel";
import { RnpPlanFact } from "./RnpPlanFact";
import { ManagementPnlSummary } from "./ManagementPnlSummary";
import { ManagementPnlMonthly } from "./ManagementPnlMonthly";
import { TaxSettingsPanel } from "./TaxSettingsPanel";
import { TaxReconciliationPanel } from "./TaxReconciliationPanel";
import { ExpenseCoveragePanel } from "./ExpenseCoveragePanel";
import { ManualStoreExpenseManager } from "./ManualStoreExpenseManager";
import { SettingsChangeHistory } from "./SettingsChangeHistory";
import { SupplyPlanning } from "./SupplyPlanning";
import { SelfBuyoutsPanel } from "./SelfBuyoutsPanel";
import { CashflowPanel } from "./CashflowPanel";
import { MigrationImport } from "./MigrationImport";
import { ShopWorkspaces } from "./ShopWorkspaces";
import { OzonAccounting } from "./OzonAccounting";
import { FinanceProfitHeading } from "./FinanceProfitHeading";
import { PriceScenarios } from "./PriceScenarios";
import { SyncHistory, BackupHistory } from "./SyncHistory";
import { OnboardingPanel } from "./OnboardingPanel";
import { ProductFeedbackLink } from "./ProductFeedbackLink";
import { RuntimeStatus } from "./RuntimeStatus";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  AlertTriangle,
  ArrowUpRight,
  BarChart3,
  Bell,
  Bot,
  CalendarDays,
  CheckCircle2,
  ClipboardCheck,
  ChevronRight,
  Command,
  Factory,
  Filter,
  Download,
  LayoutDashboard,
  LucideIcon,
  Megaphone,
  Menu,
  Package,
  PackageCheck,
  PackageX,
  Pencil,
  Plus,
  Play,
  RefreshCw,
  Search,
  ChevronDown,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  House,
  LogOut,
  Truck,
  Trash2,
  WalletCards,
  X,
  Zap,
} from "lucide-react";
import type {
  ActionRecommendation,
  AgentCandidate,
  AdCampaign,
  AdsHero,
  AssistantAutomation,
  CreativeSignal,
  DashboardMetric,
  ExpenseSplit,
  ExportItem,
  FinanceLedgerItem,
  FunnelStage,
  IconKey,
  InventoryWatchRow,
  MaterialNeed,
  NavItem,
  PageId,
  ProductionStage,
  ProductionTask,
  ReportCard,
  ReorderItem,
  SupplierPulse,
  SupplyShipment,
  StockCoverageRow,
  StockHero,
  StockRule,
  Tone,
} from "./contracts";
import { financeExportUrl, getFinanceFifoReconciliationPage, answerCustomerFeedback, answerCustomerQuestion, processCustomerReturn, sendCustomerChatMessage, connectWBToken, disconnectWBToken, getWBTokenStatus, connectOzonCredentials, disconnectOzonCredentials, getPriceUploadStatus, getPriceUploadHistory, applyFifoReconciliation, applyIncidentLoss, closeProductionExecution, createActionPlanItem, createIncidentCase, createIncidentCompensation, createProcurementOperation, createProcurementPayment, createProductionShift, createReportDefinition, updateReportDefinition, setReportDefinitionEnabled, createTenantBackup, decideAgentCandidate, deleteReportDefinition, downloadReportPdf, downloadSavedReport, generateQuickReport, generateReportDefinition, getActionPlanItems, getDashboardSession, getFifoReconciliation, getIncidentOperations, getMaterialInventory, getProcurementOperations, getProductionExecution, getProductionNorms, getProductCostSettings, getReportDelivery, getReportOperations, getAnalyticsDashboard, getSuppliers, getTenantBackups, getTenantSettings, getTenantSyncHistory, getTenantSyncStatus, getFbsSupplyBarcode, getFbsSupplyDetails, initializeMaterialFifo, postSupplyReceipt, refreshAgentRecommendations, requestTelegramLinkCode, saveProductionExecution, saveSupplier, startTenantSync, updateActionPlanItem, updateMaterialInventory, updateProcurementStatus, updateProductCostSettings, updateProductionNorms, updateTenantSettings, uploadProductPrices, updateProductCard, saveProductCardMedia, getManualExpenses, createManualExpense, deleteManualExpense, updateManualExpenseInclusion, getManagementEconomics, useDashboardData, type ActionPlanItem, type AnalyticsDashboard, type CustomerQualityData, type DashboardPeriod, type DashboardPeriodId, type DashboardSession, type DashboardViewModel, type DataQualityData, type FbsSupplyBarcode, type FbsSupplyDetail, type FifoReconciliationData, type FinanceFifoDetailPage, type IncidentOperations, type ManagementEconomics, type ManualExpense, type MaterialFifoRow, type MaterialInventory, type ProductCard, type ProductCostSetting, type ProductEconomicsRow, type ProductPriceRow, type PriceUploadHistoryItem, type ProductionExecutionTask, type ProductionNorm, type ReportDefinition, type ReportDelivery, type ReportOperations, type SupplierRecord, type TenantSettings, type TenantSyncRun } from "./data-source";
import { useProductsPrevious, useProductsTrend } from "./useProductsTrend";
import { FbsOrdersPanel } from "./FbsOrdersPanel";
import { compareAmount, compareCount, comparePoints } from "./productsDelta";
import { navItems as fallbackNavItems } from "./mock-dashboard";

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

function cx(...classes: Array<string | false | null | undefined>) {
  return classes.filter(Boolean).join(" ");
}

/** Small accessible replacement for browser prompt/alert/confirm.
 * It is deliberately DOM based so legacy inline action handlers can use it
 * without coupling every section to a second piece of React state.
 */
let interactionDialogSequence=0;
function openInteractionDialog(options: { title: string; message?: string; input?: string; confirm?: boolean }): Promise<string | boolean | null> {
  if (typeof document === "undefined") return Promise.resolve(options.input ?? (options.confirm ? false : null));
  const opener=document.activeElement instanceof HTMLElement?document.activeElement:null;
  const dialog = document.createElement("dialog");
  const titleId=`interaction-dialog-title-${++interactionDialogSequence}`;
  dialog.setAttribute("aria-labelledby",titleId);dialog.setAttribute("aria-modal","true");
  dialog.className = "plan-drawer operation-dialog interaction-dialog";
  const heading = document.createElement("h3");heading.id=titleId; heading.textContent = options.title;
  const message = document.createElement("p"); message.textContent = options.message ?? "";
  if(options.message){message.id=titleId+"-message";dialog.setAttribute("aria-describedby",message.id);}
  const form = document.createElement("form"); form.method = "dialog";
  const controls = document.createElement("div"); controls.className = "dialog-actions";
  const cancel = document.createElement("button"); cancel.type = "button"; cancel.className = "ghost-button"; cancel.textContent = "Отмена";
  const submit = document.createElement("button"); submit.type = "submit"; submit.className = "primary-button"; submit.textContent = options.confirm ? "Подтвердить" : "Сохранить";
  const input = options.input !== undefined ? document.createElement("textarea") : null;
  if (input) { input.className = "dialog-input"; input.value = options.input ?? ""; input.rows = 4; input.setAttribute("aria-label", options.title); }
  controls.append(cancel, submit);
  form.append(controls); dialog.append(heading, message); if (input) dialog.append(input); dialog.append(form);
  document.body.appendChild(dialog);
  return new Promise((resolve) => {
    let settled = false;let disposeFocus:(()=>void)|undefined;
    const finish = (value: string | boolean | null) => { if (settled) return; settled = true; dialog.remove();disposeFocus?.(); resolve(value); };
    cancel.addEventListener("click", () => finish(null));
    dialog.addEventListener("cancel", () => finish(null));
    form.addEventListener("submit", (event) => { event.preventDefault(); finish(options.confirm ? true : (input?.value ?? "")); });
    dialog.addEventListener("close", () => finish(null));
    if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", "");
    disposeFocus=createDialogFocusScope(dialog,()=>finish(null),opener);
  });
}

const askText = (title: string, initial = "") => openInteractionDialog({ title, input: initial }) as Promise<string | null>;
const askConfirm = (message: string) => openInteractionDialog({ title: "Подтверждение", message, confirm: true }) as Promise<boolean>;
const notifyUser = (message: string) => openInteractionDialog({ title: "Результат", message }).then(() => undefined);

function isPageId(value: string | null): value is PageId {
  return fallbackNavItems.some((item) => item.id === value);
}

function getInitialPage(): PageId {
  const url = new URL(window.location.href);
  const requestedPage = url.searchParams.get("view") ?? window.location.hash.replace("#", "");
  return isPageId(requestedPage) ? requestedPage : "today";
}

const dashboardPeriodOptions: Array<{ id: DashboardPeriodId; label: string }> = [
  { id: "today", label: "Сегодня" },
  { id: "7d", label: "7 дней" },
  { id: "30d", label: "30 дней" },
  { id: "90d", label: "90 дней" },
  { id: "custom", label: "Свои даты" },
];

function isDashboardPeriodId(value: string | null): value is DashboardPeriodId {
  return dashboardPeriodOptions.some((option) => option.id === value);
}

function dateInputValue(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function defaultCustomPeriod(): DashboardPeriod {
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - 29);
  return { id: "custom", start: dateInputValue(start), end: dateInputValue(end) };
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

function gatewayUrlFor(path: string) {
  const { protocol, hostname, host } = window.location;
  if (hostname === "localhost" || hostname === "127.0.0.1" || hostname.endsWith(".localhost")) {
    return `${protocol}//${host}${path}`;
  }

  const hostParts = hostname.split(".");
  const appIndex = hostParts.findIndex((part) => part === "app");
  if (appIndex >= 0 && appIndex + 1 < hostParts.length) {
    return `${protocol}//${hostParts.slice(appIndex + 1).join(".")}${path}`;
  }

  return `${protocol}//${host}${path}`;
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
    const form = document.createElement("form");
    form.method = "POST";
    form.action = getLogoutUrl();
    form.style.display = "none";
    document.body.appendChild(form);
    form.submit();
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
    const frame = window.requestAnimationFrame(() => {
      const element = document.getElementById(target);
      if (!element) {
        setPendingScrollTarget(null);
        return;
      }
      element.scrollIntoView({ behavior: "smooth", block: "start" });
      element.classList.add("section-focus");
      window.setTimeout(() => element.classList.remove("section-focus"), 1200);
      setPendingScrollTarget(null);
    });
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
                    <a href={getGatewayUrl("/billing")} role="menuitem"><strong>Подписка и оплата</strong><small>Статус тарифа и история платежей</small></a>
                    <a href={getGatewayUrl("/account")} role="menuitem"><strong>Аккаунт</strong><small>Пароль и email</small></a>
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
          <><SharedWarehouse/><StockView
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
          <><FinanceView cashflow={<CashflowPanel compact role={session?.user.role ?? "viewer"} />}
            selfBuyouts={<SelfBuyoutsPanel role={session?.user.role ?? "viewer"} period={dashboardPeriod} confirm={askConfirm} products={dashboard.productEconomics.map((p) => ({ nmId: p.nmId, article: p.article, product: p.product }))} />}
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
        {activeNav === "settings" ? ((currentShop()?.id??0)!==0 ? <SelectedShopSettings/> : <SettingsViewV2 sectionTarget={pendingScrollTarget} onSynced={dashboard.refresh} migration={<MigrationImport role={session?.user.role ?? "viewer"} onRefresh={dashboard.refresh} />} />) : null}

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
  busyId,
  onUpdate,
}: {
  filter: "all" | ActionPlanItem["status"];
  items: ActionPlanItem[];
  onClose: () => void;
  onFilterChange: (filter: "all" | ActionPlanItem["status"]) => void;
  onOpenFifo: () => void;
  onExecute: (item: ActionPlanItem) => Promise<void>;
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
                  <button className="secondary-button" type="button" disabled={busyId === item.id} onClick={() => void onExecute(item)}>
                    {busyId === item.id ? "Выполняем…" : item.status === "in_progress" ? "Продолжить" : "В работу"}
                  </button>
                ) : null}
                {item.status === "blocked" ? (
                  <button className="secondary-button" type="button" disabled={busyId === item.id} onClick={() => void onExecute(item)}>
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

const stockTabs = [{ id: "stock", label: "Остатки" }, { id: "reorder", label: "Пополнение" }] as const;
const stockTabTargets: Record<string, string> = { "stock-coverage": "stock", "seller-warehouses": "stock", "reorder-plan": "reorder", "stock-reorder-plan": "reorder" };

function StockView({
  inventory,
  onAction,
  onOpenStockRow,
  onOpenReorder,
  onOpenCoverage,
  onOpenRule,
  reorderPlan,
  stockCoverage,
  stockHero,
  stockMetrics,
  stockRules,
  sellerWarehouses,
  target,
}: {
  inventory: InventoryWatchRow[];
  onAction: (label: string, itemLabel: string) => void;
  onOpenStockRow: (row: InventoryWatchRow) => void;
  onOpenReorder: (item: ReorderItem) => void;
  onOpenCoverage: (row: StockCoverageRow) => void;
  onOpenRule: (row: StockRule) => void;
  reorderPlan: ReorderItem[];
  stockCoverage: StockCoverageRow[];
  stockHero: StockHero;
  stockMetrics: DashboardMetric[];
  stockRules: StockRule[];
  sellerWarehouses: Array<{ id?: number; name?: string; office_id?: number; quantity?: number; processing?: boolean }>;
  target?: string | null;
}) {
  return (
    <div className="workspace-view">
      <IncidentWorkflow />
      <section className="split-hero">
        <button className={cx("hero-card", "detail-trigger", stockHero.tone === "danger" ? "urgent" : "calm")} type="button" onClick={() => onAction("Поставка", "Пополнение")}>
          <div className="eyebrow">
            <AlertTriangle size={16} />
            пополнение
          </div>
          <h2>{stockHero.primary}</h2>
          <p>{stockHero.primaryNote}</p>
        </button>
        <button className="hero-card calm detail-trigger" type="button" onClick={() => onAction("Контроль", "Запасы WB")}>
          <div className="eyebrow">
            <PackageCheck size={16} />
            запас
          </div>
          <h2>{stockHero.secondary}</h2>
          <p>{stockHero.secondaryNote}</p>
        </button>
      </section>

      <MetricGrid metrics={stockMetrics} onMetricClick={(metric) => onAction("Контроль", metric.label)} />

      <WorkspaceTabs ariaLabel="Разделы склада" storageKey="mh.tab.stock" target={target} targetMap={stockTabTargets} tabs={stockTabs} panels={{
        stock: (<>
      <InventoryTable inventory={inventory} onOpen={(row) => onOpenStockRow(row)} />
      <div className="stock-panels">
          <section className="panel" id="stock-coverage">
            <SectionHeader compact kicker="Контур" title="Запасы WB">
              <button className="icon-button" type="button" aria-label="Открыть контроль запасов" onClick={() => onAction("Контроль", "Запасы WB")}><Package size={20} /></button>
            </SectionHeader>
            <div className="warehouse-list">
              {stockCoverage.map((row) => (
                <CoverageRow key={row.label} row={row} onOpen={() => onOpenCoverage(row)} />
              ))}
            </div>
          </section>

          <section className="panel" id="seller-warehouses">
            <SectionHeader compact kicker="FBS / DBS" title="Склады продавца"><Truck size={20} /></SectionHeader>
            {sellerWarehouses.length ? <div className="warehouse-list">{sellerWarehouses.map((warehouse) => <button className="warehouse-row detail-trigger" type="button" key={String(warehouse.id ?? warehouse.name)} onClick={() => onAction("Поставка", String(warehouse.name ?? "Склад продавца"))}><span><strong>{warehouse.name ?? "Склад продавца"}</strong><small>{warehouse.processing ? "обновляется" : "остатки продавца"}</small></span><b>{Number(warehouse.quantity ?? 0).toLocaleString("ru-RU")} шт.</b></button>)}</div> : <p className="plan-empty">Склады продавца не заведены или у токена нет категории «Маркетплейс».</p>}
          </section>
      </div>
        </>),
        reorder: (<>
      <div className="stock-grid">
        <section className="work-section" id="reorder-plan">
          <SectionHeader kicker="План пополнения" title="Что отгружать первым">
            <span className="section-tag">
              <Truck size={16} />
              Склад
            </span>
          </SectionHeader>

          <div className="reorder-list">
            {reorderPlan.map((item) => (
              <article className={cx("reorder-card", "detail-trigger", item.tone)} key={item.sku} tabIndex={0} role="button" onClick={() => onOpenReorder(item)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpenReorder(item); } }}>
                <div>
                  <span>{item.sku}</span>
                  <h3>{item.name}</h3>
                  <p>{item.risk}</p>
                </div>
                <div className="reorder-numbers">
                  <strong>{item.days}</strong>
                  <span>{item.warehouse}</span>
                </div>
                <button className="secondary-button" type="button" onClick={(event) => { event.stopPropagation(); onAction("Отгрузка", item.sku); }}>
                  {item.qty}
                </button>
              </article>
            ))}
          </div>
        </section>
        <aside className="insight-column">
          <section className="panel">
            <SectionHeader compact kicker="Правила" title="Пополнение">
              <button className="icon-button" type="button" aria-label="Открыть поставки для пополнения" onClick={() => onAction("Поставка", "Пополнение")}><Zap size={20} /></button>
            </SectionHeader>
            <div className="rule-list">
              {stockRules.map((rule) => (
                <button className="rule-card detail-trigger" type="button" key={rule.id} onClick={() => onOpenRule(rule)}>
                  <strong>{rule.title}</strong>
                  <p>{rule.description}</p>
                </button>
              ))}
            </div>
          </section>
        </aside>
      </div>
        </>),
      }} />

    </div>
  );
}

const adsTabs = [{ id: "campaigns", label: "Кампании" }, { id: "analytics", label: "Аналитика РК" }] as const;
const reportsTabs = [{ id: "reports", label: "Отчёты" }, { id: "schedules", label: "Расписания и запуски" }, { id: "delivery", label: "Доставка" }] as const;

function AdsView({
  adsFunnel,
  adsHero,
  adsMetrics,
  campaigns,
  creativeSignals,
  onAction,
  period,
  setPeriod,
  target,
}: {
  adsFunnel: FunnelStage[];
  adsHero: AdsHero;
  adsMetrics: DashboardMetric[];
  campaigns: AdCampaign[];
  creativeSignals: CreativeSignal[];
  onAction: (label: string, itemLabel: string) => void;
  period: DashboardPeriod;
  setPeriod: (period: DashboardPeriod) => void;
  target?: string | null;
}) {
  const campaignsPanel = (<>
      <section className={cx("hero-strip", "ads-hero", adsHero.tone)} aria-label="Рекламный сигнал">
        <div>
          <div className="eyebrow">
            <Megaphone size={16} />
            кампании под контролем
          </div>
          <h2>{adsHero.title}</h2>
          <p>{adsHero.description}</p>
        </div>
        <div className="hero-actions">
          <button className="secondary-button" type="button" onClick={() => onAction("Медиаплан", "реклама")}>
            <Plus size={17} />
            <span>В медиаплан</span>
          </button>
        </div>
      </section>

      <MetricGrid metrics={adsMetrics} onMetricClick={(metric) => onAction("Медиаплан", metric.label)} />

      <div className="content-grid">
        <section className="work-section" id="ads-campaigns">
          <SectionHeader kicker="Кампании" title="Где менять ставки">
            <DashboardPeriodControl period={period} setPeriod={setPeriod} />
          </SectionHeader>

          <div className="campaign-list">
            {campaigns.map((campaign) => (
              <CampaignRow campaign={campaign} key={campaign.name} onAction={onAction} />
            ))}
          </div>
        </section>

        <aside className="insight-column">
          <section className="panel" id="ads-funnel">
            <SectionHeader compact kicker="Воронка" title="Путь до заказа">
              <BarChart3 size={20} />
            </SectionHeader>
            <div className="funnel-list">
              {adsFunnel.map((stage) => (
                <FunnelBar key={stage.label} label={stage.label} value={stage.value} width={stage.width} onOpen={() => onAction("Медиаплан", stage.label)} />
              ))}
            </div>
          </section>

          <section className="panel" id="ads-signals">
            <SectionHeader compact kicker="Сигналы" title="Товары в ставках">
              <Sparkles size={20} />
            </SectionHeader>
            <div className="creative-grid">
              {creativeSignals.map((signal) => (
                <CreativeCard active={signal.active} key={signal.label} label={signal.label} value={signal.value} onOpen={() => onAction("Медиаплан", signal.label)} />
              ))}
            </div>
          </section>
        </aside>
      </div>
  </>);
  return (
    <div className="workspace-view">
      <WorkspaceTabs
        tabs={adsTabs}
        ariaLabel="Разделы рекламы"
        storageKey="mh.tab.ads"
        target={target}
        targetMap={{ "ads-campaigns": "campaigns", "ads-funnel": "campaigns", "ads-signals": "campaigns", "ads-analytics": "analytics" }}
        panels={{ campaigns: campaignsPanel, analytics: <AdsAnalyticsPanel period={period} /> }}
      />
    </div>
  );
}

export function FinanceView({
  cashWindows,
  cashflow,
  selfBuyouts,
  expenseSplit,
  financeLedger,
  financeReport,
  financeMetrics,
  onAction,
  period,
  setPeriod,
}: {
  cashflow?: ReactNode;
  selfBuyouts?: ReactNode;
  cashWindows: Array<{ label: string; value: string; detail: string }>;
  expenseSplit: ExpenseSplit[];
  financeLedger: FinanceLedgerItem[];
  financeReport: import("./contracts").FinanceReportState;
  financeMetrics: DashboardMetric[];
  onAction: (label: string, itemLabel: string) => void;
  period: DashboardPeriod;
  setPeriod: (period: DashboardPeriod) => void;
}) {
  const [exportError,setExportError] = useState("");
  const [exportBusy,setExportBusy] = useState(false);
  const exportFinance=async(format:"xlsx"|"csv")=>{setExportBusy(true);setExportError("");try{await downloadScoped(financeExportUrl(period,format),`marketshelper-finance.${format}`);}catch(reason){setExportError(reason instanceof Error?reason.message:"Экспорт выбранного магазина недоступен");}finally{setExportBusy(false);}};
  const financeCoverage = financeReport.complete
    ? { label: "период закрыт", className: "status-pill good", note: "Все даты выбранного периода покрыты реализацией WB." }
    : !financeReport.lastDate
      ? { label: "нет данных WB", className: "status-pill warn", note: "Реализация WB ещё не загружена." }
      : Number(financeReport.lagDays ?? 0) <= 1
        ? { label: "WB обновляет данные", className: "status-pill info", note: "WB ещё не закрыл последний день. Расчёт выполнен по последней доступной дате." }
        : { label: "неполное покрытие", className: "status-pill warn", note: "Часть выбранного периода ещё не покрыта реализацией WB." };
  return (
    <div className="workspace-view finance-workspace">
      <section className="hero-strip finance-hero" aria-label="Финансовый контур">
        <div>
          <div className="eyebrow">
            <WalletCards size={16} />
            деньги
          </div>
          <FinanceProfitHeading report={financeReport} />
          <p>Результат WB по реализации за выбранный период с учтённой рекламой и себестоимостью. Дополнительные подтверждённые расходы показаны ниже в управленческом результате.</p>
        </div>
          <div className="hero-actions">
            <button className="secondary-button" type="button" onClick={() => onAction("Отчет", "финансы")}>
            <Plus size={17} />
              <span>В отчет</span>
            </button>
            <button className="secondary-button" type="button" disabled={exportBusy} onClick={()=>void exportFinance("xlsx")}>
              <Download size={17} />
              <span>Скачать XLSX</span>
            </button>
            <button className="secondary-button" type="button" disabled={exportBusy} onClick={()=>void exportFinance("csv")}><Download size={17} /><span>Скачать CSV</span></button>
            {exportError&&<p role="alert">{exportError}</p>}
        </div>
      </section>

      <div className="finance-period-status">
        <span className={financeCoverage.className}>{financeCoverage.label}</span>
        <p className="form-hint finance-coverage-note">{financeCoverage.note}</p>
      </div>
      <MetricGrid metrics={financeMetrics} onMetricClick={(metric) => onAction("Отчет", metric.label)} />
      {Number(financeReport.advertisingAccounting?.excludedRub ?? 0) !== 0 ? <p className="form-hint">Удержания WB за оплату продвижения: {financeReport.advertisingAccounting!.excludedRub.toLocaleString("ru-RU")} ₽. Рекламный расход учтён по кампаниям за выбранный период; его оплата не вычитается повторно.</p> : null}
      {financeReport.advertisingAccounting?.source === "settlement_fallback" ? <p className="form-hint" role="status">Расходы рекламных кампаний не загружены. В расчёте сохранены удержания WB за продвижение; дата оплаты может отличаться от периода показа рекламы.</p> : null}
      {financeReport.balance !== undefined && financeReport.balance > 0 ? <div className="cash-grid finance-balance-grid"><div className="stat"><span>Баланс WB</span><strong>{Math.round(financeReport.balance).toLocaleString("ru-RU")} ₽</strong><small>текущий баланс продавца</small></div><div className="stat"><span>К выводу</span><strong>{Math.round(financeReport.forWithdraw ?? 0).toLocaleString("ru-RU")} ₽</strong><small>по данным Finance API</small></div></div> : null}

      {financeReport.cogsEvidence && financeReport.cogsEvidence.totalUnits > 0 ? <>
        {financeReport.cogsEvidence.quality === "insufficient" ? <p className="form-hint" role="status">Сумма прибыли предварительная: часть продаж или возвратов не имеет ни подтверждённого FIFO, ни положительной текущей себестоимости. Для этих единиц нельзя считать себестоимость равной нулю.</p> : financeReport.cogsEvidence.quality !== "exact" ? <p className="form-hint" role="status">Сумма прибыли предварительная: часть себестоимости оценена по текущей ставке товара и может отличаться от стоимости партии на дату продажи.</p> : null}
      </> : null}
      <ManagementPnlSummary period={period} />
      <TaxReconciliationPanel />
      <ManagementPnlMonthly period={period} onOpenTaxSettings={() => onAction("Налоги", "")} />
      {cashflow}
      <section className="work-section finance-movement-panel" aria-label="Денежное движение">
        <SectionHeader kicker="P&L" title="Денежное движение">
          <DashboardPeriodControl period={period} setPeriod={setPeriod} />
        </SectionHeader>
          {financeLedger.length ? <div className="ledger-list">
            {financeLedger.map((item) => <FinanceLedgerRow item={item} key={item.label} onOpen={() => onAction("Отчет", item.label)} />)}
          </div> : <p className="plan-empty">Нет данных о движении денег</p>}
      </section>
      <div className="content-grid finance-expenses-grid" aria-label="Расходы и их структура">
          <section className="panel finance-expenses-panel">
            <SectionHeader compact kicker="WB реализация" title="Статьи расходов">
            </SectionHeader>
            {financeReport.wbCosts ? <div className="finance-wb-details"><h4>Дополнительная аналитика WB</h4><p className="form-hint">Расшифровка удержаний; не прибавляется повторно к расходам финансового отчёта.</p>{financeReport.wbCosts.errors.length ? <p className={financeReport.wbCosts.status === "processing" ? "form-hint" : "operation-error"}>{financeReport.wbCosts.status === "processing" ? "Дополнительная аналитика WB ещё рассчитывается." : wbAvailabilityMessage(financeReport.wbCosts.errors)}</p> : null}<p>Подмены: {financeReport.wbCosts.deductions.toLocaleString("ru-RU")} ₽ · Габариты: {financeReport.wbCosts.measurementPenalties.toLocaleString("ru-RU")} ₽ · Платное хранение: {financeReport.wbCosts.paidStorage.toLocaleString("ru-RU")} ₽{financeReport.wbCosts.status === "processing" ? " · расчёт продолжается" : ""}</p></div> : null}
            {financeReport.rows.length ? <div className="ledger-list">{financeReport.rows.map((row) => <FinanceLedgerRow key={row.label} item={{ label: row.label, amount: `${Math.round(row.amount).toLocaleString("ru-RU")} ₽`, detail: "финансовый отчёт WB", tone: row.amount < 0 ? "warn" : "good" }} onOpen={() => onAction("Отчет", row.label)} />)}</div> : <p className="plan-empty">Финансовый отчёт WB ещё не загружен</p>}
            {financeReport.lastDate ? <small className="muted">Последняя дата реализации: {financeReport.lastDate}{financeReport.calculationEnd ? ` · расчёт до ${financeReport.calculationEnd}` : ""}{financeReport.lagDays ? ` · отставание ${Math.round(financeReport.lagDays)} дн.` : ""}</small> : null}
          </section>
        <aside className="insight-column">
          <section className="panel">
            <SectionHeader compact kicker="Структура" title="Доля расходов">
              <WalletCards size={20} />
            </SectionHeader>
            {expenseSplit.length ? <div className="split-list">
              {expenseSplit.map((item) => <SplitRow item={item} key={item.label} onOpen={() => onAction("Отчет", item.label)} />)}
            </div> : <p className="plan-empty">Нет данных о структуре расходов</p>}
          </section>

        </aside>
      </div>
          <section className="panel">
            <SectionHeader compact kicker="Контроль" title="Кассовые окна">
              <button className="icon-button" type="button" aria-label="Сформировать финансовый отчёт" onClick={() => onAction("Отчет", "финансы")}><ShieldCheck size={20} /></button>
            </SectionHeader>
            {cashWindows.length ? <div className="cash-grid">{cashWindows.map((item) => <button className="stat detail-trigger" type="button" key={item.label} onClick={() => onAction("Отчет", item.label)}><span>{item.label}</span><strong>{item.value}</strong><small>{item.detail}</small></button>)}</div> : <p className="plan-empty">Нет загруженных финансовых операций</p>}
          </section>
      <details className="panel finance-cost-review" aria-label="Проверка себестоимости">
        <summary><h3>Проверка себестоимости</h3><span className="form-hint">Источники стоимости, сверка партий и подробные строки</span></summary>
      {financeReport.cogsEvidence && financeReport.cogsEvidence.totalUnits > 0 ? <section className={`panel finance-cogs-evidence ${financeReport.cogsEvidence.complete ? "" : "finance-cogs-incomplete"}`} aria-label="Покрытие себестоимости в прибыли">
        <SectionHeader compact kicker="Себестоимость в P&L" title={financeReport.cogsEvidence.complete ? "Себестоимость подтверждена или оценена полностью" : "В прибыли есть строки без себестоимости"}>
          <PackageCheck size={18} />
        </SectionHeader>
        <div className="cash-grid finance-fifo-summary">
          <div className="stat"><span>Точный исторический FIFO</span><strong>{Math.round(financeReport.cogsEvidence.exactFifoRub).toLocaleString("ru-RU")} ₽</strong><small>{financeReport.cogsEvidence.exactUnits.toLocaleString("ru-RU")} ед. · покрытие {financeReport.cogsEvidence.exactUnitCoveragePercent.toLocaleString("ru-RU", { maximumFractionDigits: 2 })}%</small></div>
          <div className="stat"><span>Оценка по текущей себестоимости</span><strong>{Math.round(financeReport.cogsEvidence.currentEstimateRub).toLocaleString("ru-RU")} ₽</strong><small>{financeReport.cogsEvidence.estimatedUnits.toLocaleString("ru-RU")} ед. · оценка не является историческим FIFO</small></div>
          <div className="stat"><span>Без подтверждённой ставки</span><strong>{financeReport.cogsEvidence.insufficientUnits.toLocaleString("ru-RU")} ед.</strong><small>{financeReport.cogsEvidence.totalUnits.toLocaleString("ru-RU")} ед. продаж и возвратов в периоде</small></div>
        </div>

      </section> : null}
          {financeReport.fifoPeriodBasis === "sale_date" ? <section className="panel finance-fifo-panel" aria-label="Сверка FIFO-себестоимости продаж">
            <SectionHeader compact kicker="Себестоимость партий" title="Сверка FIFO по дате продажи">
              <PackageCheck size={18} />
            </SectionHeader>
            <p className="form-hint">Отдельная оценка по датам продаж и возвратов. Она не включена в прибыль выше: финансовый отчёт WB использует даты реализации, а возвраты могут относиться к другому периоду. В журнале могут быть начальные остатки и временные оценки по стоимости из настроек товара, поэтому сумму нельзя считать полностью фактической себестоимостью.</p>
            {financeReport.fifoCosts?.length ? (() => {
              const rows = financeReport.fifoCosts;
              const totalEvents = rows.reduce((sum, row) => sum + row.totalEvents, 0);
              const coveredEvents = rows.reduce((sum, row) => sum + row.coveredEvents, 0);
              const baseline = rows.reduce((sum, row) => sum + row.baselineCost, 0);
              const estimated = rows.reduce((sum, row) => sum + row.estimatedFifoCost, 0);
              const journaled = rows.reduce((sum, row) => sum + row.journaledCost, 0);
              return <>
                <div className="cash-grid finance-fifo-summary">
                  <div className="stat"><span>Оценка по FIFO</span><strong>{Math.round(estimated).toLocaleString("ru-RU")} ₽</strong><small>Для продаж и возвратов выбранного периода</small></div>
                  <div className="stat"><span>Базовая оценка</span><strong>{Math.round(baseline).toLocaleString("ru-RU")} ₽</strong><small>По себестоимости из настроек товара</small></div>
                  <div className="stat"><span>События в FIFO-журнале</span><strong>{coveredEvents.toLocaleString("ru-RU")} / {totalEvents.toLocaleString("ru-RU")}</strong><small>{Math.round(totalEvents ? coveredEvents / totalEvents * 100 : 0)}% событий учтено; сумма учтённых событий {Math.round(journaled).toLocaleString("ru-RU")} ₽</small></div>
                </div>
                <div className="ledger-list finance-fifo-list">{[...rows].sort((a, b) => Math.abs(b.estimatedFifoCost - b.baselineCost) - Math.abs(a.estimatedFifoCost - a.baselineCost)).slice(0, 8).map((row) => <FinanceLedgerRow key={row.nmId} item={{ label: row.productName || row.supplierArticle || ("WB " + row.nmId), amount: Math.round(row.estimatedFifoCost).toLocaleString("ru-RU") + " ₽", detail: "WB " + row.nmId + " · продажи " + row.saleUnits + " · возвраты " + row.returnUnits + " · база " + Math.round(row.baselineCost).toLocaleString("ru-RU") + " ₽ · ошибки " + row.errorEvents, tone: row.errorEvents ? "warn" : "info" }} />)}</div>
              </>;
            })() : <p className="plan-empty">За период нет строк продаж или возвратов.</p>}
          </section> : null}
          {financeReport.fifoReconciliation ? <section className="panel" aria-label="Сверка FIFO с финансовыми строками WB">
            <SectionHeader compact kicker="Финансовые операции WB" title="Сводка качества FIFO-сопоставления">
              <PackageCheck size={18} />
            </SectionHeader>
            <p className="form-hint">Строки сгруппированы по дате финансовой операции. Точная себестоимость проверяется по данным продажи или возврата, количеству товара и подтверждённым производственным или закупочным партиям. Подтверждённые строки уже включены в P&L; остальные строки учтены по текущей себестоимости как оценка или отмечены как неполные.</p>
            {financeReport.fifoReconciliation.saleReturnRows ? <>
              <div className="cash-grid finance-fifo-summary">
                <div className="stat"><span>Точно сопоставлено</span><strong>{financeReport.fifoReconciliation.exactRows.toLocaleString("ru-RU")} / {financeReport.fifoReconciliation.saleReturnRows.toLocaleString("ru-RU")}</strong><small>{Math.round(financeReport.fifoReconciliation.exactRows / financeReport.fifoReconciliation.saleReturnRows * 100)}% финансовых строк</small></div>
                <div className="stat"><span>Покрытие по количеству</span><strong>{financeReport.fifoReconciliation.exactUnits.toLocaleString("ru-RU")} / {financeReport.fifoReconciliation.saleReturnUnits.toLocaleString("ru-RU")} ед.</strong><small>{financeReport.fifoReconciliation.exactUnitCoveragePercent.toLocaleString("ru-RU", { maximumFractionDigits: 2 })}% единиц продаж и возвратов</small></div>
                <div className="stat"><span>Подтверждённая FIFO-себестоимость</span><strong>{Math.round(financeReport.fifoReconciliation.exactFifoCogs).toLocaleString("ru-RU")} ₽</strong><small>Только точно сопоставленные строки, возвраты со знаком минус</small></div>
              </div>
              <p className="form-hint">Требуют проверки: {Object.entries(financeReport.fifoReconciliation.statusCounts).filter(([status]) => status !== "exact").map(([status, count]) => `${({ unmatched: "нет связанной продажи", ambiguous: "несколько возможных соответствий", synthetic: "временная оценка", baseline: "оценка по настройкам" } as Record<string, string>)[status] ?? "другая причина"} — ${count}`).join(" · ") || "нет"}</p>
            </> : <p className="plan-empty">В выбранном периоде нет финансовых строк продаж или возвратов.</p>}
          </section> : null}
          <FinanceFifoDetailTable period={period} />
      </details>
      <details className="cabinet-disclosure"><summary>Магазины WB</summary><ShopWorkspaces /></details>
      <details className="cabinet-disclosure"><summary>Учёт Ozon</summary><OzonAccounting /></details>
      <details className="cabinet-disclosure"><summary>Сценарии цены и закупки</summary><PriceScenarios /></details>
      {selfBuyouts}
    </div>
  );
}

function FinanceFifoDetailTable({ period }: { period: DashboardPeriod }) {
  const [open, setOpen] = useState(false);
  const [pageNumber, setPageNumber] = useState(1);
  const [reloadVersion, setReloadVersion] = useState(0);
  const [data, setData] = useState<FinanceFifoDetailPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pageSize = 25;
  const offset = (pageNumber - 1) * pageSize;

  useEffect(() => {
    setPageNumber(1);
    setData(null);
  }, [period.id, period.start, period.end]);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    getFinanceFifoReconciliationPage(period, offset, pageSize, controller.signal)
      .then((result) => setData(result))
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Не удалось загрузить строки FIFO-сверки");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [open, period.id, period.start, period.end, offset, reloadVersion]);

  const pageCount = Math.max(1, Math.ceil((data?.total ?? 0) / pageSize));
  const statusLabel: Record<string, string> = {
    exact: "точно",
    unmatched: "нет пары",
    ambiguous: "неоднозначно",
    synthetic: "временный слой",
    baseline: "базовая оценка",
  };

  return (
    <section className="panel finance-fifo-detail-panel" aria-label="Детальные строки FIFO-сверки">
      <div className="finance-fifo-detail-heading">
        <div>
          <span className="eyebrow">Разбор финансовых строк</span>
          <h3>Детальная FIFO-сверка</h3>
        </div>
        <button className="secondary-button" type="button" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
          {open ? "Свернуть" : "Показать строки"}
        </button>
      </div>
      <p className="form-hint">Проверьте стоимость каждой продажи и возврата. Столбец «Учтено в P&amp;L» показывает стоимость подтверждённой партии, оценку по текущей ставке или отсутствие подтверждённой себестоимости. Идентификаторы операций доступны в раскрытой таблице.</p>
      {open ? <div>
        {loading ? <p role="status" className="plan-empty">Загружаю FIFO-сверку…</p> : null}
        {error ? <div className="operation-error" role="alert"><p>Не удалось загрузить строки: {error}</p><button className="secondary-button" type="button" onClick={() => setReloadVersion((version) => version + 1)}>Повторить</button></div> : null}
        {!loading && !error && data && data.total === 0 ? <p className="plan-empty">За выбранный период нет строк продаж или возвратов для сверки.</p> : null}
        {!loading && !error && data && data.rows.length > 0 ? <>
          <div className="finance-fifo-table-scroll">
            <table className="finance-fifo-detail-table">
              <thead><tr><th>rrd_id</th><th>Дата операции</th><th>Артикул / nm_id</th><th>SRID</th><th>Тип</th><th>Шт.</th><th>Статус и причина</th><th>Подтверждённая стоимость</th><th>Наблюдаемая FIFO-сумма</th><th>Учтено в P&amp;L</th></tr></thead>
              <tbody>{data.rows.map((row) => <tr key={`${row.rrdId}:${row.eventType}`}>
                <td>{row.rrdId || "—"}</td>
                <td>{row.operationDate || "—"}</td>
                <td>{row.supplierArticle || "—"}<small>WB {row.nmId || "—"}</small></td>
                <td className="finance-fifo-srid">{row.srid || "—"}</td>
                <td>{row.eventType === "return" ? "Возврат" : "Продажа"}</td>
                <td>{row.units.toLocaleString("ru-RU")}</td>
                <td><strong>{statusLabel[row.status] ?? row.status}</strong>{row.reason ? <small>{fifoReasonLabel(row.reason)}</small> : null}</td>
                <td>{row.eligibleCostRub === null ? "—" : `${row.eligibleCostRub.toLocaleString("ru-RU")} ₽`}</td>
                <td>{row.observedCostRub === null ? "—" : `${row.observedCostRub.toLocaleString("ru-RU")} ₽`}</td>
                <td>{row.cogsSource === "exact_fifo" ? "Точный FIFO" : row.cogsSource === "manual_source_fifo" ? "FIFO по ручному источнику · предварительно" : row.cogsSource === "current_cost_estimate" ? "Оценка по текущей ставке" : row.cogsSource === "insufficient" ? "Нет себестоимости" : "Исключено"}<small>{row.cogsSource === "insufficient" ? "Прибыль предварительная" : `${Number(row.cogsRub ?? 0).toLocaleString("ru-RU")} ₽`}</small></td>
              </tr>)}</tbody>
            </table>
          </div>
          <div className="finance-fifo-pagination">
            <span>Строки {offset + 1}–{Math.min(offset + data.rows.length, data.total)} из {data.total.toLocaleString("ru-RU")}</span>
            <div>
              <button className="secondary-button" type="button" onClick={() => setPageNumber((page) => Math.max(1, page - 1))} disabled={pageNumber <= 1 || loading}>Назад</button>
              <span aria-live="polite">Страница {pageNumber} из {pageCount}</span>
              <button className="secondary-button" type="button" onClick={() => setPageNumber((page) => Math.min(pageCount, page + 1))} disabled={pageNumber >= pageCount || loading}>Вперёд</button>
            </div>
          </div>
        </> : null}
      </div> : null}
    </section>
  );
}

const productTabs = [{ id: "products", label: "Товары" }, { id: "analytics", label: "Аналитика" }, { id: "content", label: "Контент и качество" }, { id: "prices", label: "Цены" }] as const;
const productTargetMap: Record<string, string> = { "products-heatmap": "analytics", "rnp-plan-fact": "analytics", "product-cards": "content", "shop-quality": "content", "price-advisor": "prices", "product-prices": "prices", "price-uploads": "prices" };

export function ProductsView({
  period,
  productCards,
  productCardErrors,
  productPriceErrors,
  products,
  prices,
  customerQuality,
  onRefresh,
  setPeriod,
  onOpenProduct,
  target,
}: {
  period: DashboardPeriod;
  productCards: import("./data-source").ProductCard[];
  productCardErrors: string[];
  productPriceErrors: string[];
  products: ProductEconomicsRow[];
  prices: ProductPriceRow[];
  customerQuality: CustomerQualityData | null;
  onRefresh: () => Promise<void>;
  setPeriod: (period: DashboardPeriod) => void;
  onOpenProduct: (item: ProductEconomicsRow) => void;
  target?: string | null;
}) {
  const [query, setQuery] = useState("");
  const [priceDrafts, setPriceDrafts] = useState<Record<number, { price: number; discount: number }>>({});
  const [priceMessage, setPriceMessage] = useState("");
  const [priceUploadId, setPriceUploadId] = useState<number | null>(null);
  const [priceUploadStatus, setPriceUploadStatus] = useState("");
  const [priceSubmitting, setPriceSubmitting] = useState(false);
  const priceSubmitLock = useRef(false);
  const [priceResults, setPriceResults] = useState<Array<Record<string, unknown>>>([]);
  const [priceUploadHistory, setPriceUploadHistory] = useState<PriceUploadHistoryItem[]>([]);
  const [editingCard, setEditingCard] = useState<ProductCard | null>(null);
  const normalized = query.trim().toLocaleLowerCase("ru-RU");
  const productGroups = useProductGroups();
  const [groupId, setGroupId] = useGroupSelection(productGroups.groups, productGroups.loaded);
  const groupMembers = groupId === ALL_PRODUCTS ? null : new Set(productGroups.groups.find((g) => String(g.id) === groupId)?.nmIds);
  const visible = products.filter((item) => (!groupMembers || groupMembers.has(Number(item.nmId))) && (!normalized || `${item.article} ${item.product} ${item.nmId}`.toLocaleLowerCase("ru-RU").includes(normalized)));
  const revenue = products.reduce((sum, item) => sum + item.revenue, 0);
  const netProfit = products.reduce((sum, item) => sum + item.margin, 0);
  const netMarginPct = revenue > 0 ? (netProfit / revenue) * 100 : 0;
  const atRisk = products.filter((item) => item.daysOfStock !== null && item.daysOfStock >= 0 && item.daysOfStock < 7).length;
  const trend = useProductsTrend(period).data; // lazy, like the deltas: the table renders first
  const previous = useProductsPrevious(period).data; // lazy: tiles render first, the delta appears when it arrives
  const prev = previous ? (previous.hasData ? previous.totals : null) : undefined;
  const changes = prev === undefined ? [undefined, undefined, undefined, undefined] : [
    compareAmount(revenue, prev?.revenue, "₽"),
    comparePoints(netMarginPct, prev && prev.revenue > 0 ? prev.marginPct : null),
    compareCount(atRisk, prev?.atRisk, "SKU", false),
    compareCount(products.length, prev?.skuCount, "", null),
  ];
  useEffect(() => { void getPriceUploadHistory().then(setPriceUploadHistory).catch(() => undefined); }, []);
  useEffect(() => {
    if (!priceUploadId) return undefined;
    let active = true;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const result = await getPriceUploadStatus(priceUploadId);
        if (!active) return;
        const payload = (result.data ?? {}) as Record<string, unknown>;
        setPriceUploadStatus(`${String(result.label ?? "Статус неизвестен")} · ${String(payload.successGoodsNumber ?? "—")} из ${String(payload.overAllGoodsNumber ?? "—")}`);
        setPriceResults(Array.isArray(result.items) ? result.items : []);
        if (result.terminal === true) { setPriceUploadId(null); return; }
      } catch (error) {
        if (!active) return;
        setPriceUploadStatus(`${error instanceof Error ? error.message : "Статус недоступен"}. Проверка будет повторена; цены повторно не отправляются.`);
      }
      if (active) timer = window.setTimeout(() => void poll(), 10000);
    };
    void poll();
    return () => { active = false; window.clearTimeout(timer); };
  }, [priceUploadId]);
  const submitPrices = async () => {
    if (priceSubmitLock.current || priceUploadId) return;
    const goods = Object.entries(priceDrafts).map(([nmID, value]) => ({ nmID: Number(nmID), price: Number(value.price), discount: Number(value.discount) }));
    if (!goods.length) { setPriceMessage("Изменений цен нет"); return; }
    if (goods.some((row) => !Number.isInteger(row.price) || row.price <= 0 || !Number.isInteger(row.discount) || row.discount < 0 || row.discount > 99)) {
      setPriceMessage("Цена должна быть положительным целым числом, скидка — целым числом от 0 до 99."); return;
    }
    if (!await askConfirm(`Отправить изменения в WB для ${goods.length} SKU? Перед отправкой проверьте предпросмотр цен в списке.`)) return;
    priceSubmitLock.current = true;
    setPriceSubmitting(true);
    try {
      const result = await uploadProductPrices(goods, goods.flatMap((row) => { const old = (prices ?? []).find((item) => item.nmId === row.nmID); return old ? [{ nmID: row.nmID, vendorCode: old.vendorCode, price: old.price, discount: old.discount }] : []; }));
      const data = result.data as Record<string, unknown> | undefined;
      const uploadId = Number(data?.id ?? data?.uploadID ?? 0);
      if (!uploadId || result.error) throw new Error(String(result.errorText || "WB не вернул номер загрузки. Результат не подтверждён; проверьте историю в ЛК WB перед повторной отправкой."));
      setPriceUploadId(uploadId);
      setPriceUploadStatus("В обработке");
      setPriceResults([]);
      setPriceMessage(`Загрузка WB №${uploadId}: ожидаем результат обработки`);
      setPriceUploadHistory(await getPriceUploadHistory().catch(() => priceUploadHistory));
      setPriceDrafts({});
    } catch (error) { setPriceMessage(error instanceof Error ? error.message : "Не удалось загрузить цены"); }
    finally { priceSubmitLock.current = false; setPriceSubmitting(false); }
  };
  const qualityActions = {
    onRefresh: () => void onRefresh(),
    onAnswerFeedback: async (id: string) => { const text = await askText("Ответ на отзыв", "Спасибо за обратную связь!"); if (!text) return; try { await answerCustomerFeedback(id, text); await notifyUser("Ответ отправлен в WB"); } catch (error) { await notifyUser(error instanceof Error ? error.message : "Ответ не отправлен"); } },
    onAnswerQuestion: async (id: string) => { const text = await askText("Ответ на вопрос", "Спасибо за вопрос! Сейчас уточним информацию."); if (!text) return; try { await answerCustomerQuestion(id, text); await notifyUser("Ответ отправлен в WB"); } catch (error) { await notifyUser(error instanceof Error ? error.message : "Ответ не отправлен"); } },
    onReplyChat: async (replySign: string) => { if (!replySign) return; const text = await askText("Сообщение покупателю"); if (!text?.trim()) return; try { await sendCustomerChatMessage(replySign, text); await notifyUser("Сообщение отправлено в WB"); } catch (error) { await notifyUser(error instanceof Error ? error.message : "Сообщение не отправлено"); } },
    onReturn: async (id: string, decision: "approve" | "reject") => { if (!await askConfirm(decision === "approve" ? "Одобрить возврат и отправить решение в WB?" : "Отклонить возврат? Решение будет отправлено в WB.")) return; try { await processCustomerReturn(id, decision); await notifyUser(decision === "approve" ? "Возврат одобрен" : "Возврат отклонён"); } catch (error) { await notifyUser(error instanceof Error ? error.message : "Возврат не обработан"); } },
  };
  return (
    <div className="workspace-view">
      {(customerQuality?.errors?.length || productCardErrors.length || productPriceErrors.length) ? <p className="data-banner stale" role="status">{wbAvailabilityMessage([...(customerQuality?.errors ?? []), ...productCardErrors, ...productPriceErrors])}</p> : null}
      <WorkspaceTabs tabs={productTabs} ariaLabel="Разделы товаров" storageKey="mh.tab.products" target={target} targetMap={productTargetMap} panels={{
        products: <>
          <section className="hero-strip finance-hero"><div><div className="eyebrow"><PackageCheck size={16} /> товары</div><h2>{products.length} SKU в экономике</h2><p>Выручка, выкуп, реклама, запас и чистая маржинальность по каждому артикулу WB.</p></div></section>
          <MetricGrid metrics={[{ label: "Выручка", value: `${Math.round(revenue).toLocaleString("ru-RU")} ₽`, delta: "за период", detail: "по товарам", tone: "good", change: changes[0] }, { label: "Расчётная маржинальность", value: `${netMarginPct.toFixed(1)}%`, delta: "по учтённым затратам", detail: "себестоимость и комиссия WB учтены", tone: netMarginPct >= 0 ? "good" : "warn", change: changes[1] }, { label: "Риск запаса", value: String(atRisk), delta: "SKU", detail: "меньше 7 дней", tone: atRisk ? "warn" : "good", change: changes[2] }, { label: "Артикулы", value: String(products.length), delta: "в выборке", detail: "данные WB", tone: "info", change: changes[3] }]} onMetricClick={(metric) => setQuery(metric.label === "Артикулы" ? "" : metric.label)} />
          <p className="form-hint">ДРР — доля рекламных расходов в выручке: значение выше 100% означает, что расходы на рекламу превышают выручку. Отрицательная маржа означает превышение учтённых затрат над выручкой; при небольшой выручке процент может быть большим.</p>
          <section className="work-section"><SectionHeader kicker="Экономика" title="Товары по артикулам"><DashboardPeriodControl period={period} setPeriod={setPeriod} /></SectionHeader><label className="search-box product-search"><Search size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Артикул или товар" type="search" /></label><ProductGroupFilter groups={productGroups.groups} value={groupId} onChange={setGroupId} /><ProductGroupsPanel products={products} groups={productGroups.groups} error={productGroups.error} onChanged={productGroups.reload} />{visible.length ? <ProductEconomicsList items={visible} onOpen={onOpenProduct} trend={trend} periodDays={periodLengthDays(period)} /> : <p className="plan-empty">По этому запросу товары не найдены</p>}</section>
        </>,
        analytics: <>
          <ProductsHeatmap period={period} />
          <RnpPlanFact products={products} />
        </>,
        content: <>
          <ProductCardsPanel cards={productCards} errors={productCardErrors} onEdit={setEditingCard} />
          <ShopQualityPanel data={customerQuality} actions={qualityActions} />
        </>,
        prices: <>
          <PriceAdvisorPanel period={period} prices={prices ?? []} onApply={(items) => setPriceDrafts((rows) => ({ ...rows, ...Object.fromEntries(items.map((i) => [i.nmId, { price: i.price, discount: i.discount }])) }))} />
          <ProductPricesPanel prices={prices ?? []} query={query} drafts={priceDrafts} onDraft={(nmId, draft) => setPriceDrafts((rows) => ({ ...rows, [nmId]: draft }))}
            onSubmit={() => void submitPrices()} busy={priceSubmitting || !!priceUploadId} message={priceMessage} uploadStatus={priceUploadStatus}
            results={priceResults} history={priceUploadHistory} hasErrors={productPriceErrors.length > 0} />
        </>,
      }} />
      {editingCard ? <ProductCardEditor card={editingCard} onClose={() => setEditingCard(null)} onSaved={async () => { setEditingCard(null); await onRefresh(); }} /> : null}
    </div>
  );
}

function ProductCardEditor({
  card,
  onClose,
  onSaved,
}: {
  card: ProductCard;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const dialogRef=useDialogFocus(onClose);
  const [title, setTitle] = useState(card.title);
  const [description, setDescription] = useState(card.description ?? "");
  const [brand, setBrand] = useState(card.brand);
  const [mediaText, setMediaText] = useState(card.photos.join("\n"));
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const save = async () => {
    if (!confirmed) { setMessage("Подтвердите отправку изменений в WB."); return; }
    const urls = mediaText.split(/\r?\n/).map((url) => url.trim()).filter(Boolean);
    if (urls.some((url) => !/^https?:\/\//i.test(url))) { setMessage("Каждая ссылка на медиа должна начинаться с http:// или https://."); return; }
    setBusy(true); setMessage("");
    try {
      await updateProductCard(card.nmId, { title: title.trim(), description: description.trim(), brand: brand.trim() });
      if (urls.length) await saveProductCardMedia(card.nmId, urls);
      await onSaved();
    } catch (error) { setMessage(error instanceof Error ? error.message : "Изменения не отправлены"); }
    finally { setBusy(false); }
  };
  return <div className="drawer-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section className="plan-drawer operation-dialog product-card-editor" role="dialog" aria-modal="true" ref={dialogRef} aria-labelledby="product-card-editor-title">
      <div className="drawer-header"><div><span className="eyebrow">Контент WB · редактирование</span><h2 id="product-card-editor-title">{card.title}</h2><p>WB {card.nmId} · {card.vendorCode || "без артикула продавца"}</p></div><button className="icon-button" type="button" aria-label="Закрыть" onClick={onClose} disabled={busy}><X size={18} /></button></div>
      <div className="form-grid"><label>Название<input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={255} /></label><label>Бренд<input value={brand} onChange={(event) => setBrand(event.target.value)} maxLength={255} /></label><label className="full-width">Описание<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={5} maxLength={5000} /></label><label className="full-width">Медиафайлы, по одной ссылке на строку<textarea value={mediaText} onChange={(event) => setMediaText(event.target.value)} rows={5} /></label></div>
      <label className="form-checkbox"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /> Я подтверждаю отправку изменений и замену списка медиафайлов в WB</label>
      {message ? <p className="operation-error" role="status">{message}</p> : null}
      <div className="dialog-actions"><button className="ghost-button" type="button" onClick={onClose} disabled={busy}>Отмена</button><button className="primary-button" type="button" onClick={() => void save()} disabled={busy || !confirmed}>{busy ? "Отправка…" : "Сохранить в WB"}</button></div>
    </section>
  </div>;
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

export function ControlView({
  data,
  target,
  onNavigate,
  onOpenCheck,
  onOpenFifo,
}: {
  data: DataQualityData | null;
  target?: string | null;
  onNavigate: (page: PageId, target?: string) => void;
  onOpenCheck: (row: Record<string, unknown>) => void;
  onOpenFifo: () => void;
}) {
  if (!data) return <div className="workspace-view"><section className="work-section"><p className="plan-empty">Проверки качества данных загружаются.</p></section></div>;
  const summary = data.summary;
  const checks = [...data.checks].sort((a,b) => checkPriority(a.Статус)-checkPriority(b.Статус));
  const actionableChecks = checks.filter(row => checkPriority(row.Статус) !== 3);
  const readyChecks = checks.filter(row => checkPriority(row.Статус) === 3);
  const score = Number(summary.score ?? 0);
  const tone: Tone = Number(summary.critical ?? 0) > 0 ? "danger" : Number(summary.warnings ?? 0) > 0 ? "warn" : "good";
  const checkTone = (status: string): Tone => checkPriority(status) === 0 ? "danger" : checkPriority(status) === 1 ? "warn" : checkPriority(status) === 3 ? "good" : "info";
  const openCheck = (row: Record<string, unknown>) => onOpenCheck(row);
  return <div className="workspace-view"><section className="hero-strip"><div><div className="eyebrow"><ClipboardCheck size={16} /> контроль</div><h2>{score.toFixed(0)}% готовности данных</h2><p>{String(data.fifo_guard.reason ?? "Проверка полноты и надёжности расчётов.")}</p></div><div className="hero-actions"><button className="secondary-button" type="button" onClick={onOpenFifo}><ClipboardCheck size={17} /><span>Открыть FIFO-сверку</span></button></div></section><MetricGrid metrics={[{label:"Критично",value:String(summary.critical ?? 0),delta:"блокирует",detail:"требует действий",tone:Number(summary.critical ?? 0) ? "danger" : "good"},{label:"Внимание",value:String(summary.warnings ?? 0),delta:"с оговорками",detail:"можно проверить",tone:"warn"},{label:"Готово",value:String(summary.ready ?? 0),delta:"проверок",detail:"можно использовать",tone:"good"},{label:"FIFO",value:String(data.fifo_guard.status ?? "—"),delta:"защита",detail:cabinetText(String(data.fifo_guard.guard_mode ?? "")),tone}]} onMetricClick={(metric) => metric.label === "FIFO" ? onOpenFifo() : onNavigate("control")} /><p className="form-hint">Нормы материала и себестоимость можно проверить в <button className="ghost-button" type="button" onClick={() => onNavigate("settings")}>настройках производства</button>.</p><WorkspaceTabs ariaLabel="Разделы страницы Контроль" storageKey="mh.tab.control" tabs={[{id:"attention",label:`Требует внимания · ${actionableChecks.length}`},{id:"ok",label:`В порядке · ${readyChecks.length}`}]} target={target} targetMap={{"control-checks":"attention"}} initial={actionableChecks.length ? "attention" : "ok"} panels={{attention:<section className="work-section" id="control-checks"><SectionHeader kicker="Проверки" title="Надёжность расчётов"><ClipboardCheck size={18} /></SectionHeader>{actionableChecks.length ? <div className="data-quality-list">{actionableChecks.map((row,index) => { const status = String(row.Статус ?? ""); return <button className="data-quality-card detail-trigger" key={`${String(row.Проверка ?? "check")}-${index}`} type="button" onClick={() => openCheck(row)}><div className="data-quality-heading"><div><span>{cabinetText(String(row.Модуль ?? "Контроль"))}</span><strong>{cabinetText(String(row.Проверка ?? ""))}</strong></div><StatusPill tone={checkTone(status)}>{status}</StatusPill></div><p><span>Детали</span>{String(row.Детали ?? "—")}</p><p><span>Рекомендуемое действие</span>{String(row["Что сделать"] ?? "—")}</p></button>; })}</div> : <p className="plan-empty">{checks.length ? "Проверок, требующих внимания, нет" : "Проверок пока нет"}</p>}</section>,ok:<section className="work-section"><SectionHeader kicker="Проверки" title="Надёжность расчётов"><ClipboardCheck size={18} /></SectionHeader>{readyChecks.length ? <div className="data-quality-list">{readyChecks.map((row,index) => <button className="data-quality-card detail-trigger" type="button" key={index} onClick={() => openCheck(row)}><strong>{cabinetText(String(row.Проверка ?? ""))}</strong><p>{String(row.Детали ?? "—")}</p></button>)}</div> : <p className="plan-empty">Проверок в порядке пока нет</p>}</section>}} /></div>;
}

function SettingsViewV2({ onSynced, migration, sectionTarget }: { onSynced: () => Promise<void>; migration: React.ReactNode; sectionTarget?: string | null }) {
  const [requestedSection, setRequestedSection] = useState(sectionTarget);
  useEffect(() => { if (sectionTarget) setRequestedSection(sectionTarget); }, [sectionTarget]);
  const [settings, setSettings] = useState<TenantSettings | null>(null);
  const [backups, setBackups] = useState<Array<Record<string, unknown>>>([]);
  const [syncRuns, setSyncRuns] = useState<TenantSyncRun[]>([]);
  const [productCosts, setProductCosts] = useState<ProductCostSetting[]>([]);
  const [manualExpenses, setManualExpenses] = useState<ManualExpense[]>([]);
  const [managementEconomics, setManagementEconomics] = useState<ManagementEconomics | null>(null);
  const [productionNorms, setProductionNorms] = useState<ProductionNorm[]>([]);
  const [materialInventory, setMaterialInventory] = useState<MaterialInventory[]>([]);
  const [materialFifo, setMaterialFifo] = useState<MaterialFifoRow[]>([]);
  const [openingRate, setOpeningRate] = useState(0.01);
  const [costQuery, setCostQuery] = useState("");
  const [editingCost, setEditingCost] = useState<ProductCostSetting | null>(null);
  const [editingNorm, setEditingNorm] = useState<ProductionNorm | null>(null);
  const [wbToken, setWbToken] = useState("");
  const [wbScopes, setWbScopes] = useState<string[]>([]);
  const [wbMissingScopes, setWbMissingScopes] = useState<string[]>([]);
  const [wbScopeMetadataAvailable, setWbScopeMetadataAvailable] = useState(false);
  const [wbReadOnly, setWbReadOnly] = useState(false);
  const [wbExpiresAt, setWbExpiresAt] = useState<number | null>(null);
  const [ozonClientId, setOzonClientId] = useState("");
  const [ozonApiKey, setOzonApiKey] = useState("");
  const [message, setMessage] = useState("");
  const [historyRefreshKey, setHistoryRefreshKey] = useState(0);
  const [busy, setBusy] = useState<"settings" | "backup" | "sync" | "cost" | "material" | "expense" | "wb-token" | null>(null);

  const load = async () => {
    try {
      const [nextSettings, backupData, syncData, costData, normsData, materialData, wbStatus, expenseData] = await Promise.all([getTenantSettings(), getTenantBackups(), getTenantSyncHistory(), getProductCostSettings(), getProductionNorms(), getMaterialInventory(), getWBTokenStatus(), getManualExpenses().catch(() => ({ items: [] as ManualExpense[] }))]);
      setSettings(nextSettings);
      setBackups(backupData.backups);
      setSyncRuns(syncData.runs);
      setProductCosts(costData.items);
      setManualExpenses(expenseData.items);
      setProductionNorms(normsData.items);
      setMaterialInventory(materialData.items);
      setMaterialFifo(materialData.fifo);
      setOpeningRate(materialData.opening_rate_rub_m);
      setWbScopes(wbStatus.scopes);
      setWbMissingScopes(wbStatus.missing_scopes);
      setWbScopeMetadataAvailable(Boolean(wbStatus.scope_metadata_available));
      setWbReadOnly(Boolean(wbStatus.read_only));
      setWbExpiresAt(wbStatus.expires_at ?? null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось загрузить параметры кабинета");
    }
  };

  useEffect(() => { void load(); }, []);

  const saveSettings = async () => {
    if (!settings) return;
    setBusy("settings");
    try {
      setSettings(await updateTenantSettings(settings));
      setMessage("Параметры сохранены");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось сохранить параметры");
    } finally {
      setBusy(null);
    }
  };

  const saveWBToken = async () => {
    if (!wbToken.trim()) { setMessage("Введите WB-токен"); return; }
    setBusy("wb-token");
    try {
      const result = await connectWBToken(wbToken);
      setWbToken("");
      setWbScopes(result.scopes);
      setWbMissingScopes(result.missing_scopes);
      setWbScopeMetadataAvailable(Boolean(result.scope_metadata_available));
      setWbReadOnly(Boolean(result.read_only));
      setWbExpiresAt(result.expires_at ?? null);
      setSettings((current) => current ? { ...current, wb_connected: result.connected } : current);
      setMessage("WB подключён. Запустите первую синхронизацию ниже.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось подключить WB"); }
    finally { setBusy(null); }
  };

  const removeWBToken = async () => {
    setBusy("wb-token");
    try {
      await disconnectWBToken();
      setSettings((current) => current ? { ...current, wb_connected: false } : current);
      setWbScopes([]); setWbMissingScopes([]);
      setWbScopeMetadataAvailable(false); setWbReadOnly(false); setWbExpiresAt(null);
      setMessage("WB отключён");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось отключить WB"); }
    finally { setBusy(null); }
  };

  const saveOzon = async () => {
    if (!ozonClientId.trim() || !ozonApiKey.trim()) { setMessage("Введите Client-Id и Api-Key Ozon"); return; }
    setBusy("wb-token");
    try {
      await connectOzonCredentials(ozonClientId, ozonApiKey);
      setOzonClientId(""); setOzonApiKey("");
      setSettings((current) => current ? { ...current, ozon_connected: true } : current);
      setMessage("Ozon подключён");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось подключить Ozon"); }
    finally { setBusy(null); }
  };

  const removeOzon = async () => {
    setBusy("wb-token");
    try { await disconnectOzonCredentials(); setSettings((current) => current ? { ...current, ozon_connected: false } : current); setMessage("Ozon отключён"); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось отключить Ozon"); }
    finally { setBusy(null); }
  };

  const createBackup = async () => {
    setBusy("backup");
    try {
      const result = await createTenantBackup();
      setMessage(`Создана резервная копия: ${result.name}`);
      const backupData = await getTenantBackups();
      setBackups(backupData.backups);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось создать резервную копию");
    } finally {
      setBusy(null);
    }
  };

  const monitorSync = async () => {
    try {
      for (let attempt = 0; attempt < 60; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 5000));
        const status = await getTenantSyncStatus();
        if (status.status === "running") continue;
        const [syncData, costData] = await Promise.all([getTenantSyncHistory(), getProductCostSettings()]);
        setSyncRuns(syncData.runs);
        setProductCosts(costData.items);
        await onSynced();
        setMessage(status.status === "success" ? "Синхронизация завершена, данные обновлены." : status.status === "partial" ? "Данные обновлены частично. Подробности — в журнале синхронизации." : (status.details || "Синхронизация завершилась с ошибкой"));
        return;
      }
      setMessage("Синхронизация продолжается в фоне. История обновится после завершения.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось обновить статус синхронизации");
    }
  };

  const startSync = async (scope: "all" | "ads" | "finance" = "all", days = 90) => {
    setBusy("sync");
    try {
      await startTenantSync(scope, days);
      setMessage("Синхронизация WB запущена. Можно продолжать работу, статус появится в журнале.");
      await monitorSync();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось запустить синхронизацию");
    } finally {
      setBusy(null);
    }
  };

  const saveCost = async (item: ProductCostSetting, reason?: string) => {
    setBusy("cost");
    try {
      await updateProductCostSettings([item], reason);
      setMessage(`Себестоимость для «${item.product_name || item.supplier_article || item.nm_id}» сохранена`);
      setHistoryRefreshKey((value) => value + 1);
      const costData = await getProductCostSettings();
      setProductCosts(costData.items);
      return true;
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось сохранить себестоимость");
      return false;
    } finally {
      setBusy(null);
    }
  };

  const saveExpense = async (form: HTMLFormElement) => {
    const data = new FormData(form);
    const nmId = Number(data.get("nm_id"));
    if (!nmId) { setMessage("Выберите товар"); return; }
    setBusy("expense");
    try {
      await createManualExpense({ nm_id: nmId, article: String(data.get("article") || ""), amount_rub: Number(data.get("amount_rub") || 0), allocation_basis: String(data.get("allocation_basis") || "per_unit") as ManualExpense["allocation_basis"], source: "manual", period_start: String(data.get("period_start") || ""), period_end: String(data.get("period_end") || ""), note: String(data.get("note") || ""), include_in_management_result: Boolean(data.get("include_in_management_result")) });
      setManualExpenses((await getManualExpenses()).items);
      form.reset();
      setMessage("Расход сохранён");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось сохранить расход"); }
    finally { setBusy(null); }
  };

  const removeExpense = async (id: number) => {
    setBusy("expense");
    try { await deleteManualExpense(id); setManualExpenses((rows) => rows.filter((row) => row.id !== id)); setMessage("Расход удалён"); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось удалить расход"); }
    finally { setBusy(null); }
  };

  const toggleExpenseInclusion = async (item: ManualExpense) => {
    const included = !item.include_in_management_result;
    const confirmation = included
      ? "Подтвердите: эта сумма отсутствует в данных WB, расходах WB-рекламы и себестоимости товара. Включить её в управленческий результат?"
      : "Исключить этот ручной расход из управленческого результата?";
    if (!window.confirm(confirmation)) return;
    setBusy("expense");
    try {
      await updateManualExpenseInclusion(item.id, included);
      setManualExpenses((rows) => rows.map((row) => row.id === item.id ? { ...row, include_in_management_result: included } : row));
      setMessage(included ? "Расход включён в управленческий результат" : "Расход оставлен только в реестре");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось изменить учёт расхода"); }
    finally { setBusy(null); }
  };

  const calculateManagement = async (item: ManualExpense) => {
    setBusy("expense");
    try { setManagementEconomics(await getManagementEconomics(item.nm_id, item.period_start, item.period_end)); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось рассчитать управленческий результат"); }
    finally { setBusy(null); }
  };

  const saveNorm = async (item: ProductionNorm, reason?: string) => {
    setBusy("cost");
    try {
      await updateProductionNorms([{ nm_id: item.nm_id, material_per_unit: item.material_per_unit, min_batch: item.min_batch, pack_size: item.pack_size, enabled: item.enabled }], reason);
      setProductionNorms((rows) => rows.map((row) => row.nm_id === item.nm_id ? item : row));
      setHistoryRefreshKey((value) => value + 1);
      setMessage(`Норма для «${item.supplier_article || item.nm_id}» сохранена`);
      setEditingNorm(null);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось сохранить норму"); }
    finally { setBusy(null); }
  };

  const saveMaterials = async () => {
    setBusy("material");
    try {
      const result = await updateMaterialInventory(materialInventory);
      setMaterialInventory(result.items);
      setMaterialFifo(result.fifo);
      setOpeningRate(result.opening_rate_rub_m);
      setMessage("Физические остатки сырья сохранены. Теперь можно инициализировать FIFO.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось сохранить остатки сырья"); }
    finally { setBusy(null); }
  };

  const initializeMaterials = async () => {
    if (!await askConfirm("Инициализировать FIFO по подтверждённым физическим остаткам сырья? Слои будут созданы с указанной ставкой.")) return;
    setBusy("material");
    try {
      const result = await initializeMaterialFifo(openingRate);
      setMaterialFifo(result.fifo);
      setOpeningRate(result.opening_rate_rub_m);
      const warnings = result.result.warnings?.length ? ` Предупреждений: ${result.result.warnings.length}.` : "";
      setMessage(`FIFO сырья обновлён: создано слоёв ${result.result.created}, учтено количество ${Number(result.result.meters).toLocaleString("ru-RU")}.${warnings}`);
      await onSynced();
    } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось инициализировать FIFO сырья"); }
    finally { setBusy(null); }
  };

  if (!settings) return <div className="workspace-view"><section className="work-section"><p className="plan-empty">{message || "Загружаем параметры кабинета."}</p></section></div>;

  return (
    <div className="workspace-view">
      <section className="hero-strip">
        <div>
          <div className="eyebrow"><SlidersHorizontal size={16} /> настройки</div>
          <h2>Подключения и обслуживание</h2>
          <p>Токены остаются в защищённом хранилище кабинета. Резервные копии не содержат ключей доступа.</p>
        </div>
      </section>
      <OnboardingPanel connected={settings.wb_connected} runs={syncRuns} costs={productCosts}
        days={settings.initial_history_days} busy={busy !== null} token={wbToken}
        onTokenChange={setWbToken} onConnect={() => void saveWBToken()}
        onDaysChange={(days) => setSettings({ ...settings, initial_history_days: days })}
        onSync={() => void startSync("all", settings.initial_history_days)}
        onEditCost={(item) => setEditingCost({ ...item })} />
      <ProductFeedbackLink />
      {message ? <p className="operation-error" role="status">{message}</p> : null}
      <SettingsWorkspace sectionTarget={requestedSection}
        connections={<>      <div className="settings-connections-grid">
        <section className="work-section">
          <SectionHeader kicker="Обновление" title="Интервалы работы"><SlidersHorizontal size={18} /></SectionHeader>
          <div className="settings-form settings-intervals">
            <label className="operation-field"><span>Синхронизация WB, минут</span><input min="15" max="1440" type="number" value={settings.sync_interval_minutes} onChange={(event) => setSettings({ ...settings, sync_interval_minutes: Number(event.target.value) })} /></label>
            <label className="operation-field"><span>История при первом запуске, дней</span><input min="7" max="90" type="number" value={settings.initial_history_days} onChange={(event) => setSettings({ ...settings, initial_history_days: Number(event.target.value) })} /></label>
            <label className="operation-field"><span>Обновление рекомендаций, минут</span><input min="15" max="1440" type="number" value={settings.agent_interval_minutes} onChange={(event) => setSettings({ ...settings, agent_interval_minutes: Number(event.target.value) })} /></label>
            <button className="primary-button" disabled={busy !== null} type="button" onClick={() => void saveSettings()}><CheckCircle2 size={17} /><span>{busy === "settings" ? "Сохраняем" : "Сохранить параметры"}</span></button>
          </div>
        </section>
        <div className="settings-connection-panel">
          <section className="panel">
            <SectionHeader compact kicker="Подключения" title="Статус API"><ShieldCheck size={20} /></SectionHeader>
            {!settings.wb_connected ? <div className="settings-form connection-form" role="group" aria-label="Подключение Wildberries"><p className="form-hint">Подключите WB-токен, чтобы загрузить продажи, остатки, финансы и отчёты.</p><label className="operation-field"><span>WB API-токен</span><input autoComplete="off" type="password" value={wbToken} onChange={(event) => setWbToken(event.target.value)} placeholder="Вставьте токен WB" /></label><button className="primary-button" disabled={busy !== null} type="button" onClick={() => void saveWBToken()}>{busy === "wb-token" ? "Проверяем" : "Проверить и сохранить"}</button></div> : null}
            <div className="automation-list">
              <AutomationRow item={{ title: "Wildberries API", detail: "Данные и операции кабинета", status: settings.wb_connected ? "подключён" : "не подключён", tone: settings.wb_connected ? "good" : "warn" }} />
              <AutomationRow item={{ title: "Ozon Seller API", detail: "Используется агентом цен", status: settings.ozon_connected ? "подключён" : "не подключён", tone: settings.ozon_connected ? "good" : "info" }} />
            </div>
            {!settings.ozon_connected ? <div className="settings-form connection-form" role="group" aria-label="Подключение Ozon"><p className="form-hint">Подключите Ozon для ценовых рекомендаций.</p><label className="operation-field"><span>Client-Id</span><input autoComplete="off" value={ozonClientId} onChange={(event) => setOzonClientId(event.target.value)} /></label><label className="operation-field"><span>Api-Key</span><input autoComplete="off" type="password" value={ozonApiKey} onChange={(event) => setOzonApiKey(event.target.value)} /></label><button className="secondary-button" disabled={busy !== null} type="button" onClick={() => void saveOzon()}>{busy === "wb-token" ? "Сохраняем" : "Сохранить Ozon"}</button></div> : <button className="ghost-button" disabled={busy !== null} type="button" onClick={() => void removeOzon()}>Отключить Ozon</button>}
            {settings.wb_connected ? <div className="connection-actions"><span className="form-hint">Токен хранится зашифрованно и не показывается повторно.</span><button className="ghost-button" disabled={busy !== null} type="button" onClick={() => void removeWBToken()}>Отключить WB</button></div> : null}
            {wbScopes.length || wbMissingScopes.length ? <p className="form-hint">Проверено: {wbScopes.join(", ") || "—"}. Для полного функционала добавьте категории: {wbMissingScopes.join(", ")}.</p> : null}
            {wbReadOnly ? <p className="form-hint warning-text">Токен WB доступен только для чтения: операции изменения и печати в кабинете могут быть недоступны.</p> : null}
            {wbScopeMetadataAvailable && wbExpiresAt ? <p className="form-hint">Срок действия токена: {new Date(wbExpiresAt * 1000).toLocaleString("ru-RU")}</p> : null}
            {settings.wb_connected && !wbScopeMetadataAvailable ? <p className="form-hint">WB не передал карту категорий для сохранённого токена. Проверена категория «Статистика»; права остальных разделов уточняются при обращении к ним.</p> : null}
          </section>
        </div>
      </div>
</>}
        data={<>{migration}      <section className="work-section">
        <SectionHeader kicker="WB API" title="Синхронизация и журнал"><RefreshCw size={18} /></SectionHeader>
        <div className="operation-actions">
          <p className="plan-empty">Обновляет продажи, остатки, финансы и рекламу. Результат каждого запуска — в журнале ниже.</p>
          <button className="secondary-button" disabled={busy !== null || !settings.wb_connected} type="button" onClick={() => void startSync()}><RefreshCw size={17} />{busy === "sync" ? "Синхронизируем" : "Синхронизировать сейчас"}</button>
          <button className="ghost-button" disabled={busy !== null || !settings.wb_connected} type="button" onClick={() => void startSync("finance", 90)}>Обновить финансовый отчёт WB (90 дней)</button>
        </div>
        <SyncHistory runs={syncRuns} />
        <button className="ghost-button" disabled={busy !== null || !settings.wb_connected} type="button" onClick={() => void startSync("ads")}>Повторить только рекламу</button>
        <RuntimeStatus />
      </section>
      <SettingsChangeHistory refreshKey={historyRefreshKey} /></>}
        production={<>      <section className="work-section" id="material-inventory">
        <SectionHeader kicker="Производство" title="Остатки сырья и сверка FIFO"><ClipboardCheck size={18} /></SectionHeader>
        <div className="operation-actions">
          <p className="plan-empty">Сначала укажите фактический остаток по каждому материалу и включите «Подтверждён». После проверки создайте FIFO-слои стартового сырья.</p>
          <div className="material-fifo-actions"><label className="operation-field"><span>Резервная ставка сырья, ₽/ед.</span><input min="0.01" step="0.01" type="number" value={openingRate} onChange={(event) => setOpeningRate(Number(event.target.value))} /></label><button className="secondary-button" disabled={busy !== null || !materialInventory.length} type="button" onClick={() => void saveMaterials()}>{busy === "material" ? "Сохраняем" : "Сохранить остатки"}</button><button className="primary-button" disabled={busy !== null || !materialInventory.length} type="button" onClick={() => void initializeMaterials()}>{busy === "material" ? "Обновляем FIFO" : "Инициализировать FIFO"}</button></div>
        </div>
        {materialInventory.length ? <div className="material-inventory-list">{materialInventory.map((item) => { const key = item.material_key || item.material_name.toLowerCase(); const fifo = materialFifo.find((row) => (row.material_key || row.material_name.toLowerCase()) === key); const update = (patch: Partial<MaterialInventory>) => setMaterialInventory((rows) => rows.map((row) => row.material_name === item.material_name ? { ...row, ...patch } : row)); return <div className="material-inventory-row" key={key}><div className="cost-setting-product"><strong>{item.material_name}</strong><span>{fifo ? `Физически ${Number(fifo.physical_meters).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} ${item.unit || "ед."} · FIFO ${Number(fifo.fifo_meters).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} ${item.unit || "ед."} · разница ${Number(fifo.difference_meters).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} ${item.unit || "ед."}` : "FIFO-слой ещё не создан"}</span></div><label className="compact-field"><span>Упаковки</span><input min="0" type="number" value={item.full_rolls} onChange={(event) => update({ full_rolls: Number(event.target.value) })} /></label><label className="compact-field"><span>Остаток, {item.unit || "ед."}</span><input min="0" step="0.1" type="number" value={item.partial_meters} onChange={(event) => update({ partial_meters: Number(event.target.value) })} /></label><label className="compact-field"><span>В упаковке, {item.unit || "ед."}</span><input min="0.1" step="0.1" type="number" value={item.roll_length} disabled={item.tracking_mode === "quantity"} onChange={(event) => update({ roll_length: Number(event.target.value) })} /></label><label className="compact-field"><span>Единица</span><input aria-label={`Единица ${item.material_name}`} maxLength={16} value={item.unit} onChange={(event) => update({ unit: event.target.value })} /></label><label className="compact-field"><span>Ставка, ₽/{item.unit || "ед."}</span><input type="number" min="0" step="0.01" value={item.opening_rate_rub} onChange={(event) => update({ opening_rate_rub: Number(event.target.value) })} /></label><label className="compact-field"><span>Учёт</span><select value={item.tracking_mode} onChange={(event) => update({ tracking_mode: event.target.value as MaterialInventory["tracking_mode"] })}><option value="packaged">Упаковки</option><option value="quantity">Количество</option></select></label><label className="checkbox-field material-known"><input type="checkbox" checked={Boolean(item.balance_known)} onChange={(event) => update({ balance_known: event.target.checked })} /> Подтверждён</label></div>; })}</div> : <p className="plan-empty">Материалы пока не загружены.</p>}
      </section>
      <section className="work-section" id="production-norms">
        <SectionHeader kicker="Производство" title="Нормы материала"><Factory size={18} /></SectionHeader>
        <div className="operation-actions">
          <p className="plan-empty">Расход указан на комплект. Эти нормы используются при расчёте производственного плана.</p>
        </div>
        {productionNorms.length ? <div className="production-norms-list">{productionNorms.map((item) => <div className="production-norm-row" key={item.nm_id}><div className="cost-setting-product"><strong>{item.product_name || "Товар без названия"}</strong><span>WB {item.nm_id} · {item.supplier_article}</span></div><div className="production-norm-values"><span>Расход <strong>{Number(item.material_per_unit).toLocaleString("ru-RU", { maximumFractionDigits: 3 })} {item.material_unit || "ед."}/компл.</strong></span><span>Партия <strong>{item.min_batch} компл.</strong></span><span>В комплекте <strong>{item.pack_size} шт.</strong></span></div><span className={item.enabled ? "section-tag" : "section-tag muted-tag"}>{item.enabled ? "Активно" : "Выключено"}</span><button className="icon-button" type="button" aria-label="Изменить норму" title="Изменить норму" onClick={() => setEditingNorm(item)}><Pencil size={17} /></button></div>)}</div> : <p className="plan-empty">Нормы производства пока не заданы.</p>}
      </section>
      <section className="work-section" id="product-costs">
        <SectionHeader kicker="Экономика" title="Себестоимость товаров"><WalletCards size={18} /></SectionHeader>
        <div className="operation-actions">
          <p className="plan-empty">Себестоимость одной продаваемой единицы WB. Она используется в расчёте маржи и прибыли; изменение не создаёт движения товаров и не меняет исторические FIFO-слои.</p>
          <label className="search-box product-search"><Search size={17} /><input value={costQuery} onChange={(event) => setCostQuery(event.target.value)} placeholder="Артикул или товар" type="search" /></label>
        </div>
        {productCosts.length ? <div className="cost-settings-list">{productCosts.filter((item) => `${item.product_name} ${item.supplier_article} ${item.nm_id}`.toLowerCase().includes(costQuery.trim().toLowerCase())).map((item) => <div className="cost-setting-row" key={item.nm_id}><div className="cost-setting-product"><strong>{item.product_name || "Товар без названия"}</strong><span>WB {item.nm_id}{item.supplier_article ? ` · ${item.supplier_article}` : ""}</span></div><div className="cost-setting-amount"><span>Себестоимость</span><strong>{Number(item.cost_per_wb_unit).toLocaleString("ru-RU")} ₽</strong></div><button className="icon-button cost-edit-button" disabled={busy !== null} type="button" aria-label={`Изменить себестоимость: ${item.product_name || item.nm_id}`} title="Изменить себестоимость" onClick={() => setEditingCost(item)}><Pencil size={17} /></button></div>)}</div> : <p className="plan-empty">Каталог товаров ещё не загружен. После синхронизации здесь появятся карточки WB.</p>}
      </section>
      <section className="work-section" id="manual-expenses">
        <SectionHeader kicker="Экономика" title="Дополнительные расходы"><WalletCards size={18} /></SectionHeader>
        <p className="plan-empty">Ручные суммы сохраняются отдельно. По умолчанию они не уменьшают результат: база уже включает себестоимость, расходы WB и данные WB-рекламы. Включайте строку только после проверки, что её нет в этих источниках.</p>
        <form className="settings-form" onSubmit={(event) => { event.preventDefault(); void saveExpense(event.currentTarget); }}>
          <label className="operation-field"><span>Товар</span>{productCosts.length ? <select name="nm_id" required defaultValue=""><option value="" disabled>Выберите товар</option>{productCosts.map((item) => <option key={item.nm_id} value={item.nm_id}>{item.product_name || item.supplier_article || item.nm_id} · WB {item.nm_id}</option>)}</select> : <input name="nm_id" required min="1" type="number" placeholder="Артикул WB" />}</label>
          <label className="operation-field"><span>Статья</span><select name="article" defaultValue="packaging_labor"><option value="purchase_production">Закупка / производство</option><option value="logistics_returns">Логистика и возвраты</option><option value="storage">Хранение</option><option value="acceptance">Приёмка</option><option value="advertising">Реклама</option><option value="packaging_labor">Упаковка и труд</option><option value="defects_losses">Брак и потери</option><option value="taxes">Налоги</option><option value="wb_commission">Комиссия WB</option></select></label>
          <label className="operation-field"><span>Сумма, ₽</span><input name="amount_rub" min="0" required step="0.01" type="number" /></label>
          <label className="operation-field"><span>База распределения</span><select name="allocation_basis" defaultValue="per_unit"><option value="per_unit">На единицу</option><option value="total">Итого за период</option><option value="revenue_pct">Процент выручки</option></select></label>
          <label className="operation-field"><span>Период от</span><input name="period_start" required type="date" /></label>
          <label className="operation-field"><span>Период до</span><input name="period_end" required type="date" /></label>
          <label className="operation-field"><span>Примечание</span><input name="note" placeholder="Источник или комментарий" /></label>
          <label className="checkbox-field"><input name="include_in_management_result" type="checkbox" /><span>Подтверждаю: сумма отсутствует в данных WB, WB-рекламе и себестоимости; включить её в управленческий результат</span></label>
          <button className="primary-button" disabled={busy !== null} type="submit">{busy === "expense" ? "Сохраняем" : "Добавить расход"}</button>
        </form>
        {manualExpenses.length ? <div className="cost-settings-list">{manualExpenses.map((item) => <div className="cost-setting-row" key={item.id}><div className="cost-setting-product"><strong>{item.article}</strong><span>WB {item.nm_id} · {item.period_start} — {item.period_end} · источник: {item.source === "manual" ? "вручную" : "импорт"} · {item.article === "wb_commission" ? "исключён: комиссия уже в выплате WB" : item.include_in_management_result ? "включён в результат после подтверждения" : "исключён: требуется проверить отсутствие в WB, рекламе и себестоимости"}</span></div><div className="cost-setting-amount"><span>{item.allocation_basis}</span><strong>{Number(item.amount_rub).toLocaleString("ru-RU")} ₽</strong></div>{item.article !== "wb_commission" ? <button className="ghost-button" disabled={busy !== null} type="button" onClick={() => void toggleExpenseInclusion(item)}>{item.include_in_management_result ? "Исключить из результата" : "Включить в результат"}</button> : <span className="muted-tag">Комиссия WB уже учтена</span>}<button className="ghost-button" disabled={busy !== null} type="button" onClick={() => void calculateManagement(item)}>Рассчитать</button><button className="ghost-button" disabled={busy !== null} type="button" onClick={() => void removeExpense(item.id)}>Удалить</button></div>)}</div> : <p className="plan-empty">Ручных расходов пока нет.</p>}
        <ManualStoreExpenseManager />
        <ExpenseCoveragePanel products={productCosts} />
        <TaxSettingsPanel />
        <a className="secondary-button" href="?view=finance#tax-reconciliation">Финансы → Налоги: расчёт, сверка и история</a>
        {managementEconomics ? <div className="operation-actions"><strong>Управленческий результат · WB {managementEconomics.nm_id}</strong>{managementEconomics.data_status !== "ok" ? <span className="warning-text">По этому SKU нет данных продаж за выбранный период.</span> : null}<span>Прибыль по учётным данным: {managementEconomics.accounting_profit_rub.toLocaleString("ru-RU")} ₽ → после подтверждённых дополнительных расходов: {managementEconomics.management_profit_rub.toLocaleString("ru-RU")} ₽</span><span>Включено вручную: {managementEconomics.manual_expenses_rub.toLocaleString("ru-RU")} ₽ · исключено без подтверждения: {managementEconomics.excluded_manual_expenses_rub.toLocaleString("ru-RU")} ₽ · комиссия WB всегда исключена из повторного вычитания.</span>{managementEconomics.items.filter((item) => item.excluded_from_result).map((item) => <span className="muted-tag" key={item.id}>{item.article}: {item.exclusion_reason}</span>)}</div> : null}
      </section>
</>}
        backups={<>      <section className="work-section">
        <SectionHeader kicker="Обслуживание" title="Резервные копии"><ShieldCheck size={18} /></SectionHeader>
        <div className="operation-actions">
          <p className="plan-empty">Копируется база кабинета и настройки без токенов. Хранится до 14 последних копий.</p>
          <button className="secondary-button" disabled={busy !== null} type="button" onClick={() => void createBackup()}><Download size={17} />{busy === "backup" ? "Создаём копию" : "Создать резервную копию"}</button>
        </div>
        <BackupHistory backups={backups} />
      </section>
</>}
      />
      {editingCost ? <ProductCostDialog busy={busy === "cost"} item={editingCost} onClose={() => setEditingCost(null)} onSubmit={(item, reason) => { void saveCost(item, reason).then((saved) => { if (saved) setEditingCost(null); }); }} /> : null}
      {editingNorm ? <ProductionNormDialog busy={busy === "cost"} item={editingNorm} onClose={() => setEditingNorm(null)} onSubmit={(item, reason) => { void saveNorm(item, reason); }} /> : null}
    </div>
  );
}

function DashboardPeriodControl({
  period,
  setPeriod,
}: {
  period: DashboardPeriod;
  setPeriod: (period: DashboardPeriod) => void;
}) {
  const [draftRange, setDraftRange] = useState(() => ({
    start: period.start ?? defaultCustomPeriod().start ?? "",
    end: period.end ?? defaultCustomPeriod().end ?? "",
  }));

  useEffect(() => {
    if (period.id === "custom") {
      setDraftRange({
        start: period.start ?? defaultCustomPeriod().start ?? "",
        end: period.end ?? defaultCustomPeriod().end ?? "",
      });
    }
  }, [period.id, period.start, period.end]);

  const selectPeriod = (id: DashboardPeriodId) => {
    if (id === "custom") {
      const next = period.id === "custom" ? period : defaultCustomPeriod();
      setDraftRange({ start: next.start ?? "", end: next.end ?? "" });
      setPeriod(next);
      return;
    }
    setPeriod({ id });
  };

  const validDraft = Boolean(draftRange.start && draftRange.end && draftRange.start <= draftRange.end);
  const rangeChanged = draftRange.start !== period.start || draftRange.end !== period.end;

  return (
    <div className="period-control" aria-label="Период данных">
      <span className="period-caption">Период данных</span>
      <div className="segmented period-segments" role="group" aria-label="Быстрый выбор периода">
        {dashboardPeriodOptions.map((option) => (
          <button
            className={period.id === option.id ? "selected" : ""}
            key={option.id}
            type="button"
            onClick={() => selectPeriod(option.id)}
          >
            {option.label}
          </button>
        ))}
      </div>
      {period.id === "custom" ? (
        <div className="date-range-inputs">
          <label>
            <span>С</span>
            <input
              aria-label="Дата начала"
              type="date"
              value={draftRange.start}
              max={draftRange.end || undefined}
              onChange={(event) => setDraftRange((range) => ({ ...range, start: event.target.value }))}
            />
          </label>
          <label>
            <span>По</span>
            <input
              aria-label="Дата окончания"
              type="date"
              value={draftRange.end}
              min={draftRange.start || undefined}
              onChange={(event) => setDraftRange((range) => ({ ...range, end: event.target.value }))}
            />
          </label>
          <button
            className="period-apply-button"
            disabled={!validDraft || !rangeChanged}
            type="button"
            onClick={() => setPeriod({ id: "custom", start: draftRange.start, end: draftRange.end })}
          >
            Применить
          </button>
        </div>
      ) : null}
    </div>
  );
}

function ProductionView({
  materialNeeds,
  onAction,
  onRefresh,
  productionMetrics,
  productionStages,
  productionTasks,
  onOpenProductionTask,
  onOpenProductionStage,
  onOpenMaterialNeed,
}: {
  materialNeeds: MaterialNeed[];
  onAction: (label: string, itemLabel: string) => void;
  onRefresh: () => Promise<void>;
  productionMetrics: DashboardMetric[];
  productionStages: ProductionStage[];
  productionTasks: ProductionTask[];
  onOpenProductionTask: (task: ProductionTask) => void;
  onOpenProductionStage: (stage: ProductionStage) => void;
  onOpenMaterialNeed: (item: MaterialNeed) => void;
}) {
  const [productionTab, setProductionTab] = useState<"shift" | "wip" | "capacity" | "costs">("shift");
  const productionTabs = [{ id: "shift", label: "Смена" }, { id: "wip", label: "НЗП и сырьё" }, { id: "capacity", label: "Мощность и календарь" }, { id: "costs", label: "Состав и затраты" }] as const;
  const plannedValue = productionMetrics.find((metric) => metric.label === "План смены" || metric.label === "К запуску")?.value ?? "0";
  return (
    <div className="workspace-view production-view">
      <nav className="production-tabs" aria-label="Разделы производства">{productionTabs.map(tab => <button type="button" key={tab.id} aria-pressed={productionTab === tab.id} onClick={() => setProductionTab(tab.id)}>{tab.label}</button>)}</nav>
      <div hidden={productionTab !== "shift"}>
      <section className="hero-strip production-hero" aria-label="Производственный контур">
        <div>
          <div className="eyebrow">
            <Factory size={16} />
            смена
          </div>
          <h2>{productionTasks.length ? `К запуску: ${plannedValue} компл.` : "Смена не сформирована"}</h2>
          <p>{productionTasks.length ? "План, фактическое выполнение и материалы собраны рядом с задачами на запуск." : "Сначала соберите рабочую смену — после этого здесь появятся задания и их выполнение."}</p>
        </div>
        <div className="hero-actions">
          <button className="secondary-button" type="button" onClick={() => onAction("Смена", "производство")}>
            <Plus size={17} />
            <span>Собрать смену</span>
          </button>
        </div>
      </section>

      <div id="production-shift"><ProductionExecutionPanel onRefresh={onRefresh} /></div>

      <MetricGrid metrics={productionMetrics} onMetricClick={(metric) => onAction("Партия", metric.label)} />

      <div className="content-grid compact-flow">
        <section className="work-section">
          <SectionHeader kicker="Партии" title="Производственный лист">
            <span className="section-tag">
              <Factory size={16} />
              Смена
            </span>
          </SectionHeader>
          {productionTasks.length ? <div className="production-list">
            {productionTasks.map((task) => <ProductionTaskRow key={task.sku} onAction={onAction} onOpen={onOpenProductionTask} task={task} />)}
          </div> : <p className="plan-empty">Нет заданий в открытой смене. Нажмите «Собрать смену», чтобы создать рабочий план.</p>}
        </section>

        <aside className="insight-column">
          <section className="panel">
            <SectionHeader compact kicker="Этапы" title="Готовность">
              <button className="icon-button" type="button" aria-label="Открыть рабочую смену" onClick={() => onAction("Рабочая смена", "производство")}><Zap size={20} /></button>
            </SectionHeader>
            {productionStages.length ? <div className="timeline">
              {productionStages.map((stage) => <ProductionStageRow key={stage.label} stage={stage} onOpen={() => onOpenProductionStage(stage)} />)}
            </div> : <p className="plan-empty">Этапы не переданы API</p>}
          </section>

          <section className="panel">
            <SectionHeader compact kicker="Материалы" title="Покрытие сырья">
              <button className="icon-button" type="button" aria-label="Открыть закупки сырья" title="Открыть закупки сырья" onClick={() => onAction("Поставка", "сырьё") }><PackageCheck size={20} /></button>
            </SectionHeader>
            {materialNeeds.length ? <div className="material-list">
              {materialNeeds.map((item) => <MaterialNeedRow item={item} key={item.name} onOpen={() => onOpenMaterialNeed(item)} />)}
            </div> : <p className="plan-empty">Нет данных о сырье</p>}
          </section>
        </aside>
      </div>
      </div>
      <ProductionWorkspace view={productionTab === "wip" || productionTab === "capacity" ? productionTab : "hidden"} onChanged={() => void onRefresh()} />
      <div hidden={productionTab !== "capacity"}><WorkcenterCalendar /></div>
      <div hidden={productionTab !== "costs"}><ManufacturingCosts onChanged={() => void onRefresh()} /></div>
    </div>
  );
}

function ProductionExecutionPanel({ onRefresh }: { onRefresh: () => Promise<void> }) {
  const [tasks, setTasks] = useState<ProductionExecutionTask[]>([]);
  const [date, setDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = async () => {
    try {
      const result = await getProductionExecution();
      setTasks(result.tasks);
      const availableDates = Array.from(new Set(result.tasks.map((task) => task.task_date).filter((value): value is string => Boolean(value)))).sort((a, b) => b.localeCompare(a));
      setDate((current) => current && availableDates.includes(current) ? current : availableDates[0] ?? "");
      setMessage(null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось загрузить смены");
    }
  };

  useEffect(() => { void load(); }, []);

  const dates = Array.from(new Set(tasks.map((task) => task.task_date).filter((value): value is string => Boolean(value)))).sort((a, b) => b.localeCompare(a));
  const visible = tasks.filter((task) => task.task_date === date);
  const updateTask = (taskKey: string, patch: Partial<ProductionExecutionTask>) => {
    setTasks((rows) => rows.map((row) => row.task_key === taskKey ? { ...row, ...patch } : row));
  };

  const save = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await saveProductionExecution(visible);
      await load();
      await onRefresh();
      setMessage("Исполнение смены сохранено");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось сохранить смену");
    } finally {
      setBusy(false);
    }
  };

  const close = async () => {
    if (!await askConfirm("Закрыть смену и оприходовать подтверждённый фактический выпуск?")) return;
    setBusy(true);
    setMessage(null);
    try {
      // The editor keeps the current shift changes locally until the user
      // saves them. Persist those edits before closing so the backend closes
      // the same facts/statuses the operator just confirmed, rather than the
      // stale values from the last save.
      await saveProductionExecution(visible);
      const result = await closeProductionExecution(date);
      setMessage(`Смена закрыта: оприходовано ${result.units} компл.`);
      // The close operation is already committed by the API. Do not keep the
      // primary action in a busy state while the dashboard reloads unrelated
      // sections; a slow refresh must never look like a hung close request.
      setBusy(false);
      void load().catch(() => undefined);
      void onRefresh().catch(() => undefined);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось закрыть смену");
    } finally {
      setBusy(false);
    }
  };

  return <section className="work-section">
    <SectionHeader kicker="Исполнение" title="Рабочая смена"><CalendarDays size={18} /></SectionHeader>
    {message ? <p className="operation-error" role="status">{message}</p> : null}
    <div className="production-shift-toolbar">
    <label className="operation-field production-date-field">
      <span>Дата смены</span>
      <select aria-label="Дата смены" className="filter-select" disabled={!dates.length} value={date} onChange={(event) => setDate(event.target.value)}>
        {!dates.length ? <option value="">Нет открытых смен</option> : null}
        {dates.map((value) => <option key={value} value={value}>{new Date(`${value}T00:00:00`).toLocaleDateString("ru-RU")}</option>)}
      </select>
    </label>
    <span className="production-shift-count">{dates.length ? `${visible.length} ${visible.length === 1 ? "задача" : "задач"}` : "Соберите смену, чтобы начать работу"}</span>
    </div>
    {visible.length ? <div className="production-execution-list">
      {visible.map((task) => <article className="production-execution-task" key={task.task_key}>
        <div className="production-task-header">
          <strong>{task.product_name || task.supplier_article}</strong>
          <span>{task.supplier_article || task.task_key}</span>
        </div>
        <div className="production-task-fields">
          <div className="production-task-plan"><span>План</span><strong>{task.planned_units} ед.</strong></div>
          <label><span>Факт, ед.</span><input min="0" max={task.planned_units} type="number" value={task.actual_units} onChange={(event) => updateTask(task.task_key, { actual_units: Number(event.target.value) })} /></label>
          <label><span>Статус</span><select className="filter-select" value={task.status} onChange={(event) => updateTask(task.task_key, { status: event.target.value })}><option>Не начато</option><option>В работе</option><option>Изготовлено</option><option>Упаковано</option><option>Передано на отгрузку</option></select></label>
          <label className="production-task-note"><span>Примечание</span><input value={task.note} placeholder="Комментарий" onChange={(event) => updateTask(task.task_key, { note: event.target.value })} /></label>
        </div>
      </article>)}
    </div> : <p className="plan-empty">{dates.length ? "На выбранную дату заданий нет" : "Открытых смен пока нет. Нажмите «Собрать смену» и укажите дату, товар и план."}</p>}
    <div className="operation-actions">
      <button className="ghost-button" disabled={busy || !visible.length} type="button" onClick={() => void save()}>Сохранить исполнение</button>
      <button className="primary-button" disabled={busy || !visible.some((task) => task.actual_units > 0 && ["Изготовлено", "Упаковано", "Передано на отгрузку"].includes(task.status))} type="button" onClick={() => void close()}><CheckCircle2 size={16} />{busy ? "Проводим" : "Закрыть смену"}</button>
    </div>
  </section>;
}

const supplyTabs = [{ id: "orders", label: "Заявки и приёмка" }, { id: "suppliers", label: "Поставщики" }, { id: "plan", label: "План закупок" }, { id: "fbs", label: "FBS" }] as const;
const supplyTabTargets: Record<string, string> = { "supply-orders": "orders", "supplier-directory": "suppliers", "supply-planning": "plan", "supply-fbs": "fbs" };

function SupplyView({
  onAction,
  onRefresh,
  session,
  procurementOrders,
  supplierPulse,
  supplyMetrics,
  supplyShipments,
  supplyNewOrders,
  acceptanceCoefficients,
  transitTariffs,
  supplyErrors,
  onOpenShipment,
  target,
}: {
  onAction: (label: string, itemLabel: string) => void;
  onRefresh: () => Promise<void>;
  session: DashboardSession | null;
  procurementOrders: Array<Record<string, unknown>>;
  supplierPulse: SupplierPulse[];
  supplyMetrics: DashboardMetric[];
  supplyShipments: SupplyShipment[];
  supplyNewOrders: Array<Record<string, unknown>>;
  acceptanceCoefficients: Array<Record<string, unknown>>;
  transitTariffs: Array<Record<string, unknown>>;
  supplyErrors: Record<string, string>;
  onOpenShipment: (shipment: SupplyShipment) => void;
  target?: string | null;
}) {
  const [busyOrder, setBusyOrder] = useState<number | null>(null);
  const [paymentOrder, setPaymentOrder] = useState<Record<string, unknown> | null>(null);
  const [receiptOrder, setReceiptOrder] = useState<Record<string, unknown> | null>(null);
  const [procurementItems, setProcurementItems] = useState<Array<Record<string, unknown>>>([]);
  const [suppliers, setSuppliers] = useState<SupplierRecord[]>([]);
  const [editingSupplier, setEditingSupplier] = useState<SupplierRecord | null>(null);
  const [supplierBusy, setSupplierBusy] = useState(false);
  const [operationError, setOperationError] = useState<string | null>(null);
  const [fbsDialog, setFbsDialog] = useState<{ id: string; detail: FbsSupplyDetail; barcode: FbsSupplyBarcode | null } | null>(null);
  const [fbsBusy, setFbsBusy] = useState(false);
  const [fbsError, setFbsError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    void Promise.all([getProcurementOperations(), getSuppliers()])
      .then(([operations, directory]) => {
        if (!active) return;
        setProcurementItems(operations.items);
        setSuppliers(directory.items);
      })
      .catch(() => {
        if (active) setOperationError("Не удалось загрузить данные поставок");
      });
    return () => { active = false; };
  }, [procurementOrders]);
  const saveSupplierRecord = async (item: SupplierRecord) => {
    setSupplierBusy(true);
    try {
      const result = await saveSupplier(item);
      if (result.item) setSuppliers((rows) => { const next = rows.filter((row) => row.id !== result.item!.id); return [...next, result.item!].sort((a, b) => a.name.localeCompare(b.name)); });
      else setSuppliers((await getSuppliers()).items);
      setEditingSupplier(null);
      await onRefresh().catch(() => undefined);
    } catch (error) { setOperationError(error instanceof Error ? error.message : "Не удалось сохранить поставщика"); }
    finally { setSupplierBusy(false); }
  };
  const updateStatus = async (orderId: number, status: string) => {
    setOperationError(null);
    setBusyOrder(orderId);
    try {
      await updateProcurementStatus(orderId, status);
      await onRefresh();
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : "Не удалось обновить статус закупки");
    } finally {
      setBusyOrder(null);
    }
  };
  const submitPayment = async (payment: { amount: number; payment_date: string; method: string; note: string }) => {
    if (!paymentOrder) return;
    const orderId = Number(paymentOrder.id);
    setOperationError(null);
    setBusyOrder(orderId);
    try {
      await createProcurementPayment(orderId, payment);
      await onRefresh();
      setPaymentOrder(null);
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : "Не удалось записать оплату");
    } finally {
      setBusyOrder(null);
    }
  };
  const submitReceipt = async (itemId: number, quantity: number) => {
    if (!receiptOrder) return;
    const orderId = Number(receiptOrder.id);
    setOperationError(null);
    setBusyOrder(orderId);
    try {
      await postSupplyReceipt(String(orderId), itemId, quantity);
      await onRefresh();
      const data = await getProcurementOperations();
      setProcurementItems(data.items);
      setReceiptOrder(null);
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : "Не удалось провести приёмку");
    } finally {
      setBusyOrder(null);
    }
  };
  const openFbsSupply = async (supplyId: string) => {
    setFbsBusy(true);
    setFbsError(null);
    try {
      const detail = await getFbsSupplyDetails(supplyId);
      setFbsDialog({ id: supplyId, detail, barcode: null });
    } catch (error) {
      setFbsError(error instanceof Error ? error.message : "Не удалось открыть поставку WB");
    } finally {
      setFbsBusy(false);
    }
  };
  const loadFbsBarcode = async () => {
    if (!fbsDialog) return;
    setFbsBusy(true);
    setFbsError(null);
    try {
      const barcode = await getFbsSupplyBarcode(fbsDialog.id);
      setFbsDialog((current) => current ? { ...current, barcode } : current);
    } catch (error) {
      setFbsError(error instanceof Error ? error.message : "Не удалось получить QR поставки WB");
    } finally {
      setFbsBusy(false);
    }
  };
  return (
    <div className="workspace-view">
      {Object.keys(supplyErrors).length ? <p className="data-banner stale" role="status">{wbAvailabilityMessage(Object.values(supplyErrors))}</p> : null}
      <section className="hero-strip supply-hero" aria-label="Поставки">
        <div>
          <div className="eyebrow">
            <Truck size={16} />
            поставки
          </div>
          <h2>{supplyMetrics[0]?.value ?? "0 SKU"} ждут пополнения</h2>
          <p>Закупки, окна приемки и поставщики сведены в рабочую очередь без перехода в таблицы.</p>
        </div>
        <div className="hero-actions">
          <button className="secondary-button" type="button" onClick={() => onAction("Заявка", "поставки")}>
            <Plus size={17} />
            <span>Создать заявку</span>
          </button>
        </div>
      </section>

      <MetricGrid metrics={supplyMetrics} onMetricClick={(metric) => onAction("Поставка", metric.label)} />

      <WorkspaceTabs ariaLabel="Разделы поставок" storageKey="mh.tab.supply" target={target} targetMap={supplyTabTargets} tabs={supplyTabs} panels={{
        orders: (<>
      <div className="content-grid compact-flow">
        <section className="work-section" id="supply-orders">
          <SectionHeader kicker="Очередь" title="Поставки и заявки">
            <span className="section-tag">
              <Truck size={16} />
              ETA
            </span>
          </SectionHeader>
          {supplyShipments.length ? <div className="shipment-list">
            {supplyShipments.map((shipment) => <SupplyShipmentRow key={shipment.id} onAction={onAction} onOpen={() => onOpenShipment(shipment)} onOpenFbs={shipment.amount === "WB FBS" ? openFbsSupply : undefined} shipment={shipment} />)}
          </div> : <p className="plan-empty">Нет активных поставок</p>}
        </section>
        <aside className="insight-column">
          <section className="panel">
            <SectionHeader compact kicker="Окна" title="Приемка WB">
              <button className="icon-button" type="button" aria-label="Открыть поставки" onClick={() => onAction("Поставка", "Приёмка WB")}><CalendarDays size={20} /></button>
            </SectionHeader>
            <div className="slot-grid">
              {(() => {
                const coefficientRows = acceptanceCoefficients.slice(0, 3);
                if (!coefficientRows.length) return <p className="plan-empty">Коэффициенты приёмки WB пока не загружены или недоступны по токену.</p>;
                return coefficientRows.map((row, index) => { const warehouse = String(row.warehouseName ?? row.warehouse ?? row.warehouse_name ?? "Склад WB"); const date = String(row.date ?? row.dateFrom ?? row.date_to ?? `Окно ${index + 1}`); const coefficient = row.coefficient ?? row.value ?? row.coeff ?? "—"; return <button className="stat detail-trigger" type="button" key={`${warehouse}-${date}-${index}`} onClick={() => onAction("Поставка", `Приёмка WB: ${warehouse}`)}><span>{warehouse}</span><strong>{date}</strong><small>коэффициент: {String(coefficient)}</small></button>; });
              })()}
            </div>
          </section>
          <section className="panel">
            <SectionHeader compact kicker="Маршруты" title="Тарифы транзита">
              <button className="icon-button" type="button" aria-label="Открыть тарифы транзита" onClick={() => onAction("Поставка", "Тарифы транзита")}><Truck size={20} /></button>
            </SectionHeader>
            {transitTariffs.length ? <div className="slot-grid">{transitTariffs.slice(0, 4).map((row, index) => {
              const from = String(row.transitWarehouseName ?? row.fromWarehouseName ?? row.from ?? "Склад отправления");
              const to = String(row.destinationWarehouseName ?? row.toWarehouseName ?? row.to ?? "Направление");
              const box = row.boxTariff == null ? "—" : typeof row.boxTariff === "object" ? "по объёму" : `${row.boxTariff} ₽`;
              const pallet = row.palletTariff == null ? "—" : `${row.palletTariff} ₽`;
              return <button className="stat detail-trigger" type="button" key={`${from}-${to}-${index}`} onClick={() => onAction("Поставка", `Тариф: ${from} → ${to}`)}><span>{from}</span><strong>{to}</strong><small>короб: {box} · паллета: {pallet}</small></button>;
            })}</div> : <p className="plan-empty">Тарифы транзита WB пока не загружены или недоступны по токену.</p>}
          </section>
        </aside>
      </div>
      <section className="work-section">
        <SectionHeader kicker="Журнал" title="Закупочные документы"><Truck size={18} /></SectionHeader>
        {operationError ? <p className="operation-error" role="alert">{operationError}</p> : null}
        {procurementOrders.length ? <div className="procurement-order-list">{procurementOrders.map((order) => {
          const orderId = Number(order.id);
          const outstanding = Number(order.outstanding_amount ?? 0);
          const hasOpenItems = procurementItems.some((item) => Number(item.order_id) === orderId && Number(item.quantity ?? 0) > Number(item.posted_quantity ?? 0));
          const isBusy = busyOrder === orderId;
          return <article className="procurement-order-card detail-trigger" key={String(order.id)} role="button" tabIndex={0} onClick={() => onAction("Поставка", `Заявка #${orderId}`)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onAction("Поставка", `Заявка #${orderId}`); } }}>
            <div className="procurement-order-heading">
              <div><span>Заявка #{String(order.id)}</span><strong>{String(order.supplier_name ?? "Поставщик не указан")}</strong></div>
              <StatusPill tone={String(order.status).includes("Получ") ? "good" : "warn"}>{String(order.status ?? "")}</StatusPill>
            </div>
            <div className="procurement-order-meta"><span>Ожидается: <strong>{String(order.expected_date ?? "—")}</strong></span><span>К оплате: <strong>{outstanding > 0 ? `${Math.round(outstanding).toLocaleString("ru-RU")} ₽` : "нет"}</strong></span></div>
            <label className="procurement-status-field" onClick={(event) => event.stopPropagation()}><span>Статус заявки</span><select className="filter-select" value={String(order.status ?? "Запланировано")} disabled={isBusy} onChange={(event) => void updateStatus(orderId, event.target.value)}><option>Запланировано</option><option>Заказано</option><option>Частично оплачено</option><option>Оплачено</option><option>В пути</option><option>Частично получено</option><option>Получено</option><option>Отменено</option></select></label>
            <div className="procurement-order-actions">
              <button className="ghost-button" type="button" disabled={isBusy || !hasOpenItems} onClick={(event) => { event.stopPropagation(); setOperationError(null); setReceiptOrder(order); }}><PackageCheck size={16} />Провести приёмку</button>
              <button className="secondary-button" type="button" disabled={isBusy || outstanding <= 0} onClick={(event) => { event.stopPropagation(); setOperationError(null); setPaymentOrder(order); }}><WalletCards size={16} />{outstanding > 0 ? "Внести оплату" : "Оплачено"}</button>
            </div>
          </article>;
        })}</div> : <p className="plan-empty">Закупочных документов пока нет</p>}
      </section>
        </>),
        suppliers: (<>
      <section className="work-section supplier-directory-section">
        <SectionHeader kicker="Справочник" title="Поставщик">
          <button className="secondary-button" type="button" onClick={() => setEditingSupplier({ id: 0, name: "", contact_person: "", phone: "", messenger: "", email: "", country: "", default_currency: "RUB", payment_terms_days: 3, lead_time_days: 7, active: 1, note: "" })}><Plus size={16} />Добавить поставщика</button>
        </SectionHeader>
        {suppliers.length ? <div className="supplier-directory-grid">{suppliers.map((supplier) => <article className="supplier-directory-card detail-trigger" key={supplier.id} tabIndex={0} role="button" onClick={() => setEditingSupplier(supplier)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setEditingSupplier(supplier); } }}><div><span>{supplier.country || "Поставщик"}</span><h3>{supplier.name}</h3></div><p>{supplier.contact_person || "Контактное лицо не указано"}</p><div className="supplier-contact-list">{supplier.phone ? <span>Телефон: {supplier.phone}</span> : null}{supplier.email ? <span>Email: {supplier.email}</span> : null}{supplier.messenger ? <span>Мессенджер: {supplier.messenger}</span> : null}</div><div className="supplier-card-footer"><small>Срок поставки: {supplier.lead_time_days} дн.</small><button className="icon-button" type="button" aria-label={`Изменить поставщика ${supplier.name}`} onClick={(event) => { event.stopPropagation(); setEditingSupplier(supplier); }}><Pencil size={16} /></button></div></article>)}</div> : <p className="plan-empty">Поставщиков пока нет. Добавьте первого поставщика с контактами.</p>}
      </section>
        <div className="insight-column">
          <section className="panel">
            <SectionHeader compact kicker="Поставщики" title="Пульс">
              <button className="icon-button" type="button" aria-label="Создать заявку поставщику" onClick={() => onAction("Заявка", "поставщики")}><ShieldCheck size={20} /></button>
            </SectionHeader>
            {supplierPulse.length ? <div className="supplier-list">
              {supplierPulse.map((supplier) => <SupplierPulseRow item={supplier} key={supplier.name} onOpen={() => onAction("Поставщик", supplier.name)} />)}
            </div> : <p className="plan-empty">Нет данных о поставщиках</p>}
          </section>

        </div>
        </>),
        plan: (<>
      <SupplyPlanning
        readOnly={!session || session.mode === "demo" || !["owner", "manager"].includes(session.user.role.toLowerCase())}
        onRefresh={onRefresh}
      />
        </>),
        fbs: (<>
      <section className="work-section">
        <SectionHeader kicker="WB FBS" title="Новые сборочные задания"><PackageCheck size={18} /></SectionHeader>
        {supplyNewOrders.length ? <div className="shipment-list">{supplyNewOrders.slice(0, 12).map((order, index) => <article className="shipment-row" key={String(order.orderId ?? order.id ?? index)}><div><strong>Заказ {String(order.orderId ?? order.id ?? "WB")}</strong><span>{String(order.address ?? order.deliveryType ?? "Новое сборочное задание")}</span></div><StatusPill tone="warn">ожидает сборки</StatusPill></article>)}</div> : <p className="plan-empty">Новых сборочных заданий FBS нет или раздел недоступен для токена.</p>}
      </section>
      <FbsOrdersPanel canWrite={!!session && session.mode !== "demo" && ["owner", "manager"].includes(session.user.role.toLowerCase())} />
        </>),
      }} />
      {paymentOrder ? <ProcurementPaymentDialog busy={busyOrder === Number(paymentOrder.id)} order={paymentOrder} onClose={() => { if (!busyOrder) setPaymentOrder(null); }} onSubmit={submitPayment} /> : null}
      {editingSupplier ? <SupplierDialog busy={supplierBusy} item={editingSupplier} onClose={() => { if (!supplierBusy) setEditingSupplier(null); }} onSubmit={saveSupplierRecord} /> : null}
      {receiptOrder ? <ProcurementReceiptDialog busy={busyOrder === Number(receiptOrder.id)} items={procurementItems.filter((item) => Number(item.order_id) === Number(receiptOrder.id) && Number(item.quantity ?? 0) > Number(item.posted_quantity ?? 0))} order={receiptOrder} onClose={() => { if (!busyOrder) setReceiptOrder(null); }} onSubmit={submitReceipt} /> : null}
      {fbsError && !fbsDialog ? <p className="operation-error" role="alert">{fbsError}</p> : null}
      {fbsDialog ? <FbsSupplyDialog busy={fbsBusy} error={fbsError} item={fbsDialog} onClose={() => { if (!fbsBusy) { setFbsDialog(null); setFbsError(null); } }} onLoadBarcode={loadFbsBarcode} /> : null}
    </div>
  );
}

function AssistantView({
  agentCandidates,
  agentHistory,
  actions,
  assistantMetrics,
  automations,
  onAction,
  onRefresh,
}: {
  agentCandidates: AgentCandidate[];
  agentHistory: AgentCandidate[];
  actions: ActionRecommendation[];
  assistantMetrics: DashboardMetric[];
  automations: AssistantAutomation[];
  onAction: (label: string, itemLabel: string) => void;
  onRefresh: () => Promise<void>;
}) {
  const [confirmCandidate, setConfirmCandidate] = useState<AgentCandidate | null>(null);
  const [decisionBusy, setDecisionBusy] = useState(false);
  const [candidateFilter, setCandidateFilter] = useState<"all" | "ad" | "price">("all");
  const [marketplaceFilter, setMarketplaceFilter] = useState("all");
  const [actionFilter, setActionFilter] = useState("all");
  const visibleCandidates = agentCandidates.filter((candidate) =>
    (candidateFilter === "all" || candidate.agent === candidateFilter)
    && (marketplaceFilter === "all" || candidate.marketplace === marketplaceFilter)
    && (actionFilter === "all" || candidate.action === actionFilter)
  );
  const marketplaces = [...new Set(agentCandidates.map((candidate) => candidate.marketplace))];
  const actionOptions = [...new Set(agentCandidates.map((candidate) => candidate.action))];
  return (
    <div className="workspace-view">
      <section className="hero-strip assistant-hero" aria-label="Агенты цены и рекламы">
        <div>
          <div className="eyebrow">
            <Bot size={16} />
            Агенты
          </div>
          <h2>Агенты цены и рекламы</h2>
          <p>Рекомендации по рекламе и ценам. Рискованные операции остаются на ручном подтверждении.</p>
        </div>
        <div className="hero-actions">
          <button className="secondary-button" type="button" onClick={() => onAction("Агент", "очередь")}>
            <Zap size={17} />
            <span>Запустить</span>
          </button>
        </div>
      </section>

      <MetricGrid metrics={assistantMetrics} onMetricClick={(metric) => onAction("Агент", metric.label)} />

      <div className={cx("content-grid", !agentCandidates.length && "assistant-empty-layout")}>
        <section className="work-section">
          <SectionHeader kicker="Решения" title="Очередь AI-агентов">
            {agentCandidates.length ? <div className="agent-filters">
              <select aria-label="Тип агента" className="filter-select" value={candidateFilter} onChange={(event) => setCandidateFilter(event.target.value as "all" | "ad" | "price")}>
                <option value="all">Все типы</option><option value="ad">Реклама</option><option value="price">Цены</option>
              </select>
              <select aria-label="Площадка" className="filter-select" value={marketplaceFilter} onChange={(event) => setMarketplaceFilter(event.target.value)}>
                <option value="all">Все площадки</option>{marketplaces.map((marketplace) => <option key={marketplace} value={marketplace}>{marketplace.toUpperCase()}</option>)}
              </select>
              <select aria-label="Действие" className="filter-select" value={actionFilter} onChange={(event) => setActionFilter(event.target.value)}>
                <option value="all">Все действия</option>{actionOptions.map((action) => <option key={action} value={action}>{action}</option>)}
              </select>
            </div> : null}
          </SectionHeader>
          {visibleCandidates.length ? <div className="action-list">
            {visibleCandidates.slice(0, 8).map((candidate) => <AgentCandidateRow candidate={candidate} key={candidate.id} onDecision={async (outcome) => {
              try {
                if (outcome === "apply") {
                  setConfirmCandidate(candidate);
                  return;
                }
                await decideAgentCandidate(candidate.id, outcome);
                await onRefresh();
              } catch (error) {
                await notifyUser(error instanceof Error ? error.message : "Решение не выполнено");
              }
            }} />)}
          </div> : <p className="plan-empty">Очередь AI-агентов пуста</p>}
        </section>

        <aside className="insight-column">
          <section className="panel">
            <SectionHeader compact kicker="Правила" title="Автоматизация">
              <Command size={20} />
            </SectionHeader>
            {automations.length ? <div className="automation-list">
              {automations.map((automation) => <AutomationRow item={automation} key={automation.title} onOpen={() => onAction("Агент", automation.title)} />)}
            </div> : <p className="plan-empty">Правила автоматизации не переданы API</p>}
          </section>

          <section className="panel">
            <SectionHeader compact kicker="История" title="Последние решения">
              <CheckCircle2 size={20} />
            </SectionHeader>
            {agentHistory.length ? <div className="automation-list">{agentHistory.slice(0, 6).map((item) => <article className="automation-row" key={item.id}><div><strong>{item.targetName}</strong><span>{item.marketplace.toUpperCase()} · {item.agent} · {item.action}</span></div><StatusPill tone={item.status === "rejected" ? "danger" : "good"}>{item.status === "rejected" ? "отклонено" : "применено"}</StatusPill></article>)}</div> : <p className="plan-empty">Решений пока нет</p>}
          </section>

          <section className="panel">
            <SectionHeader compact kicker="Границы" title="Что не делать авто">
              <ShieldCheck size={20} />
            </SectionHeader>
            <div className="guardrail-list">
              <StatusPill tone="danger">списания FIFO</StatusPill>
              <StatusPill tone="warn">цены ниже маржи</StatusPill>
              <StatusPill tone="info">новые поставщики</StatusPill>
            </div>
          </section>
        </aside>
      </div>
      {confirmCandidate ? <AgentDecisionDialog
        busy={decisionBusy}
        candidate={confirmCandidate}
        onCancel={() => setConfirmCandidate(null)}
        onConfirm={async () => {
          setDecisionBusy(true);
          try {
            await decideAgentCandidate(confirmCandidate.id, "apply");
            setConfirmCandidate(null);
            await onRefresh();
          } catch (error) {
            await notifyUser(error instanceof Error ? error.message : "Решение не выполнено");
          } finally {
            setDecisionBusy(false);
          }
        }}
      /> : null}
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

function AgentCandidateRow({ candidate, onDecision }: { candidate: AgentCandidate; onDecision: (outcome: "apply" | "reject") => Promise<void> }) {
  return (
    <article className="action-row warn">
      <div className="action-row-icon"><Bot size={18} /></div>
      <div className="action-row-copy"><span>{candidate.marketplace.toUpperCase()} · {candidate.agent} · {candidate.action}</span><h3>{candidate.targetName}</h3><p>{candidate.reason}</p><small>{candidate.currentValue} → {candidate.proposedValue}</small></div>
      <div className="action-row-actions"><button className="secondary-button" type="button" onClick={() => void onDecision("apply")}>Применить</button><button className="icon-button" type="button" aria-label="Отклонить рекомендацию" onClick={() => void onDecision("reject")}><X size={16} /></button></div>
    </article>
  );
}

function AgentDecisionDialog({ candidate, busy, onCancel, onConfirm }: { candidate: AgentCandidate; busy: boolean; onCancel: () => void; onConfirm: () => Promise<void> }) {
  const dialogRef=useDialogFocus(onCancel);
  return (
    <div className="drawer-backdrop operation-backdrop" role="presentation">
      <section className="plan-drawer operation-dialog" role="dialog" aria-modal="true" ref={dialogRef} aria-labelledby="agent-confirm-title">
        <div className="drawer-header"><div><span className="eyebrow">подтверждение</span><h2 id="agent-confirm-title">Применить рекомендацию?</h2><p>{candidate.marketplace.toUpperCase()} · {candidate.targetName}</p></div><button className="icon-button" type="button" aria-label="Закрыть" onClick={onCancel}><X size={18} /></button></div>
        <div className="operation-form"><p className="confirm-copy">{candidate.reason}</p><p className="confirm-values">{candidate.currentValue} → {candidate.proposedValue}</p><div className="operation-actions"><button className="ghost-button" type="button" onClick={onCancel}>Отмена</button><button className="primary-button" type="button" disabled={busy} onClick={() => void onConfirm()}><CheckCircle2 size={16} />{busy ? "Применяем" : "Применить"}</button></div></div>
      </section>
    </div>
  );
}

export function ReportsView({
  exportItems,
  onAction,
  onQuickReport,
  reportCards,
  reportMetrics,
  target,
}: {
  exportItems: ExportItem[];
  onAction: (label: string, itemLabel: string) => void;
  onQuickReport: (name: string, days: number, metrics: string[]) => Promise<void>;
  reportCards: ReportCard[];
  reportMetrics: DashboardMetric[];
  target?: string | null;
}) {
  const [operations, setOperations] = useState<ReportOperations | null>(null);
  const [delivery, setDelivery] = useState<ReportDelivery>({ configured: false, email_configured: false, telegram_configured: false, telegram_linked: false });
  const [telegramCode, setTelegramCode] = useState<{ code: string; ttl: number; link: string } | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [scheduleType, setScheduleType] = useState<"daily" | "weekly" | "monthly">("weekly");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [operationsError, setOperationsError] = useState<string | null>(null);
  const [deliveryError, setDeliveryError] = useState<string | null>(null);
  const [analytics, setAnalytics] = useState<AnalyticsDashboard | null>(null);
  const [analyticsError, setAnalyticsError] = useState<string | null>(null);
  const [quickDays, setQuickDays] = useState(30);
  const refreshOperations = async () => {
    try {
      setOperations(await getReportOperations());
      setOperationsError(null);
      // A transient API restart can leave an old plain-text 404 in this
      // component. Once the report data is available again, remove that
      // stale error instead of showing it next to healthy report cards.
      setMessage(null);
    } catch (error) {
      const detail = error instanceof Error ? error.message : "Не удалось загрузить отчёты";
      setOperationsError(detail);
      setMessage(detail);
    }
  };
  useEffect(() => { void refreshOperations(); }, []);
  useEffect(() => {
    void getAnalyticsDashboard("30d")
      .then((result) => { setAnalytics(result); setAnalyticsError(null); })
      .catch((error) => {
        setAnalytics(null);
        setAnalyticsError(error instanceof Error ? error.message : "Не удалось загрузить аналитику WB");
      });
  }, []);
  const refreshDelivery = async () => { setDeliveryError(null); try { setDelivery(await getReportDelivery()); } catch (error) { setDeliveryError(error instanceof Error ? error.message : "Не удалось загрузить настройки доставки"); } };
  useEffect(() => { void refreshDelivery(); }, []);
  useEffect(() => {
    if (!operations?.runs.some((run) => run.status === "queued" || run.status === "running" || (run.status === "ok" && run.delivery_status === "queued"))) return;
    const timer = window.setInterval(() => { void getReportOperations().then(setOperations).catch(() => undefined); }, 3000);
    return () => window.clearInterval(timer);
  }, [operations]);
  const createDefinition = async (form: HTMLFormElement) => {
    const values = new FormData(form);
    const scheduleType = String(values.get("schedule_type"));
    const metrics = Array.from(values.getAll("metrics"), String);
    setBusyId(-1); setMessage(null);
    try {
      await createReportDefinition({ name: String(values.get("name")), metrics, schedule_type: scheduleType, schedule_time: String(values.get("schedule_time")), email_enabled: values.get("email_enabled") === "on", telegram_enabled: values.get("telegram_enabled") === "on", ...(scheduleType === "weekly" ? { schedule_weekday: Number(values.get("schedule_weekday")) } : {}), ...(scheduleType === "monthly" ? { schedule_day: Number(values.get("schedule_day")) } : {}) });
      setShowForm(false); form.reset(); await refreshOperations(); setMessage("Расписание сохранено");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось сохранить расписание"); } finally { setBusyId(null); }
  };
  const linkTelegram = async () => { setMessage(null); try { const result = await requestTelegramLinkCode(); setTelegramCode({ code: result.code, ttl: result.ttl_minutes, link: result.deep_link }); } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось получить код Telegram"); } };
  const runDefinition = async (id: number) => { setBusyId(id); setMessage(null); try { await generateReportDefinition(id); await refreshOperations(); setMessage("Отчёт поставлен в очередь. Статус обновится автоматически."); } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось запустить отчёт"); } finally { setBusyId(null); } };
  const quickReportMetrics: Record<string, string[]> = {
    "Операционный отчёт": ["sales_orders", "pnl", "stocks", "oos_risk", "ads"],
    "Остатки и пополнение": ["stocks", "oos_risk", "stock_in_transit", "frozen_capital"],
    "Реклама и эффективность": ["ads", "ad_performance", "organic_vs_ads", "ad_funnel"],
  };
  const runQuickReport = async (name: string) => { setBusyId(-2); setMessage(null); try { await onQuickReport(name, quickDays, quickReportMetrics[name] ?? ["sales_orders", "ads", "stocks"]); await refreshOperations(); setMessage("Отчёт поставлен в очередь. Статус обновится автоматически."); } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось запустить PDF"); } finally { setBusyId(null); } };
  const removeDefinition = async (id: number) => { if (!await askConfirm("Удалить расписание и его историю запусков?")) return; setBusyId(id); setMessage(null); try { await deleteReportDefinition(id); await refreshOperations(); } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось удалить расписание"); } finally { setBusyId(null); } };
  const editDefinition = async (report: ReportDefinition) => { const name = await askText("Название расписания", report.name); if (name === null) return; const time = await askText("Время МСК (ЧЧ:ММ)", report.schedule_time); if (time === null) return; const emailChoice = await askText("Отправлять на Email? (да/нет)", report.email_enabled ? "да" : "нет"); if (emailChoice === null) return; const telegramChoice = await askText("Отправлять в Telegram? (да/нет)", report.telegram_enabled ? "да" : "нет"); if (telegramChoice === null) return; const emailEnabled = /^да|yes|1|true$/i.test(emailChoice.trim()); const telegramEnabled = /^да|yes|1|true$/i.test(telegramChoice.trim()); if (emailEnabled && !delivery.email_configured) { setMessage("Email не настроен в профиле"); return; } if (telegramEnabled && !delivery.telegram_linked) { setMessage("Сначала привяжите Telegram в блоке доставки"); return; } let metrics: string[] = []; try { metrics = JSON.parse(report.metrics); } catch { setMessage("У расписания повреждён список разделов"); return; } setBusyId(report.id); setMessage(null); try { await updateReportDefinition(report.id, { name, metrics, schedule_type: report.schedule_type, schedule_time: time, schedule_weekday: report.schedule_weekday, schedule_day: report.schedule_day, email_enabled: emailEnabled, telegram_enabled: telegramEnabled }); await refreshOperations(); setMessage("Расписание обновлено"); } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось обновить расписание"); } finally { setBusyId(null); } };
  const toggleDefinition = async (report: ReportDefinition) => { setBusyId(report.id); setMessage(null); try { await setReportDefinitionEnabled(report.id, !Boolean(report.enabled)); await refreshOperations(); setMessage(Boolean(report.enabled) ? "Расписание поставлено на паузу" : "Расписание возобновлено"); } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось изменить статус расписания"); } finally { setBusyId(null); } };
  const metricOptions = Object.entries(operations?.metric_labels ?? { sales_orders: "Заказы и продажи", ads: "Реклама", stocks: "Остатки", pnl: "Финансовый результат" });
  const definitionMetricCount = (report: { metrics: string }) => { try { return JSON.parse(report.metrics).length as number; } catch { return 0; } };
  const runTone = (status: string): Tone => status === "ok" ? "good" : status === "error" ? "danger" : "info";
  const runLabel = (status: string) => status === "ok" ? "готов" : status === "error" ? "ошибка" : status === "running" ? "формируется" : "в очереди";
  const deliveryLabel = (status?: string) => status === "ok" ? "доставка выполнена" : status === "error" ? "ошибка доставки" : status === "not_requested" ? "без доставки" : status || "доставка ожидается";
  const reportsPanel = (<>
      <section className="hero-strip reports-hero" aria-label="Отчеты">
        <div>
          <div className="eyebrow">
            <BarChart3 size={16} />
            отчеты
          </div>
          <h2>Отчёты и расписания</h2>
          <p>Сводки для собственника, склада и рекламы собираются из тех же данных, что питают дашборд.</p>
        </div>
        <div className="hero-actions">
          <label className="report-period-select"><span>Быстрый отчёт</span><select className="filter-select" value={quickDays} onChange={(event) => setQuickDays(Number(event.target.value))}><option value={1}>1 день</option><option value={7}>7 дней</option><option value={30}>30 дней</option><option value={90}>90 дней</option></select></label>
          <button className="ghost-button" type="button" disabled={busyId === -2} onClick={() => void runQuickReport("Операционный отчёт")}>
            <Download size={17} />
            <span>Сформировать PDF</span>
          </button>
          <button className="secondary-button" type="button" onClick={() => { if (!showForm) setScheduleType("weekly"); setShowForm((value) => !value); }}>
            <Plus size={17} />
            <span>Новое расписание</span>
          </button>
        </div>
      </section>

      <MetricGrid metrics={reportMetrics.map(metric => ({...metric,value: cabinetDate(metric.value), delta: cabinetText(metric.delta), detail: cabinetText(metric.detail)}))} />

      {analytics || analyticsError ? <section className="work-section report-analytics-section"><SectionHeader kicker="WB Analytics" title="Воронка продаж и история остатков"><BarChart3 size={18} /></SectionHeader>{analyticsError ? <div className="data-banner stale" role="status">Аналитика WB недоступна: {analyticsError}</div> : null}{analytics ? <>{analytics.errors.sales_funnel ? <p className="form-hint">{analytics.errors.sales_funnel}</p> : null}<div className="analytics-funnel-grid">{analytics.sales_funnel.stages.map((stage) => <div className="analytics-funnel-card" key={stage.key}><span>{stage.label}</span><strong>{Number(stage.value).toLocaleString("ru-RU")}</strong></div>)}</div>{analytics.stock_history.length ? <div className="analytics-history-list"><strong>Снимки остатков WB</strong>{analytics.stock_history.slice(-14).map((row) => <div className="analytics-history-row" key={row.date}><span>{row.date}</span><b>{Number(row.quantity).toLocaleString("ru-RU")} шт.</b><small>{row.rows} строк</small></div>)}</div> : <p className="plan-empty">История появится после первой синхронизации WB.</p>}</> : <p className="plan-empty">После восстановления доступа обновите аналитику.</p>}</section> : null}


      <section className="work-section">
          <SectionHeader kicker="Без расписания" title="Быстрые отчёты"><span className="muted-count">3</span></SectionHeader>
          <div className="report-list compact-report-list">{["Операционный отчёт", "Остатки и пополнение", "Реклама и эффективность"].map((name) => <button className="report-row detail-trigger" key={name} type="button" disabled={busyId === -2} onClick={() => void runQuickReport(name)}><div><span>{quickReportMetrics[name].length} {plural(quickReportMetrics[name].length, ["раздел", "раздела", "разделов"])} · {quickDays} {plural(quickDays, ["день", "дня", "дней"])}</span><h3>{name}</h3><p>Сформировать PDF и сохранить в архив</p></div><Download size={18} /></button>)}</div>
      </section>
  </>);
  const schedulesPanel = (<div className="content-grid">
        <section className="work-section">
          <div className={cx("report-subsection", operations && !operationsError && !operations.definitions.length && "cabinet-empty-row")}>
            <SectionHeader kicker="Автоматическая отправка" title="Расписания"><span className="muted-count">{operations?.definitions.length ?? reportCards.length}</span></SectionHeader>
            {operationsError ? <div className="data-banner stale" role="status">Расписания недоступны: {operationsError}</div> : operations ? (operations.definitions.length ? <div className="report-list">{operations.definitions.map((report) => <article className={cx("report-row", report.enabled ? "good" : "paused")} key={report.id}><div><span>{report.schedule_type === "daily" ? "Ежедневно" : report.schedule_type === "weekly" ? "Еженедельно" : "Ежемесячно"} · {report.schedule_time} МСК · {definitionMetricCount(report)} разделов</span><h3>{report.name}</h3><p>Последний запуск: {cabinetDate(report.last_run_at)} · {report.email_enabled ? "Email" : "без email"} · {report.telegram_enabled ? "Telegram" : "без Telegram"}</p></div><StatusPill tone={report.enabled ? "good" : "warn"}>{report.enabled ? "включён" : "пауза"}</StatusPill><div className="action-controls"><button className="icon-button" aria-label={report.enabled ? "Поставить на паузу" : "Возобновить расписание"} title={report.enabled ? "Поставить на паузу" : "Возобновить расписание"} disabled={busyId === report.id} type="button" onClick={() => void toggleDefinition(report)}><Play size={16} /></button><button className="icon-button" aria-label="Изменить расписание" title="Изменить расписание" disabled={busyId === report.id} type="button" onClick={() => void editDefinition(report)}><Pencil size={16} /></button><button className="icon-button" aria-label="Сформировать PDF сейчас" title="Сформировать PDF сейчас" disabled={busyId === report.id} type="button" onClick={() => void runDefinition(report.id)}><Play size={16} /></button><button className="icon-button" aria-label="Удалить расписание" title="Удалить расписание" disabled={busyId === report.id} type="button" onClick={() => void removeDefinition(report.id)}><Trash2 size={16} /></button></div></article>)}</div> : <p className="plan-empty">Расписаний пока нет.</p>) : <p className="plan-empty">Загружаем расписания…</p>}
            {operations && !operationsError && !operations.definitions.length ? <button className="ghost-button" type="button" onClick={() => setShowForm(true)}>Создать расписание</button> : null}
          </div>
        </section>

        <aside className="insight-column">
          <section className={cx("panel", operations && !operationsError && !operations.runs.length && !exportItems.length && "cabinet-empty-row")}>
            <SectionHeader compact kicker="Запуски" title="Файлы и ошибки"><BarChart3 size={20} /></SectionHeader>
            {operationsError ? <p className="plan-empty">История запусков недоступна до восстановления API.</p> : operations ? (operations.runs.length ? <div className="export-list">{operations.runs.map((run) => <article className={cx("export-row", `run-${run.status}`)} key={run.id}><div><strong>{run.definition_name || `Отчёт #${run.definition_id}`}</strong><span>{run.metric_count ?? 0} разделов · {cabinetDate(run.finished_at ?? run.started_at)}</span>{run.error ? <small className="run-error-text">{run.error}</small> : null}{run.delivery_error ? <small className="run-error-text">{run.delivery_error}</small> : null}</div><div className="run-actions"><StatusPill tone={runTone(run.status)}>{runLabel(run.status)}</StatusPill>{run.delivery_requested ? <small className={run.delivery_status === "error" ? "run-error-text" : "muted"}>{deliveryLabel(run.delivery_status)}</small> : null}{run.status === "ok" && run.file_available ? <button className="icon-button" aria-label="Скачать PDF" title="Скачать PDF" type="button" onClick={() => void downloadSavedReport(run.id)}><Download size={16} /></button> : null}</div></article>)}</div> : exportItems.length ? <div className="export-list">{exportItems.map((item) => <ExportRow item={item} key={item.name} />)}</div> : <p className="plan-empty">Запусков ещё не было</p>) : <p className="plan-empty">Загружаем историю запусков…</p>}
            {operations && !operationsError && !operations.runs.length && !exportItems.length ? <button className="ghost-button" disabled={busyId === -2} type="button" onClick={() => void runQuickReport("Операционный отчёт")}>Сформировать первый PDF</button> : null}
          </section>
        </aside>
      </div>);
  const deliveryPanel = (<div className="content-grid">
        <aside className="insight-column">
          <section className="panel">
            <SectionHeader compact kicker="Доставка" title="Email и Telegram"><Bell size={20} /></SectionHeader>
            {deliveryError ? <div className="data-banner stale" role="status">Настройки доставки недоступны: {deliveryError}</div> : null}
            <div className="delivery-settings">
              <div><strong>Email</strong><span>{delivery.email_configured ? `${delivery.email || "настроен"} · адрес аккаунта` : "SMTP не настроен"}</span></div>
              <div><strong>Telegram</strong><span>{delivery.telegram_linked ? `привязан к @${delivery.telegram_bot_username}` : delivery.telegram_configured ? `не привязан · @${delivery.telegram_bot_username}` : "бот не настроен"}</span></div>
            </div>
            {delivery.email_configured ? <p className="form-hint">Email для отчётов — адрес аккаунта. Изменить его можно в разделе <a href={gatewayUrlFor("/account")}>«Аккаунт»</a>; код подтверждения здесь не вводится.</p> : null}
            {delivery.telegram_configured ? <div className="telegram-link-box"><button className="secondary-button" type="button" onClick={() => void linkTelegram()}>{delivery.telegram_linked ? "Привязать другой Telegram" : "Привязать Telegram"}</button>{telegramCode ? <><p className="form-hint">Как привязать Telegram:</p><ol className="form-hint telegram-link-steps"><li>Нажмите «Открыть Telegram и привязать» — откроется чат бота <strong>@{delivery.telegram_bot_username}</strong>.</li><li>В Telegram нажмите «Запустить» (Start). Ссылка передаст код боту. Если ссылка не открылась, в чате этого же бота отправьте <code>/link {telegramCode.code}</code>.</li><li>Нажмите «Обновить статус» после ответа бота. Код одноразовый и действует {telegramCode.ttl} минут; оператору или в личные сообщения его отправлять не нужно.</li></ol>{telegramCode.link ? <a className="primary-link-button" href={telegramCode.link} rel="noreferrer" target="_blank">Открыть Telegram и привязать</a> : <p className="form-hint">Откройте чат бота <strong>@{delivery.telegram_bot_username}</strong> и отправьте <code>/link {telegramCode.code}</code>.</p>}</> : null}<button className="ghost-button" type="button" onClick={() => void refreshDelivery()}>Обновить статус</button></div> : null}
          </section>
        </aside>
      </div>);
  return (
    <div className="workspace-view">
      {message ? <p className="operation-error" role="status">{message}</p> : null}
      {showForm ? <section className="work-section report-form-section"><SectionHeader kicker="Расписание" title="Новый отчёт"><CalendarDays size={18} /></SectionHeader><form className="settings-form" onSubmit={(event) => { event.preventDefault(); void createDefinition(event.currentTarget); }}><label className="operation-field"><span>Название</span><input name="name" required placeholder="Еженедельная сводка" /></label><label className="operation-field"><span>Периодичность</span><select className="filter-select" name="schedule_type" value={scheduleType} onChange={(event) => setScheduleType(event.target.value as "daily" | "weekly" | "monthly")}><option value="daily">Ежедневно</option><option value="weekly">Еженедельно</option><option value="monthly">Ежемесячно</option></select></label><label className="operation-field"><span>Время МСК</span><input name="schedule_time" required type="time" defaultValue="09:00" /></label>{scheduleType === "weekly" ? <label className="operation-field"><span>День недели</span><select className="filter-select" name="schedule_weekday" defaultValue="0"><option value="0">Понедельник</option><option value="1">Вторник</option><option value="2">Среда</option><option value="3">Четверг</option><option value="4">Пятница</option><option value="5">Суббота</option><option value="6">Воскресенье</option></select></label> : null}{scheduleType === "monthly" ? <label className="operation-field"><span>Число месяца</span><input name="schedule_day" min="1" max="28" type="number" defaultValue="1" /></label> : null}<fieldset className="report-metrics"><legend>Что включить</legend>{metricOptions.map(([code, label]) => <label key={code}><input defaultChecked name="metrics" type="checkbox" value={code} />{label}</label>)}</fieldset><fieldset className="report-metrics"><legend>Доставка</legend><label><input defaultChecked={delivery.email_configured} disabled={!delivery.email_configured} name="email_enabled" type="checkbox" />Email{delivery.email_configured ? ` · ${delivery.email || "аккаунт"}` : " · не настроен"}</label><label><input defaultChecked={delivery.telegram_linked} disabled={!delivery.telegram_linked} name="telegram_enabled" type="checkbox" />Telegram{delivery.telegram_linked ? " · привязан" : " · сначала привяжите чат"}</label></fieldset><div className="operation-actions"><button className="ghost-button" type="button" onClick={() => setShowForm(false)}>Отмена</button><button className="primary-button" disabled={busyId === -1} type="submit"><CheckCircle2 size={16} />{busyId === -1 ? "Сохраняем" : "Сохранить расписание"}</button></div></form></section> : null}
      <WorkspaceTabs
        tabs={reportsTabs}
        ariaLabel="Разделы отчётов"
        storageKey="mh.tab.reports"
        target={target}
        targetMap={{ "reports-quick": "reports", "reports-schedules": "schedules", "reports-runs": "schedules", "reports-delivery": "delivery" }}
        panels={{ reports: reportsPanel, schedules: schedulesPanel, delivery: deliveryPanel }}
      />
    </div>
  );
}

function MetricGrid({ metrics, onMetricClick }: { metrics: DashboardMetric[]; onMetricClick?: (metric: DashboardMetric) => void }) {
  return (
    <section className="metrics-grid" aria-label="Ключевые показатели">
      {metrics.map((metric) => (
        <MetricCard key={metric.label} metric={metric} onClick={onMetricClick ? () => onMetricClick(metric) : undefined} />
      ))}
    </section>
  );
}

function MetricCard({ metric, onClick }: { metric: DashboardMetric; onClick?: () => void }) {
  const content = <>
      <span>{metric.label}</span>
      <strong>{metric.value}</strong>
      <div>
        <em>{metric.delta}</em>
        {metric.change ? <small className={cx("metric-change", metric.change.tone)}>{metric.change.text}</small> : null}
        <p>{metric.detail}</p>
      </div>
    </>;
  return onClick ? <button className={cx("metric-card", "metric-card-button", metric.tone)} type="button" onClick={onClick}>{content}</button> : <article className={cx("metric-card", metric.tone)}>{content}</article>;
}

function SectionHeader({
  children,
  compact = false,
  kicker,
  title,
}: {
  children?: ReactNode;
  compact?: boolean;
  kicker: string;
  title: string;
}) {
  return (
    <div className={cx("section-header", compact && "compact")}>
      <div>
        <span>{kicker}</span>
        <h2>{title}</h2>
      </div>
      {children}
    </div>
  );
}

function FinanceLedgerRow({ item, onOpen }: { item: FinanceLedgerItem; onOpen?: () => void }) {
  const content = <>
      <div>
        <span>{item.detail}</span>
        <strong>{item.label}</strong>
      </div>
      <em>{item.amount}</em>
    </>;
  return onOpen ? <button className={cx("ledger-row", "detail-trigger", item.tone)} type="button" onClick={onOpen}>{content}</button> : <article className={cx("ledger-row", item.tone)}>{content}</article>;
}

function SplitRow({ item, onOpen }: { item: ExpenseSplit; onOpen?: () => void }) {
  const content = <>
      <div>
        <strong>{item.label}</strong>
        <StatusPill tone={item.tone}>{item.value}</StatusPill>
      </div>
      <div className="split-meter">
        <span className={item.tone} style={{ width: `${item.width}%` }} />
      </div>
    </>;
  return onOpen ? <button className="split-row detail-trigger" type="button" onClick={onOpen}>{content}</button> : <div className="split-row">{content}</div>;
}

function ProductionStageRow({ onOpen, stage }: { onOpen?: () => void; stage: ProductionStage }) {
  const content = <>
      <span />
      <div>
        <strong>{stage.label}</strong>
        <p>{stage.value}</p>
      </div>
    </>;
  const className = cx("signal", stage.status === "done" && "done", stage.status === "active" && "active", stage.status === "blocked" && "blocked", onOpen && "detail-trigger");
  return onOpen ? <button className={className} type="button" onClick={onOpen}>{content}</button> : <div className={className}>{content}</div>;
}

function ProductionTaskRow({
  onAction,
  onOpen,
  task,
}: {
  onAction: (label: string, itemLabel: string) => void;
  onOpen?: (task: ProductionTask) => void;
  task: ProductionTask;
}) {
  const open = () => {
    if (onOpen) {
      onOpen(task);
      return;
    }
    onAction("Партия", task.sku);
  };
  return (
    <article className={cx("production-row", "detail-trigger", task.tone)} role="button" tabIndex={0} onClick={open} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); } }}>
      <div>
        <span>{task.sku}</span>
        <h3>{task.title}</h3>
      </div>
      <div className="production-counts">
        <Stat label="План" value={task.plan} />
        <Stat label="Факт" value={task.actual} />
      </div>
      <StatusPill tone={task.tone}>{task.status}</StatusPill>
      <button className="icon-button" type="button" aria-label="Открыть партию" onClick={(event) => { event.stopPropagation(); onAction("Партия", task.sku); }}>
        <ChevronRight size={18} />
      </button>
    </article>
  );
}

function MaterialNeedRow({ item, onOpen }: { item: MaterialNeed; onOpen?: () => void }) {
  const content = <>
      <div>
        <strong>{item.name}</strong>
        <span>{item.need}</span>
      </div>
      <div>
        <em>{item.stock}</em>
        <StatusPill tone={item.tone}>{item.status}</StatusPill>
      </div>
    </>;
  return onOpen ? <button className={cx("material-row", "detail-trigger", item.tone)} type="button" onClick={onOpen}>{content}</button> : <article className={cx("material-row", item.tone)}>{content}</article>;
}

function FbsSupplyDialog({
  busy,
  error,
  item,
  onClose,
  onLoadBarcode,
}: {
  busy: boolean;
  error: string | null;
  item: { id: string; detail: FbsSupplyDetail; barcode: FbsSupplyBarcode | null };
  onClose: () => void;
  onLoadBarcode: () => Promise<void>;
}) {
  const dialogRef=useDialogFocus(onClose);
  const barcodeValue = item.barcode?.barcode ?? item.barcode?.file ?? item.barcode?.url;
  const imageSource = typeof barcodeValue === "string" && barcodeValue.startsWith("data:image/") ? barcodeValue : null;
  return <dialog ref={dialogRef} aria-modal="true" className="plan-drawer operation-dialog" open aria-labelledby="fbs-supply-dialog-title">
    <div className="dialog-header"><div><span className="eyebrow">WB FBS</span><h2 id="fbs-supply-dialog-title">Поставка {item.id}</h2></div><button className="icon-button" type="button" aria-label="Закрыть" onClick={onClose}><X size={18} /></button></div>
    {error ? <p className="operation-error" role="alert">{error}</p> : null}
    <div className="fbs-detail-actions"><button className="secondary-button" type="button" disabled={busy} onClick={() => void onLoadBarcode()}>{busy ? "Загружаю…" : "Получить QR / стикер"}</button></div>
    {imageSource ? <img className="fbs-barcode-preview" src={imageSource} alt={`QR поставки ${item.id}`} /> : item.barcode ? <p className="form-hint">WB вернул код в формате {String(item.barcode.type ?? "файла")}. Откройте его в кабинете WB для печати.</p> : null}
    <pre className="fbs-detail-json">{JSON.stringify(item.detail, null, 2)}</pre>
    <div className="dialog-actions"><button className="ghost-button" type="button" onClick={onClose}>Закрыть</button></div>
  </dialog>;
}

function SupplyShipmentRow({
  onAction,
  onOpen,
  onOpenFbs,
  shipment,
}: {
  onAction: (label: string, itemLabel: string) => void;
  onOpen?: (shipment: SupplyShipment) => void;
  onOpenFbs?: (supplyId: string) => void;
  shipment: SupplyShipment;
}) {
  const open = () => {
    if (onOpenFbs) {
      onOpenFbs(shipment.id);
      return;
    }
    if (onOpen) {
      onOpen(shipment);
      return;
    }
    onAction("Поставка", shipment.id);
  };
  return (
    <article className={cx("shipment-row", "detail-trigger", shipment.tone)} role="button" tabIndex={0} onClick={open} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); } }}>
      <div>
        <span>{shipment.id}</span>
        <h3>{shipment.supplier}</h3>
      </div>
      <div className="shipment-meta">
        <Stat label="ETA" value={shipment.eta} />
        <Stat label="Сумма" value={shipment.amount} />
      </div>
      <StatusPill tone={shipment.tone}>{shipment.status}</StatusPill>
      <button className="icon-button" type="button" aria-label={onOpenFbs ? "Открыть поставку WB" : "Открыть поставку"} onClick={(event) => { event.stopPropagation(); open(); }}>
        <ChevronRight size={18} />
      </button>
    </article>
  );
}

function SupplierPulseRow({ item, onOpen }: { item: SupplierPulse; onOpen?: () => void }) {
  const content = <>
      <div>
        <strong>{item.name}</strong>
        <span>{item.detail}</span>
      </div>
      <StatusPill tone={item.tone}>{item.metric}</StatusPill>
    </>;
  return onOpen ? <button className="supplier-row detail-trigger" type="button" onClick={onOpen}>{content}</button> : <article className="supplier-row">{content}</article>;
}

function AutomationRow({ item, onOpen }: { item: AssistantAutomation; onOpen?: () => void }) {
  const content = <>
      <div>
        <strong>{item.title}</strong>
        <span>{item.detail}</span>
      </div>
      <StatusPill tone={item.tone}>{item.status}</StatusPill>
    </>;
  return onOpen ? <button className={cx("automation-row", "detail-trigger", item.tone)} type="button" onClick={onOpen}>{content}</button> : <article className={cx("automation-row", item.tone)}>{content}</article>;
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

function ExportRow({ item, onOpen }: { item: ExportItem; onOpen?: () => void }) {
  const content = <>
      <div>
        <strong>{item.name}</strong>
        <span>{item.updated}</span>
      </div>
      <StatusPill tone={item.tone}>{item.format}</StatusPill>
    </>;
  return onOpen ? <button className="export-row detail-trigger" type="button" onClick={onOpen}>{content}</button> : <article className="export-row">{content}</article>;
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

function CampaignRow({
  campaign,
  onAction,
}: {
  campaign: AdCampaign;
  onAction: (label: string, itemLabel: string) => void;
}) {
  return (
    <article className={cx("campaign-row", campaign.tone)}>
      <div>
        <span>{campaign.signal}</span>
        <h3>{campaign.name}</h3>
      </div>
      <div className="campaign-stats">
        <Stat label="Расход" value={campaign.spend} />
        <Stat label="ACoS" value={campaign.acos} />
        <Stat label="Выручка" value={campaign.revenue} />
      </div>
      <button className="secondary-button" type="button" onClick={() => onAction(campaign.action, campaign.name)}>
        {campaign.action}
      </button>
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

function InventoryTable({ inventory, onOpen }: { inventory: InventoryWatchRow[]; onOpen?: (row: InventoryWatchRow) => void }) {
  const sharedSignal = inventory.length > 1 && inventory.every(row => row.signal === inventory[0].signal) ? inventory[0].signal : null;
  const displayRows = sharedSignal ? inventory.map(row => ({...row,signal:"—"})) : inventory;
  return (
    <section className="table-section" id="inventory-watch">
      <SectionHeader kicker="SKU под наблюдением" title="Остатки, маржа, сигнал">
        <button className="ghost-button desktop-only" type="button" disabled={!onOpen} onClick={() => { if (onOpen && inventory[0]) onOpen(inventory[0]); }}>
          <Sparkles size={17} />
          <span>AI сводка</span>
        </button>
      </SectionHeader>

      {sharedSignal ? <p className="form-hint">{sharedSignal}</p> : null}
      {inventory.some(row => row.margin.trim().startsWith("−") || row.margin.trim().startsWith("-")) ? <p className="form-hint">Отрицательная маржа означает, что учтённые затраты превышают выручку. При небольшой выручке процент может быть большим; проверьте период и состав затрат.</p> : null}
      <div className="desktop-table">
        <table>
          <thead>
            <tr>
              <th>SKU</th>
              <th>Товар</th>
              <th>Запас</th>
              <th>Маржа</th>
              <th>Сигнал</th>
            </tr>
          </thead>
          <tbody>
            {displayRows.map((row) => (
            <InventoryTableRow row={row} key={row.sku} onOpen={onOpen ? () => onOpen(inventory.find(original => original.sku === row.sku) ?? row) : undefined} />
            ))}
          </tbody>
        </table>
      </div>

      <div className="mobile-sku-list">
        {displayRows.map((row) => (
          <article className="mobile-sku-card detail-trigger" key={row.sku} tabIndex={onOpen ? 0 : undefined} role={onOpen ? "button" : undefined} onClick={onOpen ? () => onOpen(inventory.find(original => original.sku === row.sku) ?? row) : undefined} onKeyDown={onOpen ? (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpen(inventory.find(original => original.sku === row.sku) ?? row); } } : undefined}>
            <div>
              <span>{row.sku}</span>
              <h3>{row.name}</h3>
            </div>
            <div className="mobile-sku-meta">
              <strong>{row.stock}</strong>
              <span className="mobile-sku-margin">Маржа {row.margin}</span>
              <StatusPill tone={row.tone}>{row.signal}</StatusPill>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function InventoryTableRow({ onOpen, row }: { onOpen?: () => void; row: InventoryWatchRow }) {
  return (
    <tr className={onOpen ? "detail-trigger" : undefined} tabIndex={onOpen ? 0 : undefined} onClick={onOpen} onKeyDown={onOpen ? (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpen(); } } : undefined}>
      <td>{row.sku}</td>
      <td>{row.name}</td>
      <td>{row.stock}</td>
      <td>{row.margin}</td>
      <td>
        <StatusPill tone={row.tone}>{row.signal}</StatusPill>
      </td>
    </tr>
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

function CoverageRow({ onOpen, row }: { onOpen?: () => void; row: StockCoverageRow }) {
  const content = <>
      <div>
        <strong>{row.label}</strong>
        <span>{row.detail}</span>
      </div>
      <div className="warehouse-meter">
        <span className={row.tone} style={{ width: `${row.width}%` }} />
      </div>
      <StatusPill tone={row.tone}>{row.value}</StatusPill>
    </>;
  return onOpen ? <button className="warehouse-row detail-trigger" type="button" onClick={onOpen}>{content}</button> : <div className="warehouse-row">{content}</div>;
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function FunnelBar({ label, onOpen, value, width }: { label: string; onOpen: () => void; value: string; width: number }) {
  return (
    <button className="funnel-bar detail-trigger" type="button" onClick={onOpen}>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
      <div>
        <span style={{ width: `${Math.max(width > 0 ? 4 : 0, Math.min(100,width))}%` }} />
      </div>
    </button>
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

function ProductCostDialog({
  busy,
  item,
  onClose,
  onSubmit,
}: {
  busy: boolean;
  item: ProductCostSetting;
  onClose: () => void;
  onSubmit: (item: ProductCostSetting, reason?: string) => void;
}) {
  const dialogRef=useDialogFocus(()=>{if(!busy)onClose();});
  const [reason, setReason] = useState("");
  const [cost, setCost] = useState(String(item.cost_per_wb_unit));
  const [note, setNote] = useState(item.note);
  const title = item.product_name || item.supplier_article || `WB ${item.nm_id}`;
  return <div className="drawer-backdrop operation-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section className="plan-drawer operation-dialog" role="dialog" aria-modal="true" ref={dialogRef} aria-labelledby="cost-dialog-title">
      <div className="drawer-header"><div><span className="eyebrow">экономика</span><h2 id="cost-dialog-title">Себестоимость товара</h2><p>{title} · WB {item.nm_id}</p></div><button className="icon-button" type="button" aria-label="Закрыть" disabled={busy} onClick={onClose}><X size={18} /></button></div>
      <form className="operation-form" onSubmit={(event) => { event.preventDefault(); onSubmit({ ...item, cost_per_wb_unit: Number(cost), note }, reason); }}>
        <label>Себестоимость одной единицы, ₽<input autoFocus min="0" required step="0.01" type="number" value={cost} onChange={(event) => setCost(event.target.value)} /></label>
        <label>Примечание<input value={note} onChange={(event) => setNote(event.target.value)} placeholder="Необязательно" /></label>
        <p className="plan-empty">Изменение применяется к расчёту маржи и прибыли. Исторические FIFO-слои не меняются.</p>
        <label>Причина изменения<textarea value={reason} maxLength={1000} onChange={(event) => setReason(event.target.value)} placeholder="Например, новая закупочная цена или уточнение расхода. Необязательно." /></label>
        <div className="operation-actions"><button className="ghost-button" disabled={busy} type="button" onClick={onClose}>Отмена</button><button className="primary-button" disabled={busy} type="submit"><CheckCircle2 size={16} />{busy ? "Сохраняем" : "Сохранить"}</button></div>
      </form>
    </section>
  </div>;
}

function ProductionNormDialog({ busy, item, onClose, onSubmit }: { busy: boolean; item: ProductionNorm; onClose: () => void; onSubmit: (item: ProductionNorm, reason?: string) => void }) {
  const dialogRef=useDialogFocus(()=>{if(!busy)onClose();});
  const [reason, setReason] = useState("");
  const [material, setMaterial] = useState(String(item.material_per_unit));
  const [batch, setBatch] = useState(String(item.min_batch));
  const [pack, setPack] = useState(String(item.pack_size));
  const [enabled, setEnabled] = useState(Boolean(item.enabled));
  return <div className="drawer-backdrop operation-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section className="plan-drawer operation-dialog" role="dialog" aria-modal="true" ref={dialogRef} aria-labelledby="norm-dialog-title">
      <div className="drawer-header"><div><span className="eyebrow">производство</span><h2 id="norm-dialog-title">Норма материала</h2><p>{item.product_name || item.supplier_article} · WB {item.nm_id}</p></div><button className="icon-button" type="button" aria-label="Закрыть" disabled={busy} onClick={onClose}><X size={18} /></button></div>
      <form className="operation-form" onSubmit={(event) => { event.preventDefault(); onSubmit({ ...item, material_per_unit: Number(material), min_batch: Number(batch), pack_size: Number(pack), enabled: enabled ? 1 : 0 }, reason); }}>
        <label>Расход на комплект, {item.material_unit || "ед."}<input required min="0" step="0.001" type="number" value={material} onChange={(event) => setMaterial(event.target.value)} /></label>
        <label>Минимальная партия, комплектов<input required min="1" step="1" type="number" value={batch} onChange={(event) => setBatch(event.target.value)} /></label>
        <label>Штук в комплекте<input required min="1" step="1" type="number" value={pack} onChange={(event) => setPack(event.target.value)} /></label>
        <label className="checkbox-field"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /> Использовать в производственном плане</label>
        <label>Причина изменения<textarea value={reason} maxLength={1000} onChange={(event) => setReason(event.target.value)} placeholder="Например, новая закупочная цена или уточнение расхода. Необязательно." /></label>
        <div className="operation-actions"><button className="ghost-button" disabled={busy} type="button" onClick={onClose}>Отмена</button><button className="primary-button" disabled={busy} type="submit"><CheckCircle2 size={16} />{busy ? "Сохраняем" : "Сохранить норму"}</button></div>
      </form>
    </section>
  </div>;
}

function SupplierDialog({ busy, item, onClose, onSubmit }: { busy: boolean; item: SupplierRecord; onClose: () => void; onSubmit: (item: SupplierRecord) => void }) {
  const dialogRef=useDialogFocus(()=>{if(!busy)onClose();});
  const [draft, setDraft] = useState(item);
  const update = (key: keyof SupplierRecord, value: string | number) => setDraft((current) => ({ ...current, [key]: value }));
  return <div className="drawer-backdrop operation-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section className="plan-drawer operation-dialog supplier-dialog" role="dialog" aria-modal="true" ref={dialogRef} aria-labelledby="supplier-dialog-title">
      <div className="drawer-header"><div><span className="eyebrow">справочник поставщиков</span><h2 id="supplier-dialog-title">{item.id ? "Редактировать поставщика" : "Добавить поставщика"}</h2><p>Контакты используются при оформлении закупок.</p></div><button className="icon-button" type="button" aria-label="Закрыть" disabled={busy} onClick={onClose}><X size={18} /></button></div>
      <form className="operation-form supplier-form" onSubmit={(event) => { event.preventDefault(); onSubmit(draft); }}>
        <label>Название поставщика<input required value={draft.name} onChange={(event) => update("name", event.target.value)} placeholder="Например, ООО Кромка+" /></label>
        <label>Контактное лицо<input value={draft.contact_person} onChange={(event) => update("contact_person", event.target.value)} placeholder="Имя менеджера" /></label>
        <div className="supplier-form-grid"><label>Телефон<input value={draft.phone} onChange={(event) => update("phone", event.target.value)} placeholder="+7 ..." /></label><label>Email<input type="email" value={draft.email} onChange={(event) => update("email", event.target.value)} placeholder="name@example.com" /></label><label>WeChat / Telegram<input value={draft.messenger} onChange={(event) => update("messenger", event.target.value)} placeholder="WeChat ID или @username" /></label><label>Страна<input value={draft.country} onChange={(event) => update("country", event.target.value)} placeholder="Китай / Россия" /></label><label>Срок оплаты, дней<input min="0" type="number" value={draft.payment_terms_days} onChange={(event) => update("payment_terms_days", Number(event.target.value))} /></label><label>Срок поставки, дней<input min="0" type="number" value={draft.lead_time_days} onChange={(event) => update("lead_time_days", Number(event.target.value))} /></label></div>
        <label>Примечание<textarea value={draft.note} onChange={(event) => update("note", event.target.value)} placeholder="Условия, реквизиты, комментарии" /></label>
        <label className="checkbox-field"><input type="checkbox" checked={Boolean(draft.active)} onChange={(event) => update("active", event.target.checked ? 1 : 0)} /> Активный поставщик</label>
        <div className="operation-actions"><button className="ghost-button" disabled={busy} type="button" onClick={onClose}>Отмена</button><button className="primary-button" disabled={busy} type="submit"><CheckCircle2 size={16} />{busy ? "Сохраняем" : "Сохранить поставщика"}</button></div>
      </form>
    </section>
  </div>;
}

function ProcurementPaymentDialog({
  busy,
  order,
  onClose,
  onSubmit,
}: {
  busy: boolean;
  order: Record<string, unknown>;
  onClose: () => void;
  onSubmit: (payment: { amount: number; payment_date: string; method: string; note: string }) => void;
}) {
  const dialogRef=useDialogFocus(()=>{if(!busy)onClose();});
  const outstanding = Math.max(0, Number(order.outstanding_amount ?? 0));
  const today = new Date().toISOString().slice(0, 10);
  return <div className="drawer-backdrop operation-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section className="plan-drawer operation-dialog" role="dialog" aria-modal="true" ref={dialogRef} aria-labelledby="payment-title">
      <div className="drawer-header"><div><span className="eyebrow">реальная операция</span><h2 id="payment-title">Внести оплату</h2><p>Закупка #{String(order.id)} · остаток {Math.round(outstanding).toLocaleString("ru-RU")} ₽</p></div><button className="icon-button" type="button" aria-label="Закрыть" disabled={busy} onClick={onClose}><X size={18} /></button></div>
      <form className="operation-form" onSubmit={(event) => { event.preventDefault(); const form = new FormData(event.currentTarget); onSubmit({ amount: Number(form.get("amount")), payment_date: String(form.get("payment_date")), method: String(form.get("method")), note: String(form.get("note") ?? "") }); }}>
        <label>Сумма оплаты<input name="amount" required min="0.01" max={outstanding || undefined} step="0.01" type="number" defaultValue={outstanding ? outstanding.toFixed(2) : ""} /></label>
        <label>Дата оплаты<input name="payment_date" required type="date" defaultValue={today} /></label>
        <label>Способ оплаты<select className="filter-select" name="method" defaultValue="Банк"><option>Банк</option><option>Наличные</option><option>Карта</option><option>Другое</option></select></label>
        <label>Комментарий<input name="note" placeholder="Необязательно" /></label>
        <div className="operation-actions"><button className="ghost-button" type="button" disabled={busy} onClick={onClose}>Отмена</button><button className="primary-button" disabled={busy || outstanding <= 0} type="submit"><CheckCircle2 size={16} />{busy ? "Сохраняем" : "Записать оплату"}</button></div>
      </form>
    </section>
  </div>;
}

function ProcurementReceiptDialog({
  busy,
  items,
  order,
  onClose,
  onSubmit,
}: {
  busy: boolean;
  items: Array<Record<string, unknown>>;
  order: Record<string, unknown>;
  onClose: () => void;
  onSubmit: (itemId: number, quantity: number) => void;
}) {
  const dialogRef=useDialogFocus(()=>{if(!busy)onClose();});
  const first = items[0];
  const [itemId, setItemId] = useState(String(first?.id ?? ""));
  const selected = items.find((item) => String(item.id) === itemId) ?? first;
  const remaining = Math.max(0, Number(selected?.quantity ?? 0) - Number(selected?.posted_quantity ?? 0));
  return <div className="drawer-backdrop operation-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section className="plan-drawer operation-dialog" role="dialog" aria-modal="true" ref={dialogRef} aria-labelledby="receipt-title">
      <div className="drawer-header"><div><span className="eyebrow">реальная операция</span><h2 id="receipt-title">Провести приёмку</h2><p>Закупка #{String(order.id)}. Остаток позиции будет оприходован в FIFO.</p></div><button className="icon-button" type="button" aria-label="Закрыть" disabled={busy} onClick={onClose}><X size={18} /></button></div>
      <form className="operation-form" onSubmit={(event) => { event.preventDefault(); onSubmit(Number(itemId), Number(new FormData(event.currentTarget).get("quantity"))); }}>
        <label>Позиция<select className="filter-select" value={itemId} onChange={(event) => setItemId(event.target.value)}>{items.map((item) => <option key={String(item.id)} value={String(item.id)}>{String(item.product_name || item.material_name || item.supplier_article || `Позиция #${String(item.id)}`)} · остаток {Math.max(0, Number(item.quantity ?? 0) - Number(item.posted_quantity ?? 0))}</option>)}</select></label>
        <label>Принято единиц<input name="quantity" required min="0.01" max={remaining || undefined} step="0.01" type="number" defaultValue={remaining || ""} /></label>
        <div className="operation-actions"><button className="ghost-button" type="button" disabled={busy} onClick={onClose}>Отмена</button><button className="primary-button" disabled={busy || !selected || remaining <= 0} type="submit"><CheckCircle2 size={16} />{busy ? "Проводим" : "Оприходовать"}</button></div>
      </form>
    </section>
  </div>;
}

function CreativeCard({ active = false, label, onOpen, value }: { active?: boolean; label: string; onOpen: () => void; value: string }) {
  return (
    <button className={cx("creative-card", "detail-trigger", active && "active")} type="button" onClick={onOpen}>
      <div className="creative-thumb">
        <Package size={22} />
      </div>
      <strong>{label}</strong>
      <span>{value}</span>
    </button>
  );
}

function StatusPill({ children, tone }: { children: ReactNode; tone: Tone }) {
  return <span className={cx("status-pill", tone)}>{children}</span>;
}

export default function SelectedShopApp(){return <ShopContextBoundary><App /></ShopContextBoundary>;}
