import { useCallback, useEffect, useId, useRef, useState } from "react";
import { API_BASE_URL, apiHeaders } from "./data-source";
import { scopedFetch } from "./selected-shop";
import { previewMoney } from "./ozon-preview-money";
import "./OzonNormalizationPanel.css";

type Component = { source_path: string; amount_rub: string | null; sku?: string | null; reference_only?: boolean; reason?: string };
type SourceRow = {
  source_occurrence_key: string; source_row_index: number; accrual_date?: string | null; category?: string | null;
  eligible: boolean; observed_total_rub?: string | null; normalized_total_rub: string | null;
  quantity: number | null; known_cogs_rub: string | null; document_event_ids: string[]; blockers: string[];
  observed_components?: Component[]; components?: Array<{ name: string; amount_rub: string; sku: string | null }>;
};
type Preview = {
  loaded: boolean; fingerprint?: string | null; source_fingerprint: string | null; rows: SourceRow[];
  observed_total_rub?: string | null; normalized_total_rub: string | null; result_after_fifo_rub: string | null;
  normalized_row_count?: number; excluded_row_count?: number; all_rows_normalized?: boolean;
  known_cost_units?: number; unknown_cost_units?: number; source_collection_complete?: boolean;
};
type Document = { external_event_id: string; sku: string; kind: string; accrued_at: string; source_total_rub: string; posting_ref?: string; namespace: string };
type Documents = { lines: Document[] };
class HttpError extends Error { constructor(message: string, public status: number) { super(message); } }
const categories: Record<string, string> = { POSTING: "Товарное начисление", ITEM: "Сборы по товарам", NON_ITEM: "Услуги", CONTAINER_FEES: "Грузоместа" };
const category = (row: SourceRow) => categories[row.category ?? ""] ?? "Неизвестная категория";
const dateLabel = (value?: string | null) => {
  if (!value) return "дата не указана";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleDateString("ru-RU", { timeZone: "Europe/Moscow" });
};
async function request<T>(path: string, signal: AbortSignal, body?: unknown): Promise<T> {
  const response = await scopedFetch(`${API_BASE_URL}/api/v1/ozon-accounting${path}`, {
    method: body === undefined ? "GET" : "POST", headers: apiHeaders(body !== undefined), signal,
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    const failure = await response.json().catch(() => ({}));
    throw new HttpError(typeof failure.detail === "string" ? failure.detail : failure.detail?.message ?? `Ozon: HTTP ${response.status}`, response.status);
  }
  return response.json() as Promise<T>;
}

export function OzonNormalizationPanel({ start, end, canWrite }: { start: string; end: string; canWrite: boolean }) {
  const titleId = useId();
  const [namespace, setNamespace] = useState("main");
  const [draftNamespace, setDraftNamespace] = useState("main");
  const [data, setData] = useState<Preview | null>(null);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [occurrence, setOccurrence] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const [visibleCount, setVisibleCount] = useState(50);
  const [documentSearch, setDocumentSearch] = useState("");
  const generation = useRef(0);
  const readAbort = useRef<AbortController | null>(null);
  const writeAbort = useRef<AbortController | null>(null);
  const selectedRow = data?.rows.find(row => row.source_occurrence_key === occurrence);
  const visibleRows = data?.rows.slice(0, visibleCount) ?? [];
  const matchingDocuments = documents.filter(doc => `${doc.external_event_id} ${doc.sku} ${doc.posting_ref ?? ""}`.toLowerCase().includes(documentSearch.trim().toLowerCase()));

  const load = useCallback(async (preserveError = false) => {
    const current = ++generation.current;
    readAbort.current?.abort();
    const controller = new AbortController(); readAbort.current = controller;
    setBusy(true); setData(null); setDocuments([]); setOccurrence(""); setSelected([]); setConfirmed(false);
    setVisibleCount(50); setDocumentSearch("");
    if (!preserveError) setError(null);
    const query = new URLSearchParams({ start_date: start, end_date: end, namespace });
    try {
      const [preview, journal] = await Promise.all([
        request<Preview>(`/normalization/preview?${query}`, controller.signal),
        request<Documents>(`/normalization/documents?${query}`, controller.signal),
      ]);
      if (current !== generation.current || controller.signal.aborted) return;
      setData(preview); setDocuments(journal.lines ?? []);
    } catch (caught) {
      if (current === generation.current && !controller.signal.aborted) setError(caught instanceof Error ? caught.message : "Не удалось загрузить сверку Ozon.");
    } finally { if (current === generation.current) setBusy(false); }
  }, [start, end, namespace]);

  useEffect(() => {
    setNotice(null); void load();
    return () => { ++generation.current; readAbort.current?.abort(); writeAbort.current?.abort(); };
  }, [load]);

  const save = async () => {
    if (!canWrite || busy || !data?.fingerprint || !selectedRow || !selected.length || !confirmed) return;
    const current = generation.current;
    const controller = new AbortController(); writeAbort.current = controller;
    setBusy(true); setError(null); setNotice(null);
    try {
      await request("/normalization/link", controller.signal, {
        start_date: start, end_date: end, namespace, expected_fingerprint: data.fingerprint,
        source_occurrence_key: selectedRow.source_occurrence_key, document_event_ids: selected,
      });
      if (current !== generation.current || controller.signal.aborted) return;
      setNotice("Связь сохранена. Сверка обновлена по текущим документам.");
      await load();
    } catch (caught) {
      if (current !== generation.current || controller.signal.aborted) return;
      setError(caught instanceof Error ? caught.message : "Связь не сохранена.");
      if (caught instanceof HttpError && caught.status === 409) {
        setNotice("Данные изменились. Проверьте обновлённый источник и выберите документы повторно.");
        await load(true);
      }
    } finally { if (current === generation.current) setBusy(false); }
  };

  return <section className="ozon-normalization" aria-labelledby={titleId} aria-busy={busy}>
    <div className="ozon-normalization-heading"><div><p className="ozon-normalization-eyebrow">Источник → документы → FIFO</p><h3 id={titleId}>Сверка начислений Ozon</h3></div>
      <button className="secondary-button" type="button" disabled={busy} onClick={() => { setNotice(null); void load(); }}>Обновить сверку</button></div>
    <p>Сборы сверяются с итогом источника. Продажи и возвраты можно связать с уже внесёнными документами — повторно вводить суммы не нужно. Даты движения и партии берём из документального журнала.</p>
    <form className="ozon-normalization-scope" onSubmit={event => { event.preventDefault(); const value = draftNamespace.trim(); if (value && value !== namespace) setNamespace(value); }}>
      <label>Реестр документов Ozon<input value={draftNamespace} maxLength={80} disabled={busy} onChange={event => setDraftNamespace(event.target.value)} /></label>
      <button className="secondary-button" disabled={busy || !draftNamespace.trim() || draftNamespace.trim() === namespace}>Применить реестр</button>
    </form>
    {error && <p role="alert" className="ozon-normalization-error">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {busy && <p role="status">Загружаем сверку…</p>}
    {data && !data.loaded && <p>За выбранный период источник ещё не загружен. Сначала загрузите данные Ozon. Отсутствие данных не означает нулевую прибыль.</p>}
    {data?.loaded && <>
      <div className="ozon-normalization-metrics">
        <div><span>Итог строк источника</span><strong>{previewMoney(data.observed_total_rub)}</strong></div>
        <div><span>Сверено по компонентам и документам</span><strong>{previewMoney(data.normalized_total_rub)}</strong></div>
        <div><span>Результат после подтверждённого FIFO</span><strong>{previewMoney(data.result_after_fifo_rub)}</strong></div>
      </div>
      <p>Сверено строк: {data.normalized_row_count ?? 0} из {data.rows.length}. Исключено: {data.excluded_row_count ?? data.rows.length}. Неизвестная себестоимость: {data.unknown_cost_units ?? 0} ед.</p>
      {data.rows.some(row => !row.eligible && row.quantity === null) && <p>У исключённых товарных строк количество и себестоимость ещё не подтверждены. Они не входят в число известных единиц.</p>}
      <p>{data.source_collection_complete ? "Страницы источника прочитаны." : "Полнота загрузки источника не подтверждена."} Реальная денежная сверка с Ozon ещё не выполнена. Частичная сумма включает только сверенные строки; результат — до налогов и внешних расходов.</p>
      {data.rows.length === 0 ? <p>Источник не вернул начислений. Это не подтверждение отсутствия продаж.</p> : <div className="ozon-normalization-table" tabIndex={0} aria-label="Начисления Ozon: прокручиваемая таблица"><table><thead><tr><th>Строка / дата начисления</th><th>Категория</th><th>Итог источника</th><th>Сверка</th><th>Историческая стоимость</th></tr></thead><tbody>
        {visibleRows.map(row => <tr key={row.source_occurrence_key}><td>{row.source_row_index + 1} · {dateLabel(row.accrual_date)}</td><td>{category(row)}</td><td>{previewMoney(row.observed_total_rub)}</td><td>{row.eligible ? (row.document_event_ids.length ? `Документы: ${row.document_event_ids.join(", ")}` : "Компоненты сверены") : row.blockers.join(" ") || "Требуется документальная сверка"}</td><td>{previewMoney(row.known_cogs_rub)}</td></tr>)}
      </tbody></table></div>}
      {visibleRows.length < data.rows.length && <><p>Показано {visibleRows.length} из {data.rows.length} начислений. Итоги рассчитаны по всем строкам.</p><button className="secondary-button" type="button" onClick={() => setVisibleCount(value => value + 50)}>Показать ещё начисления</button></>}
      {canWrite && data.rows.length > 0 && <div className="ozon-normalization-link">
        <h4>Связать начисление с документами</h4><p>Выберите одну строку и документальные операции этого реестра. Сервер проверит дату начисления, SKU, итог, повторное использование и актуальность источника. Связь не меняет физические остатки и не отправляет операции в Ozon.</p>
        <label>Начисление Ozon<select disabled={busy} value={occurrence} onChange={event => { setOccurrence(event.target.value); setSelected([]); setConfirmed(false); }}><option value="">Выберите строку</option>{visibleRows.map(row => <option key={row.source_occurrence_key} value={row.source_occurrence_key}>{row.source_row_index + 1} · {dateLabel(row.accrual_date)} · {category(row)} · {previewMoney(row.observed_total_rub)}</option>)}</select></label>
        {selectedRow && <>
          <label>Найти документ по ID или SKU<input value={documentSearch} onChange={event => setDocumentSearch(event.target.value)} disabled={busy} /></label>
          <fieldset disabled={busy}><legend>Документальные операции</legend>{documents.length ? matchingDocuments.slice(0, 100).map(doc => <label className="ozon-normalization-document" key={`${doc.namespace}:${doc.external_event_id}`}><input type="checkbox" checked={selected.includes(doc.external_event_id)} onChange={event => { setSelected(previous => event.target.checked ? [...previous, doc.external_event_id] : previous.filter(id => id !== doc.external_event_id)); setConfirmed(false); }} /><span>{doc.external_event_id} · {doc.sku ? `SKU ${doc.sku}` : "сбор"} · {dateLabel(doc.accrued_at)} · {previewMoney(doc.source_total_rub)}<small>Начисление: {doc.accrued_at} · отправление: {doc.posting_ref || "не указано"}</small></span></label>) : <p>В этом периоде и реестре нет документальных операций. Внесите документы в журнал Ozon, затем обновите сверку.</p>}{documents.length > 0 && matchingDocuments.length === 0 && <p>Документы по этому запросу не найдены.</p>}{matchingDocuments.length > 100 && <p>Показаны первые 100 документов. Уточните поиск по ID, SKU или отправлению.</p>}</fieldset>
          <p>Выбрано документов: {selected.length}{selected.length ? ` · ${selected.join(", ")}` : ""}.</p>
          <label className="ozon-normalization-document"><input type="checkbox" disabled={busy || !selected.length} checked={confirmed} onChange={event => setConfirmed(event.target.checked)} /><span>Я проверил начисление и выбранные документы</span></label>
          <button className="secondary-button" type="button" disabled={busy || !selected.length || !confirmed} onClick={() => void save()}>Подтвердить связь документов</button>
        </>}
      </div>}
      {!canWrite && <p>Связь документов доступна владельцу и менеджеру. Просмотр сверки доступен без изменения данных.</p>}
      <details className="ozon-normalization-details"><summary>Сведения об источнике</summary><p>Версия источника: <code>{data.source_fingerprint?.slice(0, 12) || "не определена"}</code>. Коррекции требуют повторной сверки; версии не складываются друг с другом. Возврат требует исходной продажи и подтверждённой исторической стоимости.</p>
        {visibleRows.flatMap(row => (row.observed_components ?? []).map(component => <p key={`${row.source_occurrence_key}:${component.source_path}`}><code>{component.source_path}</code>{component.sku ? ` · SKU ${component.sku}` : ""}: {previewMoney(component.amount_rub)}{component.reference_only ? " · справочное поле, не прибавляется к итогам" : " · поле источника, формула не предполагается"}{component.reason ? ` · ${component.reason}` : ""}</p>))}
      </details>
    </>}
  </section>;
}
