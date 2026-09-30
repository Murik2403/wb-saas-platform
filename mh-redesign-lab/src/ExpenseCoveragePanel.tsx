import { useRef, useState, type FormEvent } from "react";
import { getExpenseCoverage, saveExpenseCoverage, type ExpenseCoverage, type ProductCostSetting } from "./data-source";

const articles: Record<string, string> = { purchase_production: "Закупка / производство", wb_commission: "Комиссия WB", logistics_returns: "Логистика и возвраты", storage: "Хранение", acceptance: "Приёмка", advertising: "Реклама", packaging_labor: "Упаковка и труд", defects_losses: "Брак и потери", taxes: "Налоги" };
const states: Record<string, string> = { confirmed: "Подтверждено по документам", confirmed_zero: "Подтверждённый ноль", not_applicable: "Не применяется", estimated: "Оценка", unknown: "Данных недостаточно" };

export function ExpenseCoveragePanel({ products }: { products: ProductCostSetting[] }) {
  const [nmId, setNmId] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [coverage, setCoverage] = useState<ExpenseCoverage | null>(null);
  const [article, setArticle] = useState("taxes");
  const [state, setState] = useState("unknown");
  const [source, setSource] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const pending = useRef<{ snapshot: string; key: string } | null>(null);
  const active = coverage && String(coverage.nm_id) === nmId && coverage.start === start && coverage.end === end;

  async function load(event: FormEvent) {
    event.preventDefault(); setBusy(true); setMessage(""); setCoverage(null);
    try { setCoverage(await getExpenseCoverage(Number(nmId), start, end)); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось загрузить покрытие."); }
    finally { setBusy(false); }
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!active || !coverage.can_write) return;
    const payload = { nm_id: coverage.nm_id, start: coverage.start, end: coverage.end, article, state, source_ref: source, reason, expected_fingerprint: coverage.items.find(item => item.article === article)!.fingerprint };
    const snapshot = JSON.stringify(payload);
    if (!pending.current || pending.current.snapshot !== snapshot) pending.current = { snapshot, key: crypto.randomUUID() };
    setBusy(true); setMessage("");
    try {
      await saveExpenseCoverage({ ...payload, request_key: pending.current.key });
      pending.current = null;
      setCoverage(await getExpenseCoverage(coverage.nm_id, coverage.start, coverage.end));
      setMessage("Подтверждение сохранено. Суммы расходов не изменились.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось сохранить подтверждение."); }
    finally { setBusy(false); }
  }

  return <div className="operation-actions">
    <h3>Проверка полноты расходов по товару</h3>
    <p>Подтвердите каждую статью за выбранный период по своим документам. Старые записи, оценки и исключённые расходы сами по себе не подтверждают полноту. Эти отметки не изменяют деньги в отчёте.</p>
    <form onSubmit={load} className="operation-form">
      <label>Товар для проверки<select required value={nmId} onChange={e => setNmId(e.target.value)}><option value="">Выберите товар</option>{products.map(p => <option key={p.nm_id} value={p.nm_id}>{p.product_name || p.supplier_article} · WB {p.nm_id}</option>)}</select></label>
      <label>Начало проверки<input type="date" required value={start} max={end || undefined} onChange={e => setStart(e.target.value)} /></label>
      <label>Конец проверки<input type="date" required value={end} min={start || undefined} onChange={e => setEnd(e.target.value)} /></label>
      <button type="submit" disabled={busy}>Показать покрытие расходов</button>
    </form>
    {message && <p role="status">{message}</p>}
    {active && <>
      <p>{coverage.count} из {coverage.total} статей подтверждено пользователем. {coverage.preliminary ? "Расходы проверены не полностью." : "Полнота FIFO и исходного отчёта проверяется отдельно."}</p>
      <ul>{coverage.items.map(item => <li key={item.article}><strong>{articles[item.article]}</strong>: {states[item.state]}{item.stale ? " — исходные данные изменились; повторите проверку" : ""}{item.source_ref && <> · {item.source_ref}</>}{item.observed_source && <> · В источнике есть ненулевые расходы; ноль и неприменимость недоступны. Это не подтверждение полноты данных.</>}</li>)}</ul>
      {coverage.can_write ? <form onSubmit={save} className="operation-form">
        <label>Статья подтверждения<select value={article} onChange={e => setArticle(e.target.value)}>{Object.entries(articles).map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <label>Состояние расходов<select value={state} onChange={e => setState(e.target.value)}>{Object.entries(states).map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <label>Источник или документ<input required maxLength={200} value={source} onChange={e => setSource(e.target.value)} /></label>
        <label>Причина подтверждения<textarea required maxLength={500} value={reason} onChange={e => setReason(e.target.value)} /></label>
        <button type="submit" disabled={busy}>Сохранить состояние расходов</button>
      </form> : <p>Изменять подтверждения может владелец или менеджер.</p>}
      <details><summary>История подтверждений ({coverage.history.length})</summary><ul>{coverage.history.map(row => <li key={row.id}>{row.created_at} · {articles[row.article]} · {states[row.state]} · {row.source_ref} · {row.reason} · {row.actor}</li>)}</ul></details>
    </>}
  </div>;
}
