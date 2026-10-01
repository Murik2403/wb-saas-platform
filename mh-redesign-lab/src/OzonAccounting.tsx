import { previewMoney } from "./ozon-preview-money";
import { scopedFetch as fetch, currentShop, navigateToShop } from "./selected-shop";
import { useEffect, useRef, useState } from "react";
import { API_BASE_URL, apiHeaders, getDashboardSession } from "./data-source";
import "./OzonAccounting.css";
import { OzonHistoricalPnl } from "./OzonHistoricalPnl";

type CostMapping = { mode: "manual" | "wb_estimate"; unit_cost_text: string | null; nm_id: number | null };
type Product = { sku: string; offer_id: string; product_id: number; stocks: Array<{ present?: number; reserved?: number; type?: string }>; cost_mapping?: CostMapping | null };
type FeePreview = { reconciled_total_rub: string | null; rows: Array<{ source_occurrence_key: string; accrual_id?: number; date?: string; category?: string; reconciled: boolean; component_sum_rub: string | null; reason?: string; components: Array<{ source_path: string; type_id: number; name: string; sku: string | null; amount_rub: string }> }> };
type PostingEvidence = { loaded: boolean; source_row_count: number; display_truncated?: boolean; scope?: { requested_number_count?: number; missing_number_count?: number }; rows: Array<{ source_occurrence_key: string; posting_number?: string; accrual_date?: string; sku_text: string | null; type_id_text: string | null; quantity_text: string | null; accrued_rub: string | null; seller_price_rub: string | null; reason?: string }> };
type Workspace = { posting_accrual_evidence?: PostingEvidence; fee_preview?: FeePreview; configured?: boolean; loaded: boolean; source_complete: boolean; source_collection_complete?: boolean; financial_normalization_verified?: boolean; accounting_complete: boolean; synced_at?: string; net_accrual_rub: string | null; estimated_cogs_rub?: string; estimate_after_known_cogs_rub?: string | null; profit_rub: string | null; sales_and_return_units?: number; known_cost_units?: number; products: Product[]; blockers: string[]; source_counts?: Record<string, number>; fees?: Array<{ type_id: string; name: string; amount_rub: string; mapped: boolean }>; returns_scope?: string };
const iso = (day: Date) => `${day.getFullYear()}-${String(day.getMonth()+1).padStart(2,"0")}-${String(day.getDate()).padStart(2,"0")}`;
const previous = (days: number) => { const day = new Date(); day.setDate(day.getDate()-days); return iso(day); };
const money = (value: string | null | undefined) => value === null || value === undefined ? "не рассчитано" : `${Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ₽`;
async function request<T>(path: string, body?: unknown, method = "GET"): Promise<T> {
  const result = await fetch(`${API_BASE_URL}/api/v1/ozon-accounting${path}`, { method, headers: apiHeaders(body !== undefined), ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  if (!result.ok) { const error = await result.json().catch(() => ({})); throw new Error(typeof error.detail === "string" ? error.detail : `Ozon: HTTP ${result.status}`); }
  return result.json() as Promise<T>;
}

export function OzonAccounting() {
  if ((currentShop()?.id ?? 0) !== 0) return <section className="panel ozon-accounting" aria-labelledby="ozon-accounting-title">
    <h2 id="ozon-accounting-title">Учёт Ozon</h2>
    <p>Учёт Ozon доступен в основном кабинете. У дополнительного WB-магазина нет подтверждённой привязки к продавцу Ozon.</p>
    <button type="button" className="secondary-button" onClick={() => navigateToShop(0)}>Переключиться на основной магазин</button>
  </section>;
  return <PrimaryOzonAccounting />;
}

function PrimaryOzonAccounting() {
  const [start, setStart] = useState(() => previous(7));
  const [end, setEnd] = useState(() => previous(1));
  const [data, setData] = useState<Workspace | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [canWrite, setCanWrite] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, { mode: string; value: string }>>({});
  const generation = useRef(0);
  const period = `?start_date=${start}&end_date=${end}`;
  const refresh = async () => {
    const current = ++generation.current;
    setBusy(true); setError(null);
    try { const result = await request<Workspace>(`/workspace${period}`); if (current === generation.current) { setData(result); setDrafts({}); } }
    catch (caught) { if (current === generation.current) setError(caught instanceof Error ? caught.message : "Не удалось прочитать Ozon"); }
    finally { if (current === generation.current) setBusy(false); }
  };
  useEffect(() => { void getDashboardSession().then(session => setCanWrite(session.mode !== "demo" && ["owner", "manager"].includes(session.user.role.toLowerCase()))).catch(() => setCanWrite(false)); }, []);
  useEffect(() => { setData(null); void refresh(); return () => { ++generation.current; }; }, [start, end]);
  const sync = async () => {
    if (busy || !canWrite) return;
    setBusy(true); setError(null);
    try { const result = await request<{ workspace: Workspace }>("/sync", { start_date: start, end_date: end }, "POST"); setData({ ...result.workspace, configured: true }); setDrafts({}); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Загрузка Ozon не завершена"); }
    finally { setBusy(false); }
  };
  const save = async (product: Product) => {
    const draft = drafts[product.sku]; if (!draft || busy || !canWrite) return;
    setBusy(true); setError(null);
    try { await request("/cost", { sku: product.sku, mode: draft.mode, ...(draft.mode === "manual" ? { unit_cost: draft.value.replace(",", ".") } : { nm_id: Number(draft.value) }) }, "PUT"); await refresh(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Себестоимость не сохранена"); setBusy(false); }
  };
  return <section className="panel ozon-accounting" aria-labelledby="ozon-accounting-title">
    <h2 id="ozon-accounting-title">Учёт Ozon</h2>
    <p>Отдельный учёт начислений Ozon. Продажи и остатки WB сюда не объединяются. Итог начислений включает поступления и удержания маркетплейса; это не выручка.</p>
    <div className="ozon-controls"><label>Ozon: с даты<input type="date" value={start} disabled={busy} onChange={event => setStart(event.target.value)} /></label><label>Ozon: по дату<input type="date" value={end} disabled={busy} onChange={event => setEnd(event.target.value)} /></label><button type="button" className="secondary-button" disabled={busy} onClick={() => void refresh()}>Обновить сохранённый отчёт</button><button type="button" className="primary-button" disabled={busy || !canWrite || data?.configured === false} onClick={() => void sync()}>{busy ? "Загружаем…" : "Загрузить данные Ozon"}</button></div>
    {error ? <p role="alert" className="operation-error">{error} Сохранённые данные не заменены частично.</p> : null}
    {data?.configured === false ? <p>Подключите Ozon в разделе «Настройки».</p> : null}
    {!canWrite ? <p>Загрузка и изменение себестоимости доступны владельцу и менеджеру.</p> : null}
    {data?.loaded ? <>
      <p role="status">Загрузка API: {data.source_collection_complete === true ? "все страницы загружены" : "полнота не подтверждена"}. Денежная расшифровка API: {data.financial_normalization_verified === true ? "подтверждена" : "не подтверждена"}. Расчёт: {data.accounting_complete ? "по загруженным данным" : "предварительный"}. Последняя загрузка: {data.synced_at ? new Date(data.synced_at).toLocaleString("ru-RU") : "—"}.</p>
      <div className="ozon-metrics"><div>Итог начислений Ozon<strong>{money(data.net_accrual_rub)}</strong></div><div>Оценочная себестоимость продаж и возвратов<strong>{money(data.estimated_cogs_rub)}</strong></div><div>Покрытие себестоимости<strong>{data.known_cost_units ?? 0} / {data.sales_and_return_units ?? 0} ед.</strong></div></div>
      <p>Прибыль: {money(data.profit_rub)}. Точный FIFO Ozon требует подтверждённых списаний партий для этого канала. Оценочная ставка не заменяет исторический FIFO.</p>
      {data.blockers.length ? <ul aria-label="Неполные данные Ozon">{data.blockers.map(reason => <li key={reason}>{reason}</li>)}</ul> : null}
      <p>{Object.entries(data.source_counts ?? {}).map(([key,value]) => `${({products:"Товары",stocks:"Остатки",fbo:"Отправления FBO",fbs:"Отправления FBS",returns:"Реестр возвратов",accruals:"Начисления"} as Record<string,string>)[key] ?? key}: ${value}`).join(" · ")}</p>
      <p>{data.returns_scope}</p>
      <h3>Себестоимость товаров Ozon</h3><p>Укажите свою ставку или артикул WB, откуда взять текущую оценку. Сопоставление никогда не выполняется автоматически.</p>
      <div className="ozon-table-wrap"><table><thead><tr><th>Товар Ozon</th><th>Остатки</th><th>Источник себестоимости</th><th>Ставка / артикул WB</th><th>Действие</th></tr></thead><tbody>{data.products.slice(0,100).map(product => {
        const draft = drafts[product.sku] ?? { mode: product.cost_mapping?.mode ?? "manual", value: product.cost_mapping?.unit_cost_text ?? String(product.cost_mapping?.nm_id ?? "") };
        const patch = (value: Partial<typeof draft>) => setDrafts(current => ({...current,[product.sku]: {...draft,...value}}));
        return <tr key={product.product_id}><td>{product.offer_id}<small>SKU {product.sku || "не указан"}</small></td><td>{product.stocks.length ? product.stocks.map((stock,index) => <small key={index}>{stock.type ?? "склад"}: {stock.present ?? "—"} шт., резерв {stock.reserved ?? "—"}</small>) : "Нет строк остатков"}</td><td><select aria-label={`Источник стоимости Ozon ${product.sku}`} disabled={!canWrite || busy} value={draft.mode} onChange={event => patch({mode:event.target.value,value:""})}><option value="manual">Моя ставка за единицу, RUB</option><option value="wb_estimate">Текущая оценка товара WB</option></select></td><td><input aria-label={`Стоимость Ozon ${product.sku}`} disabled={!canWrite || busy} value={draft.value} onChange={event => patch({value:event.target.value})} inputMode="decimal" /></td><td><button type="button" className="secondary-button" disabled={!canWrite || busy || !drafts[product.sku] || !product.sku} onClick={() => void save(product)}>Сохранить стоимость</button></td></tr>;
      })}</tbody></table></div>{data.products.length > 100 ? <p>Показаны первые 100 товаров из {data.products.length}. Все товары учтены в загрузке.</p> : null}
      {data.fee_preview ? <section aria-labelledby="ozon-fee-preview-title"><h3 id="ozon-fee-preview-title">Предварительная сверка сборов Ozon</h3>
        <p>Сумма сборов, совпавших с итогом исходных строк: {previewMoney(data.fee_preview.reconciled_total_rub)}. Это часть начислений, уже включённая в итог Ozon. Прибыль и полнота источника не подтверждены; запись в документальный учёт недоступна.</p>
        <p>Сверено строк: {data.fee_preview.rows.filter(row => row.reconciled).length} из {data.fee_preview.rows.length}. Продажи и возвраты требуют отдельной проверки.</p>
        <div className="ozon-table-wrap"><table><thead><tr><th>Исходная строка</th><th>Дата / категория</th><th>Результат сверки</th><th>Компоненты</th></tr></thead><tbody>{data.fee_preview.rows.slice(0, 100).map(row => <tr key={row.source_occurrence_key}><td>{row.accrual_id ?? "—"}<small>Вхождение {data.fee_preview!.rows.indexOf(row) + 1}</small></td><td>{row.date ?? "—"}<small>{row.category ?? "—"}</small></td><td>{row.reconciled ? previewMoney(row.component_sum_rub) : row.reason ?? "Не сверено"}</td><td>{row.components.map(component => <small key={component.source_path}>{component.name}{component.sku ? ` · SKU ${component.sku}` : ""}: {previewMoney(component.amount_rub)}</small>)}</td></tr>)}</tbody></table></div>
        {data.fee_preview.rows.length > 100 ? <p>Показаны первые 100 строк из {data.fee_preview.rows.length}; сумма вычислена по всем сверенным строкам.</p> : null}
      </section> : null}
      <section aria-labelledby="ozon-posting-evidence-title"><h3 id="ozon-posting-evidence-title">Исходные финансовые строки отправлений Ozon</h3>
        <p>Количество из источника не подтверждает движение продажи или возврата. Суммы показаны со знаком источника; формула итогов отправлений и связь возвратов не подтверждены.</p>
        <p>Запрашиваются отправления из загруженного списка FBO/FBS за выбранный период. Финансовый метод возвращает историю этих отправлений без фильтра по дате начисления.</p>
        {data.posting_accrual_evidence?.loaded ? <>
          <p>Запрошено отправлений: {data.posting_accrual_evidence.scope?.requested_number_count ?? "не указано"}. Отправлений без ответа: {data.posting_accrual_evidence.scope?.missing_number_count ?? "не указано"}. Исходных строк: {data.posting_accrual_evidence.source_row_count}. Это не подтверждение полноты финансового источника.</p>
          <div className="ozon-table-wrap"><table><thead><tr><th>Отправление / дата начисления</th><th>SKU / тип начисления</th><th>Количество источника</th><th>Начислено за услугу</th><th>Цена за единицу</th><th>Замечание</th></tr></thead><tbody>{data.posting_accrual_evidence.rows.map(row => <tr key={row.source_occurrence_key}><td>{row.posting_number ?? "не указано / не распознано"}<small>{row.accrual_date ?? "не указано / не распознано"}</small></td><td>{row.sku_text ?? "не указано / не распознано"}<small>Тип: {row.type_id_text ?? "не указано / не распознано"}</small></td><td>{row.quantity_text ?? "не указано / не распознано"}</td><td>{row.accrued_rub === null ? "не указано / не распознано" : previewMoney(row.accrued_rub)}</td><td>{row.seller_price_rub === null ? "не указано / не распознано" : previewMoney(row.seller_price_rub)}</td><td>{row.reason ?? "—"}</td></tr>)}</tbody></table></div>
          {data.posting_accrual_evidence.display_truncated ? <p>Показаны первые 100 строк из {data.posting_accrual_evidence.source_row_count}. Исходный ответ сохранён целиком.</p> : null}
        </> : <p>Этот сохранённый отчёт не содержит финансовых строк отправлений. Они появятся после новой загрузки данных Ozon.</p>}
      </section>
      {data.fees?.length ? <><h3>Сборы по справочнику Ozon</h3><p>Расшифровка уже входит в итог начислений: эти суммы не вычитаются повторно.</p><ul>{data.fees.map(fee => <li key={fee.type_id}>{fee.name}: {money(fee.amount_rub)}{fee.mapped ? "" : " · тип не распознан"}</li>)}</ul></> : null}
    </> : data ? <p>{data.blockers.join(" ")}</p> : <p>Читаем сохранённый отчёт…</p>}
    <OzonHistoricalPnl key={`${start}:${end}`} start={start} end={end} canWrite={canWrite} />
  </section>;
}
