import { plural, cabinetDate, cabinetText } from "./cabinet-copy";
import { WorkspaceTabs } from "./WorkspaceTabs";
import { useEffect, useState } from "react";
import { BarChart3, Bell, CalendarDays, CheckCircle2, Download, Pencil, Plus, Play, Trash2 } from "lucide-react";
import type { DashboardMetric, ExportItem, ReportCard, Tone } from "./contracts";
import { createReportDefinition, updateReportDefinition, setReportDefinitionEnabled, deleteReportDefinition, downloadSavedReport, generateReportDefinition, getReportDelivery, getReportOperations, getAnalyticsDashboard, requestTelegramLinkCode, type AnalyticsDashboard, type ReportDefinition, type ReportDelivery, type ReportOperations } from "./data-source";
import { MetricGrid, SectionHeader, StatusPill, askConfirm, askText, cx, gatewayUrlFor } from "./cabinet-shared";

const reportsTabs = [{ id: "reports", label: "Отчёты" }, { id: "schedules", label: "Расписания и запуски" }, { id: "delivery", label: "Доставка" }] as const;

export function ReportsView({
  exportItems,
  onAction,
  onQuickReport,
  reportCards,
  reportMetrics,
  target,
  canLinkTelegram = false,
}: {
  exportItems: ExportItem[];
  onAction: (label: string, itemLabel: string) => void;
  onQuickReport: (name: string, days: number, metrics: string[]) => Promise<void>;
  reportCards: ReportCard[];
  reportMetrics: DashboardMetric[];
  target?: string | null;
  canLinkTelegram?: boolean;
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
  const linkTelegram = async () => { if (!canLinkTelegram) return; setMessage(null); try { const result = await requestTelegramLinkCode(); setTelegramCode({ code: result.code, ttl: result.ttl_minutes, link: result.deep_link }); } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось получить код Telegram"); } };
  const runDefinition = async (id: number) => { setBusyId(id); setMessage(null); try { await generateReportDefinition(id); await refreshOperations(); setMessage("Отчёт поставлен в очередь. Статус обновится автоматически."); } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось запустить отчёт"); } finally { setBusyId(null); } };
  const quickReportMetrics: Record<string, string[]> = {
    "Операционный отчёт": ["sales_orders", "pnl", "stocks", "oos_risk", "ads"],
    "Остатки и пополнение": ["stocks", "oos_risk", "stock_in_transit", "frozen_capital"],
    "Реклама и эффективность": ["ads", "ad_performance", "organic_vs_ads", "ad_funnel"],
  };
  const runQuickReport = async (name: string) => { setBusyId(-2); setMessage(null); try { await onQuickReport(name, quickDays, quickReportMetrics[name] ?? ["sales_orders", "ads", "stocks"]); await refreshOperations(); setMessage("Отчёт поставлен в очередь. Статус обновится автоматически."); } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось запустить PDF"); } finally { setBusyId(null); } };
  const removeDefinition = async (id: number) => { if (!await askConfirm("Удалить расписание и его историю запусков?")) return; setBusyId(id); setMessage(null); try { await deleteReportDefinition(id); await refreshOperations(); } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось удалить расписание"); } finally { setBusyId(null); } };
  const editDefinition = async (report: ReportDefinition) => { const name = await askText("Название расписания", report.name); if (name === null) return; const time = await askText("Время МСК (ЧЧ:ММ)", report.schedule_time); if (time === null) return; const emailChoice = await askText("Отправлять на Email? (да/нет)", report.email_enabled ? "да" : "нет"); if (emailChoice === null) return; const telegramChoice = delivery.telegram_paused ? (report.telegram_enabled ? "да" : "нет") : await askText("Отправлять в Telegram? (да/нет)", report.telegram_enabled ? "да" : "нет"); if (telegramChoice === null) return; const emailEnabled = /^да|yes|1|true$/i.test(emailChoice.trim()); const telegramEnabled = /^да|yes|1|true$/i.test(telegramChoice.trim()); if (emailEnabled && !delivery.email_configured) { setMessage("Email не настроен в профиле"); return; } if (telegramEnabled && !delivery.telegram_paused && !delivery.telegram_linked) { setMessage("Сначала привяжите Telegram в блоке доставки"); return; } let metrics: string[] = []; try { metrics = JSON.parse(report.metrics); } catch { setMessage("У расписания повреждён список разделов"); return; } setBusyId(report.id); setMessage(null); try { await updateReportDefinition(report.id, { name, metrics, schedule_type: report.schedule_type, schedule_time: time, schedule_weekday: report.schedule_weekday, schedule_day: report.schedule_day, email_enabled: emailEnabled, telegram_enabled: telegramEnabled }); await refreshOperations(); setMessage("Расписание обновлено"); } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось обновить расписание"); } finally { setBusyId(null); } };
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
            {operationsError ? <div className="data-banner stale" role="status">Расписания недоступны: {operationsError}</div> : operations ? (operations.definitions.length ? <div className="report-list">{operations.definitions.map((report) => <article className={cx("report-row", report.enabled ? "good" : "paused")} key={report.id}><div><span>{report.schedule_type === "daily" ? "Ежедневно" : report.schedule_type === "weekly" ? "Еженедельно" : "Ежемесячно"} · {report.schedule_time} МСК · {definitionMetricCount(report)} разделов</span><h3>{report.name}</h3><p>Последний запуск: {cabinetDate(report.last_run_at)} · {report.email_enabled ? "Email" : "без email"} · {delivery.telegram_paused ? "доставка в мессенджер на паузе" : report.telegram_enabled ? "Telegram" : "без Telegram"}</p></div><StatusPill tone={report.enabled ? "good" : "warn"}>{report.enabled ? "включён" : "пауза"}</StatusPill><div className="action-controls"><button className="icon-button" aria-label={report.enabled ? "Поставить на паузу" : "Возобновить расписание"} title={report.enabled ? "Поставить на паузу" : "Возобновить расписание"} disabled={busyId === report.id} type="button" onClick={() => void toggleDefinition(report)}><Play size={16} /></button><button className="icon-button" aria-label="Изменить расписание" title="Изменить расписание" disabled={busyId === report.id} type="button" onClick={() => void editDefinition(report)}><Pencil size={16} /></button><button className="icon-button" aria-label="Сформировать PDF сейчас" title="Сформировать PDF сейчас" disabled={busyId === report.id} type="button" onClick={() => void runDefinition(report.id)}><Play size={16} /></button><button className="icon-button" aria-label="Удалить расписание" title="Удалить расписание" disabled={busyId === report.id} type="button" onClick={() => void removeDefinition(report.id)}><Trash2 size={16} /></button></div></article>)}</div> : <p className="plan-empty">Расписаний пока нет.</p>) : <p className="plan-empty">Загружаем расписания…</p>}
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
            <SectionHeader compact kicker="Доставка" title={delivery.telegram_paused ? "Доставка по email" : "Email и Telegram"}><Bell size={20} /></SectionHeader>
            {deliveryError ? <div className="data-banner stale" role="status">Настройки доставки недоступны: {deliveryError}</div> : null}
            <div className="delivery-settings">
              <div><strong>Email</strong><span>{delivery.email_configured ? `${delivery.email || "настроен"} · адрес аккаунта` : "SMTP не настроен"}</span></div>
              {!delivery.telegram_paused ? <div><strong>Telegram</strong><span>{delivery.telegram_linked ? `привязан к @${delivery.telegram_bot_username}` : delivery.telegram_configured ? `не привязан · @${delivery.telegram_bot_username}` : "бот не настроен"}</span></div> : null}
            </div>
            {delivery.email_configured ? <p className="form-hint">Email для отчётов — адрес аккаунта. Изменить его можно в разделе <a href={gatewayUrlFor("/account")}>«Аккаунт»</a>; код подтверждения здесь не вводится.</p> : null}
            {delivery.telegram_configured ? <div className="telegram-link-box"><button className="secondary-button" type="button" disabled={!canLinkTelegram} title={!canLinkTelegram ? "Получателя Telegram изменяет только владелец" : undefined} onClick={() => void linkTelegram()}>{delivery.telegram_linked ? "Привязать другой Telegram" : "Привязать Telegram"}</button>{telegramCode ? <><p className="form-hint">Как привязать Telegram:</p><ol className="form-hint telegram-link-steps"><li>Нажмите «Открыть Telegram и привязать» — откроется чат бота <strong>@{delivery.telegram_bot_username}</strong>.</li><li>В Telegram нажмите «Запустить» (Start). Ссылка передаст код боту. Если ссылка не открылась, в чате этого же бота отправьте <code>/link {telegramCode.code}</code>.</li><li>Нажмите «Обновить статус» после ответа бота. Код одноразовый и действует {telegramCode.ttl} минут; оператору или в личные сообщения его отправлять не нужно.</li></ol>{telegramCode.link ? <a className="primary-link-button" href={telegramCode.link} rel="noreferrer" target="_blank">Открыть Telegram и привязать</a> : <p className="form-hint">Откройте чат бота <strong>@{delivery.telegram_bot_username}</strong> и отправьте <code>/link {telegramCode.code}</code>.</p>}</> : null}<button className="ghost-button" type="button" onClick={() => void refreshDelivery()}>Обновить статус</button></div> : null}
          </section>
        </aside>
      </div>);
  return (
    <div className="workspace-view">
      {message ? <p className="operation-error" role="status">{message}</p> : null}
      {showForm ? <section className="work-section report-form-section"><SectionHeader kicker="Расписание" title="Новый отчёт"><CalendarDays size={18} /></SectionHeader><form className="settings-form" onSubmit={(event) => { event.preventDefault(); void createDefinition(event.currentTarget); }}><label className="operation-field"><span>Название</span><input name="name" required placeholder="Еженедельная сводка" /></label><label className="operation-field"><span>Периодичность</span><select className="filter-select" name="schedule_type" value={scheduleType} onChange={(event) => setScheduleType(event.target.value as "daily" | "weekly" | "monthly")}><option value="daily">Ежедневно</option><option value="weekly">Еженедельно</option><option value="monthly">Ежемесячно</option></select></label><label className="operation-field"><span>Время МСК</span><input name="schedule_time" required type="time" defaultValue="09:00" /></label>{scheduleType === "weekly" ? <label className="operation-field"><span>День недели</span><select className="filter-select" name="schedule_weekday" defaultValue="0"><option value="0">Понедельник</option><option value="1">Вторник</option><option value="2">Среда</option><option value="3">Четверг</option><option value="4">Пятница</option><option value="5">Суббота</option><option value="6">Воскресенье</option></select></label> : null}{scheduleType === "monthly" ? <label className="operation-field"><span>Число месяца</span><input name="schedule_day" min="1" max="28" type="number" defaultValue="1" /></label> : null}<fieldset className="report-metrics"><legend>Что включить</legend>{metricOptions.map(([code, label]) => <label key={code}><input defaultChecked name="metrics" type="checkbox" value={code} />{label}</label>)}</fieldset><fieldset className="report-metrics"><legend>Доставка</legend><label><input defaultChecked={delivery.email_configured} disabled={!delivery.email_configured} name="email_enabled" type="checkbox" />Email{delivery.email_configured ? ` · ${delivery.email || "аккаунт"}` : " · не настроен"}</label>{!delivery.telegram_paused ? <label><input defaultChecked={delivery.telegram_linked} disabled={!delivery.telegram_linked} name="telegram_enabled" type="checkbox" />Telegram{delivery.telegram_linked ? " · привязан" : " · сначала привяжите чат"}</label> : null}</fieldset><div className="operation-actions"><button className="ghost-button" type="button" onClick={() => setShowForm(false)}>Отмена</button><button className="primary-button" disabled={busyId === -1} type="submit"><CheckCircle2 size={16} />{busyId === -1 ? "Сохраняем" : "Сохранить расписание"}</button></div></form></section> : null}
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
