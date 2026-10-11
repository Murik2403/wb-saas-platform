import { wbAvailabilityMessage } from "./cabinet-copy";
import { downloadScoped } from "./selected-shop";
import { fifoReasonLabel } from "./fifo-reason";
import { WorkspaceTabs } from "./WorkspaceTabs";
import { FinanceProfitHeading } from "./FinanceProfitHeading";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Download, PackageCheck, Plus, ShieldCheck, WalletCards } from "lucide-react";
import type { DashboardMetric, ExpenseSplit, FinanceLedgerItem } from "./contracts";
import { financeExportUrl, getFinanceFifoReconciliationPage, type DashboardPeriod, type FinanceFifoDetailPage } from "./data-source";
import { LazyPanel } from "./LazyPanel";
import { lazyNamed } from "./LazyPanel";
import { DashboardPeriodControl, MetricGrid, SectionHeader, StatusPill, cx } from "./cabinet-shared";
const ManagementPnlSummary = lazyNamed(() => import("./ManagementPnlSummary"), "ManagementPnlSummary");
const ManagementPnlMonthly = lazyNamed(() => import("./ManagementPnlMonthly"), "ManagementPnlMonthly");
const TaxReconciliationPanel = lazyNamed(() => import("./TaxReconciliationPanel"), "TaxReconciliationPanel");
const ShopWorkspaces = lazyNamed(() => import("./ShopWorkspaces"), "ShopWorkspaces");
const PriceScenarios = lazyNamed(() => import("./PriceScenarios"), "PriceScenarios");

const financeTabs = [{ id: "overview", label: "Обзор" }, { id: "money", label: "Счета и кассовые окна" }, { id: "tax", label: "Налоги" }, { id: "costs", label: "Расходы и себестоимость" }, { id: "accounts", label: "Магазины и сценарии" }] as const;
const financeTargetMap: Record<string, string> = { "tax-reconciliation": "tax" };

export function FinanceView({
  target,
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
  target?: string | null;
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
  // ?view=finance#tax-reconciliation arrives without an in-app target, so fall back to the URL hash.
  const tabTarget = target ?? (typeof window !== "undefined" && window.location.hash === "#tax-reconciliation" ? "tax-reconciliation" : null);
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
      <WorkspaceTabs tabs={financeTabs} ariaLabel="Разделы финансов" storageKey="mh.tab.finance" target={tabTarget} targetMap={financeTargetMap} keepMounted={["tax", "accounts"]} panels={{
        overview: <>
      <LazyPanel label="управленческий ОПиУ"><ManagementPnlSummary period={period} /></LazyPanel>
      <section className="work-section finance-movement-panel" aria-label="Денежное движение">
        <SectionHeader kicker="P&L" title="Денежное движение">
          <DashboardPeriodControl period={period} setPeriod={setPeriod} />
        </SectionHeader>
          {financeLedger.length ? <div className="ledger-list">
            {financeLedger.map((item) => <FinanceLedgerRow item={item} key={item.label} onOpen={() => onAction("Отчет", item.label)} />)}
          </div> : <p className="plan-empty">Нет данных о движении денег</p>}
      </section>
      <LazyPanel label="ОПиУ по месяцам"><ManagementPnlMonthly period={period} onOpenTaxSettings={() => onAction("Налоги", "")} /></LazyPanel>
        </>,
        money: <>
      {cashflow}
          <section className="panel">
            <SectionHeader compact kicker="Контроль" title="Кассовые окна">
              <button className="icon-button" type="button" aria-label="Сформировать финансовый отчёт" onClick={() => onAction("Отчет", "финансы")}><ShieldCheck size={20} /></button>
            </SectionHeader>
            {cashWindows.length ? <div className="cash-grid">{cashWindows.map((item) => <button className="stat detail-trigger" type="button" key={item.label} onClick={() => onAction("Отчет", item.label)}><span>{item.label}</span><strong>{item.value}</strong><small>{item.detail}</small></button>)}</div> : <p className="plan-empty">Нет загруженных финансовых операций</p>}
          </section>
        </>,
        tax: <>
      <LazyPanel label="сверку налогов"><TaxReconciliationPanel /></LazyPanel>
        </>,
        costs: <>
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
        </>,
        accounts: <>
      <details className="cabinet-disclosure"><summary>Магазины WB</summary><LazyPanel label="магазины"><ShopWorkspaces /></LazyPanel></details>
      <p>Кабинет Ozon открывается переключателем площадки вверху страницы.</p>
      <details className="cabinet-disclosure"><summary>Сценарии цены и закупки</summary><LazyPanel label="сценарии цен"><PriceScenarios /></LazyPanel></details>
      {selfBuyouts}
        </>,
      }} />
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
