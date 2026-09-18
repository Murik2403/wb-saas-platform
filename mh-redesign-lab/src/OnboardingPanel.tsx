import { useEffect, useState } from "react";
import { CheckCircle2 } from "lucide-react";
import { getActivationSummary, getFirstGrowthReport, recordFirstReportViewed, type ActivationSummary, type FirstGrowthReport, type ProductCostSetting, type TenantSyncRun } from "./data-source";
import { onboardingEvidence } from "./onboarding";

type Props = {
  connected: boolean;
  runs: TenantSyncRun[];
  costs: ProductCostSetting[];
  days: number;
  busy: boolean;
  token: string;
  onTokenChange: (value: string) => void;
  onConnect: () => void;
  onDaysChange: (value: number) => void;
  onSync: () => void;
  onEditCost: (cost: ProductCostSetting) => void;
};

function FirstReportCard({ report, onRecorded }: { report: FirstGrowthReport; onRecorded: () => void }) {
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let current = true;
    // Run only after this actual report has been committed to the screen.
    const frame = requestAnimationFrame(() => {
      void recordFirstReportViewed(report).then(() => {
        if (current) { setSaved(true); setError(""); onRecorded(); }
      }).catch(() => { if (current) setError("Отчёт открыт, но отметку первого результата сохранить не удалось. Повторите сохранение."); });
    });
    return () => { current = false; cancelAnimationFrame(frame); };
  }, [report.report_revision, retry]);
  const rub = (value: number) => `${value.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ₽`;
  return <section className="first-report-card" aria-labelledby="first-report-title">
    <h3 id="first-report-title">Первый отчёт: {report.product}</h3>
    <p className="form-hint">Артикул WB {report.nm_id} · {report.period.start} — {report.period.end}</p>
    <dl className="first-report-metrics">
      <div><dt>Выручка</dt><dd>{rub(report.revenue)}</dd></div>
      <div><dt>Продажи</dt><dd>{report.sales.toLocaleString("ru-RU")} шт.</dd></div>
      <div><dt>Себестоимость единицы</dt><dd>{rub(report.unit_cost)}</dd></div>
      <div><dt>Прибыль по учтённым расходам</dt><dd>{rub(report.margin)}</dd></div>
      <div><dt>Маржинальность</dt><dd>{report.margin_pct.toLocaleString("ru-RU", { maximumFractionDigits: 1 })}%</dd></div>
    </dl>
    <p className="form-hint">{report.limitation}</p>
    {saved ? <p role="status">Первый результат сохранён. Дальше можно настроить регулярные отчёты.</p> : null}
    {error ? <div role="status"><p>{error}</p><button className="ghost-button" type="button" onClick={() => setRetry((value) => value + 1)}>Повторить сохранение</button></div> : null}
    <a className="ghost-button" href="?view=reports">Настроить регулярные отчёты</a>
  </section>;
}

export function OnboardingPanel(props: Props) {
  const [nmId, setNmId] = useState<number | null>(null);
  const [summary, setSummary] = useState<ActivationSummary | null>(null);
  const [report, setReport] = useState<FirstGrowthReport | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const evidence = onboardingEvidence(props.runs, props.costs, nmId);
  useEffect(() => {
    let current = true;
    setSummary(null); setReport(null); setError("");
    void getActivationSummary(nmId).then((value) => { if (current) setSummary(value); })
      .catch((reason) => { if (current) setError(reason instanceof Error ? reason.message : "Не удалось проверить готовность отчёта"); });
    return () => { current = false; };
  }, [props.connected, props.runs, props.costs, nmId, refresh]);

  const openReport = async () => {
    if (!nmId || !summary?.ready_for_report) return;
    setLoading(true); setError(""); setReport(null);
    try { setReport(await getFirstGrowthReport(nmId)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось открыть отчёт"); }
    finally { setLoading(false); }
  };
  // Server reads the complete history; the legacy settings endpoint sends
  // only 14 runs. Never infer activation from either the history or readiness.
  const syncComplete = summary?.sync.complete ?? evidence.syncComplete;
  const syncState = summary?.sync.state ?? evidence.latest?.status;
  const syncMessage = syncComplete ? "История магазина успешно загружена"
    : syncState === "error" ? "Последняя загрузка завершилась с ошибкой. Проверьте журнал и права WB, затем повторите."
      : syncState === "partial" ? "Загрузка частичная. Проверьте недоступные разделы в журнале и повторите полную загрузку."
        : syncState === "running" ? "Загрузка ещё идёт. Дождитесь результата."
          : evidence.syncMessage;
  useEffect(() => {
    if (syncState !== "running") return;
    const timer = window.setTimeout(() => setRefresh((value) => value + 1), 5000);
    return () => window.clearTimeout(timer);
  }, [syncState, refresh]);
  const costComplete = summary?.selected_product.cost_configured ?? evidence.costComplete;
  const done = summary?.first_value_verified;
  if (done && !report) return <section className="work-section"><p>Первый результат получен. Подключения и себестоимость можно изменить ниже.</p></section>;
  return <section className="work-section onboarding-panel" id="onboarding" aria-labelledby="onboarding-title">
    <h2 id="onboarding-title">Первый результат на ваших данных</h2>
    <p className="form-hint">Загрузите историю, выберите один товар, сохраните полную себестоимость единицы и откройте расчёт.</p>
    {summary?.is_demo ? <p role="status">Это демонстрационные данные. Первый результат учитывается только в вашем кабинете.</p> : null}
    <div className="onboarding-steps">
      <article className={`onboarding-step ${props.connected ? "done" : "current"}`}>
        <span className="onboarding-step-number">1</span><div><strong>Подключить WB</strong><small>{props.connected ? "Токен проверен и сохранён" : "Без токена данные не загружаются"}</small></div>
        {props.connected ? <CheckCircle2 size={18} /> : <div className="onboarding-inline-form"><input aria-label="WB API-токен для первого запуска" autoComplete="off" type="password" value={props.token} onChange={(event) => props.onTokenChange(event.target.value)} placeholder="WB API-токен" /><button className="primary-button" disabled={props.busy} type="button" onClick={props.onConnect}>Проверить и сохранить</button></div>}
      </article>
      <article className={`onboarding-step ${!props.connected ? "locked" : syncComplete ? "done" : "current"}`}>
        <span className="onboarding-step-number">2</span><div><strong>Загрузить историю</strong><small>{syncMessage}</small></div>
        {syncComplete ? <CheckCircle2 size={18} /> : props.connected ? <div className="onboarding-inline-form"><input aria-label="Глубина первой загрузки, дней" min="7" max="90" type="number" value={props.days} onChange={(event) => props.onDaysChange(Number(event.target.value))} /><span>дней</span><button className="secondary-button" disabled={props.busy || syncState === "running"} type="button" onClick={props.onSync}>Загрузить историю</button></div> : null}
      </article>
      <article className={`onboarding-step ${!syncComplete ? "locked" : costComplete ? "done" : "current"}`}>
        <span className="onboarding-step-number">3</span><div><strong>Выбрать товар и проверить себестоимость</strong><small>Укажите всю себестоимость единицы: закупку или сырьё, производство и упаковку. Нулевое значение не считается заполненным.</small></div>
        <div className="onboarding-inline-form"><select aria-label="Товар для первого отчёта" value={nmId ?? ""} disabled={!syncComplete || loading} onChange={(event) => setNmId(event.target.value ? Number(event.target.value) : null)}><option value="">Выберите товар</option>{props.costs.map((cost) => <option key={cost.nm_id} value={cost.nm_id}>{cost.product_name || cost.supplier_article || cost.nm_id} · WB {cost.nm_id}</option>)}</select>{evidence.selected ? <button className="ghost-button" disabled={props.busy} type="button" onClick={() => props.onEditCost(evidence.selected!)}>{costComplete ? "Проверить стоимость" : "Указать стоимость"}</button> : null}</div>
        {syncComplete && !props.costs.length ? <p className="form-hint">Товары ещё не появились. Проверьте каталог и историю загрузки ниже.</p> : null}
      </article>
      <article className={`onboarding-step ${summary?.ready_for_report ? "current" : "locked"}`}>
        <span className="onboarding-step-number">4</span><div><strong>Открыть первый отчёт</strong><small>{summary?.next_step === "financial_data" ? "По выбранному товару нет реализации WB. Выберите проданный товар или загрузите нужный период." : "Покажем экономику выбранного товара за 30 дней до последней доступной реализации."}</small></div>
        <button className="primary-button" disabled={!summary?.ready_for_report || loading || props.busy} type="button" onClick={() => void openReport()}>{loading ? "Рассчитываем…" : "Открыть первый отчёт"}</button>
      </article>
    </div>
    {error ? <div role="status"><p className="operation-error">{error}</p><button className="ghost-button" type="button" onClick={() => setRefresh((value) => value + 1)}>Повторить проверку</button></div> : null}
    {report ? <FirstReportCard key={report.report_revision} report={report} onRecorded={() => setSummary((current) => current ? { ...current, first_value_verified: true } : current)} /> : null}
  </section>;
}
