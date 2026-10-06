import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { apiHeaders, API_BASE_URL } from "./data-source";
import { scopedFetch as fetch } from "./selected-shop";
import "./OzonAdsAnalyticsPanel.css";

type Metrics = Record<"spend" | "views" | "clicks" | "orders" | "ad_revenue" | "ctr" | "cpc" | "cpm" | "cpo" | "drr", number | string | null>;
type Analytics = { loaded: boolean; has_data: boolean; source_complete: boolean; totals: Metrics; campaigns: Array<Metrics & { campaign_id: string }>; by_day: Array<Metrics & { day: string }>; by_day_available: boolean; loaded_at?: string };
type Job = { id: string; status: string; error_code: string | null; polls: number };
type Connection = { configured: boolean; error_code?: string };
const BASE = "/api/v1/ozon-accounting/advertising";
const DEFINITIONS = [
  ["spend", "Расход с НДС", "money"], ["views", "Показы", "number"], ["clicks", "Клики", "number"],
  ["orders", "Заказы из рекламы", "number"], ["ad_revenue", "Прямые продажи из отчёта", "money"],
  ["ctr", "CTR", "percent"], ["cpc", "CPC", "money"], ["cpm", "CPM", "money"], ["cpo", "CPO", "money"], ["drr", "ДРР прямых продаж", "percent"],
] as const;
export function formatOzonAdMetric(value: number | string | null | undefined, kind: string): string {
  if (kind === "money" && typeof value === "string" && /^\d+(\.\d{1,2})?$/.test(value)) {
    const [whole, fraction = ""] = value.split(".");
    return whole.replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0") + "," + fraction.padEnd(2, "0") + " ₽";
  }
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return value.toLocaleString("ru-RU", { maximumFractionDigits: kind === "number" ? 0 : 2 }) + (kind === "money" ? " ₽" : kind === "percent" ? " %" : "");
}
async function request<T>(path: string, signal: AbortSignal, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(API_BASE_URL + BASE + path, { method, signal, headers: apiHeaders(body !== undefined), ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  if (!response.ok) {
    if (response.status === 429) throw new Error("Лимит Performance API. Подождите и повторите проверку; сохранённый отчёт остаётся доступен.");
    const payload = await response.json().catch(() => null) as { detail?: string | { message?: string } } | null;
    const detail = payload?.detail;
    const message = typeof detail === "string" ? detail : detail?.message;
    throw new Error(message && message.length < 400 ? message : `Реклама Ozon: запрос не выполнен (HTTP ${response.status}).`);
  }
  return response.json() as Promise<T>;
}
function Table({ rows, daily }: { rows: Analytics["campaigns"] | Analytics["by_day"]; daily: boolean }) {
  return <div className="ozon-ads-scroll" role="region" aria-label={daily ? "Реклама Ozon по дням" : "Реклама Ozon по кампаниям"} tabIndex={0}>
    <table><caption>{daily ? "По дням, присутствующим в отчёте" : "Выбранные рекламные кампании"}</caption><thead><tr><th scope="col">{daily ? "День" : "ID кампании"}</th>{DEFINITIONS.map(([key, label]) => <th key={key} scope="col">{label}</th>)}</tr></thead>
      <tbody>{rows.map((row) => { const id = "day" in row ? row.day : row.campaign_id; return <tr key={id}><th scope="row">{id}</th>{DEFINITIONS.map(([key, , kind]) => <td key={key}>{formatOzonAdMetric(row[key], kind)}</td>)}</tr>; })}</tbody>
    </table>
  </div>;
}

export function OzonAdsAnalyticsPanel({ start, end, canWrite, isOwner }: { start: string; end: string; canWrite: boolean; isOwner: boolean }) {
  const [data, setData] = useState<Analytics | null>(null);
  const [connection, setConnection] = useState<Connection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [campaigns, setCampaigns] = useState("");
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const generation = useRef(0);
  const readVersion = useRef(0);
  const readAbort = useRef<AbortController | null>(null);
  const actionAbort = useRef<AbortController | null>(null);
  const query = new URLSearchParams({ start_date: start, end_date: end }).toString();
  const load = useCallback(async (epoch: number) => {
    readAbort.current?.abort();const controller = new AbortController();readAbort.current = controller;const version = ++readVersion.current;
    setLoading(true);
    try {
      const [analytics, state] = await Promise.all([request<Analytics>(`?start_date=${encodeURIComponent(start)}&end_date=${encodeURIComponent(end)}`, controller.signal), request<Connection>("/credentials", controller.signal)]);
      if (epoch !== generation.current || version !== readVersion.current) return;
      setData(analytics);setConnection(state);
    } catch (failure) { if (!controller.signal.aborted && epoch === generation.current) setError(failure instanceof Error ? failure.message : "Не удалось прочитать отчёт Ozon."); }
    finally { if (epoch === generation.current && version === readVersion.current) setLoading(false); }
  }, [start, end]);
  useEffect(() => {
    const epoch = ++generation.current;setData(null);setConnection(null);setJob(null);setSecret("");setBusy(false);setError(null);setNotice(null);void load(epoch);
    return () => { ++generation.current;readAbort.current?.abort();actionAbort.current?.abort(); };
  }, [load, canWrite, isOwner]);
  async function action(work: (signal: AbortSignal, epoch: number) => Promise<void>) {
    if (busy) return;
    const epoch = generation.current;const controller = new AbortController();actionAbort.current = controller;setBusy(true);setError(null);setNotice(null);
    try { await work(controller.signal, epoch); }
    catch (failure) { if (!controller.signal.aborted && epoch === generation.current) setError(failure instanceof Error ? failure.message : "Операция Ozon не выполнена."); }
    finally { if (epoch === generation.current) setBusy(false); }
  }
  function configure(event: FormEvent) {
    event.preventDefault();if (!isOwner || !canWrite) return;
    void action(async (signal, epoch) => { await request("/credentials", signal, "PUT", { client_id: clientId, client_secret: secret });if (epoch !== generation.current) return;setSecret("");setClientId("");setJob(null);setNotice("Ключ Performance сохранён защищённо. Теперь можно запросить отчёт.");await load(epoch); });
  }
  function create(event: FormEvent) {
    event.preventDefault();if (!canWrite) return;
    const ids = campaigns.split(/[\s,;]+/).filter(Boolean);
    if (ids.length < 1 || ids.length > 10 || new Set(ids).size !== ids.length || ids.some((id) => !/^[1-9][0-9]{0,19}$/.test(id) || (id.length === 20 && id > "18446744073709551615"))) { setError("Укажите от 1 до 10 разных ID кампаний через запятую.");return; }
    void action(async (signal, epoch) => { const next = await request<Job>("/reports", signal, "POST", { start_date: start, end_date: end, campaign_ids: ids });if (epoch !== generation.current) return;setJob(next);setNotice("Отчёт формируется. Нажмите «Проверить готовность», когда будете готовы проверить результат."); });
  }
  function poll() {
    if (!canWrite || !job) return;
    void action(async (signal, epoch) => { const next = await request<Job>(`/reports/${encodeURIComponent(job.id)}/poll`, signal, "POST");if (epoch !== generation.current) return;setJob(next);
      if (next.status === "complete") { setNotice("Рекламный отчёт загружен.");await load(epoch); }
      else if (next.error_code === "rate_limit") setNotice("Лимит Performance API. Повторите проверку позже; сохранённые данные не изменились.");
      else if (next.status === "pending") setNotice("Отчёт ещё формируется. Автоматических повторных запросов нет.");
      else setError("Отчёт не завершён. Сохранённые данные не изменились; проверьте ключ и создайте новый запрос.");
    });
  }
  function download() {
    void action(async (signal, epoch) => { const response = await fetch(`${API_BASE_URL}${BASE}/export.csv?${query}`, { signal, headers: apiHeaders() });if (!response.ok) throw new Error(`CSV Ozon недоступен (HTTP ${response.status}).`);const blob = await response.blob();if (epoch !== generation.current || signal.aborted) return;
      const url = URL.createObjectURL(blob);const anchor = document.createElement("a");anchor.href = url;anchor.download = `ozon-advertising-${start}-${end}.csv`;anchor.click();setTimeout(() => URL.revokeObjectURL(url), 0);
    });
  }
  return <section className="ozon-ads-panel" aria-labelledby="ozon-ads-title" aria-busy={loading || busy}>
    <header><div><h2 id="ozon-ads-title">Реклама Ozon</h2><p>{start} — {end} · Performance API</p></div><button type="button" onClick={download} disabled={busy || !data?.loaded}>Скачать CSV рекламы Ozon</button></header>
    <p className="ozon-ads-note">Отчёт по явно выбранным кампаниям. Расход включает НДС; ДРР рассчитан по прямым продажам рекламного отчёта. Продажи модели не добавляются к прямым продажам. Эти данные не равны выручке всего магазина.</p>
    {loading && <p role="status">Загрузка сохранённого отчёта…</p>}
    {error && <p className="ozon-ads-error" role="alert">{error}</p>}
    {notice && <p className="ozon-ads-notice" role="status">{notice}</p>}
    {connection && <p>Performance API: {connection.configured ? "ключ настроен" : "не подключён"}. Ключ Seller API для рекламы не используется.</p>}
    {isOwner && canWrite && <details className="ozon-ads-connect"><summary>{connection?.configured ? "Изменить подключение Performance API" : "Подключить Performance API"}</summary>
      <p>Отдельные данные сервиса находятся в <a href="https://seller.ozon.ru/app/settings/api-keys?currentTab=performanceApi" target="_blank" rel="noopener noreferrer">настройках Performance API Ozon</a>.</p>
      <form onSubmit={configure}><label>Client ID Performance<input autoComplete="off" value={clientId} onChange={(event) => setClientId(event.target.value)} required maxLength={2048} disabled={busy} /></label><label>Client Secret Performance<input type="password" autoComplete="new-password" value={secret} onChange={(event) => setSecret(event.target.value)} required maxLength={2048} disabled={busy} /></label><button disabled={busy} type="submit">Сохранить ключ Performance</button></form>
      {connection?.configured && <button type="button" disabled={busy} onClick={() => { void action(async (signal, epoch) => { await request("/credentials", signal, "DELETE");if (epoch !== generation.current) return;setSecret("");setClientId("");setJob(null);await load(epoch); }); }}>Отключить Performance API</button>}
    </details>}
    {canWrite && connection?.configured && <form className="ozon-ads-report-form" onSubmit={create}><label>ID кампаний Performance<input value={campaigns} onChange={(event) => setCampaigns(event.target.value)} placeholder="101, 102" autoComplete="off" disabled={busy} aria-describedby="ozon-campaign-help" /></label><p id="ozon-campaign-help">От 1 до 10 ID через запятую. Запрос создаёт только отчёт и не меняет ставки или бюджеты.</p><button type="submit" disabled={busy}>Создать рекламный отчёт</button></form>}
    {!canWrite && <p className="ozon-ads-note">Загрузка отчётов доступна владельцу и менеджеру, подключение — владельцу. Сохранённый отчёт можно просматривать.</p>}
    {job && <div className="ozon-ads-job"><span>Состояние: {job.status === "pending" ? "формируется" : job.status === "complete" ? "готов" : "не завершён"}</span>{canWrite && job.status === "pending" && <button type="button" disabled={busy} onClick={poll}>Проверить готовность</button>}</div>}
    {data && !data.loaded && <p className="ozon-ads-empty">Рекламный отчёт за этот период ещё не загружен. Отсутствие данных не означает нулевой расход.</p>}
    {data?.loaded && !data.source_complete && <p className="ozon-ads-note">Не все показатели подтверждены отчётом. Недоступные значения показаны знаком «—».</p>}
    {data?.loaded_at && <p className="ozon-ads-note">Сохранённый отчёт обновлён: {new Date(data.loaded_at).toLocaleString("ru-RU")}</p>}
    {data && <dl className="ozon-ads-kpis">{DEFINITIONS.map(([key, label, kind]) => <div key={key}><dt>{label}</dt><dd>{formatOzonAdMetric(data.totals[key], kind)}</dd></div>)}</dl>}
    {Boolean(data?.campaigns.length) && <Table rows={data!.campaigns} daily={false} />}
    {data?.loaded && (data.by_day_available ? <Table rows={data.by_day} daily /> : <p className="ozon-ads-note">В полученном отчёте нет дневной детализации; распределение по дням не подставляется.</p>)}
  </section>;
}
