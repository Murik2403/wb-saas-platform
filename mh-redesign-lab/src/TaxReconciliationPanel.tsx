import { useEffect, useState } from "react";
import { API_BASE_URL, apiHeaders } from "./data-source";
import { scopedFetch } from "./selected-shop";
import { writeError } from "./write-error";
import "./TaxReconciliationPanel.css";

const fields = [
  ["received", "Поступления от маркетплейса", "Полученные за месяц деньги по банковским документам."],
  ["offset", "Доход, удержанный взаимозачётом", "Часть дохода, удержанная маркетплейсом. Не добавляйте повторно сумму, уже включённую в поступления."],
  ["compensation", "Компенсации, не включённые выше", "Только доходы по документам, ещё не включённые в первые две строки."],
  ["other_income", "Прочие доходы, не включённые выше", "В пределах выбранного магазина. Один доход учитывайте один раз."],
  ["refunds", "Возвращённый покупателям доход", "Возвраты дохода по документам этого месяца, ещё не вычтенные из строк выше."],
  ["profit_before_tax", "Результат до налога", "Необязательно: результат за тот же месяц без налога. Укажите из своего отчёта, не из банковской выплаты."],
  ["fns_assessed", "Начислено ФНС за месяц", "Необязательно: фактическое начисление из кабинета АУСН."],
  ["paid", "Оплачено в счёт этого начисления", "Необязательно: не весь платёж на ЕНС, а сумма, отнесённая к налогу этого месяца."],
] as const;
type Key = typeof fields[number][0];
type Values = Record<Key, number | null> & { month: string; regime: string; source: string; confirmed: boolean };
type Result = { revision: number; can_write: boolean; values: Values; calculation: { status: string; missing: string[]; base: number | null; estimate: number | null; after_estimate: number | null; fns_difference: number | null; after_fns: number | null; assessed_minus_paid: number | null; negative_base: boolean }; history: { revision: number; created_at: string; values: Values }[] };
const rub = (value: number | null) => value === null ? "Не указано" : `${value.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽`;
const empty = () => Object.fromEntries(fields.map(([key]) => [key, ""])) as Record<Key, string>;

export function buildTaxInput(cells: Record<Key, string>) {
  const values = {} as Record<Key, number | null>;
  for (const [key, label] of fields) {
    const text = cells[key].trim().replace(",", ".");
    if (!text) { values[key] = null; continue; }
    if (!/^-?\d+(\.\d{1,2})?$/.test(text)) throw new Error(`${label}: введите сумму с точностью до копейки.`);
    const number = Number(text);
    if (!Number.isFinite(number) || Math.abs(number) > 1e12 || (key !== "profit_before_tax" && number < 0)) throw new Error(`${label}: недопустимая сумма.`);
    values[key] = number;
  }
  return values;
}

export function taxReconciliationCsv(data: Result) {
  const rows = [["Месяц", data.values.month], ["Режим", "АУСН Доходы 8%"], ["Статус", data.calculation.status],
    ...fields.map(([key, label]) => [label, data.values[key] === null ? "Не указано" : String(data.values[key]).replace(".", ",")]),
    ["База", rub(data.calculation.base)], ["Оценка налога", rub(data.calculation.estimate)], ["После оценки", rub(data.calculation.after_estimate)],
    ["ФНС минус оценка", rub(data.calculation.fns_difference)], ["После начисления ФНС", rub(data.calculation.after_fns)], ["Начислено минус оплачено", rub(data.calculation.assessed_minus_paid)], ["Основание", data.values.source]];
  // Formula-looking free text must remain text in spreadsheet applications.
  return "\uFEFF" + rows.map(row => row.map(cell => `"${String(cell).replace(/^[=+@-]/, "'$&").replaceAll('"', '""')}"`).join(";")).join("\r\n");
}

export function TaxReconciliationPanel() {
  const [month, setMonth] = useState(() => { const now = new Date(); return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`; });
  const [data, setData] = useState<Result | null>(null);
  const [cells, setCells] = useState(empty);
  const [source, setSource] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [refresh, setRefresh] = useState(0);
  const fill = (next: Result) => { setData(next); setCells(Object.fromEntries(fields.map(([key]) => [key, next.values[key] === null ? "" : String(next.values[key])])) as Record<Key, string>); setSource(next.values.source); setConfirmed(next.values.confirmed); };
  useEffect(() => {
    const controller = new AbortController(); setData(null); setError(""); setMessage(""); setCells(empty()); setSource(""); setConfirmed(false);
    scopedFetch(`${API_BASE_URL}/api/v1/settings/tax-reconciliation?month=${month}`, { headers: apiHeaders(), signal: controller.signal })
      .then(async response => { if (!response.ok) throw await writeError(response); return response.json() as Promise<Result>; })
      .then(next => { if (!controller.signal.aborted) fill(next); })
      .catch(reason => { if (!controller.signal.aborted) setError(String(reason.message ?? reason)); });
    return () => controller.abort();
  }, [month, refresh]);
  async function save() {
    if (!data) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const input = buildTaxInput(cells);
      const response = await scopedFetch(`${API_BASE_URL}/api/v1/settings/tax-reconciliation`, { method: "PUT", headers: apiHeaders(true), body: JSON.stringify({ ...input, month, regime: "ausn_income", source, confirmed, revision: data.revision }) });
      if (!response.ok) throw await writeError(response);
      fill(await response.json() as Result); setMessage("Сверка сохранена. Предыдущие версии остались в истории.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось сохранить"); }
    finally { setBusy(false); }
  }
  const calculation = data?.calculation;
  return <section className="work-section tax-reconciliation" id="tax-reconciliation">
    <details>
      <summary>Налоги · АУСН «Доходы» 8% — расчёт и сверка</summary>
      <p>Сверка по календарному месяцу выбранного магазина. Для нескольких магазинов одного налогоплательщика сведите доходы вместе: этот блок не рассчитывает общий налог организации.</p>
      <p>Доходы определяются по поступлениям и взаимозачётам, а не по дате продажи WB. Оценка по введённым данным; фактический налог начисляет ФНС. Пустое поле означает отсутствие данных, ноль укажите явно.</p>
      <label>Месяц сверки<input type="month" min="2000-01" max="2099-12" value={month} disabled={busy} onChange={event => { if (event.target.value) setMonth(event.target.value); }} /></label>
      {!data && !error ? <p role="status">Загружаем сверку…</p> : null}
      {error ? <p role="alert">{error} <button type="button" className="ghost-button" disabled={busy} onClick={() => setRefresh(value => value + 1)}>Загрузить актуальную версию</button></p> : null}
      {data ? <>
        <form onSubmit={event => { event.preventDefault(); void save(); }}>
          <div className="tax-reconciliation-grid">{fields.map(([key, label, hint]) => <label key={key}>{label}<input inputMode="decimal" value={cells[key]} disabled={busy || !data.can_write} placeholder="Не указано" onChange={event => { setCells(previous => ({ ...previous, [key]: event.target.value })); setMessage(""); }} /><small>{hint}</small></label>)}</div>
          <label>Основание / документы<input maxLength={500} value={source} required disabled={busy || !data.can_write} onChange={event => { setSource(event.target.value); setMessage(""); }} /></label>
          <label className="tax-reconciliation-confirm"><input type="checkbox" checked={confirmed} required disabled={busy || !data.can_write} onChange={event => setConfirmed(event.target.checked)} />Проверил даты, состав доходов и возвратов; одни суммы не включены дважды.</label>
          {data.can_write ? <button className="primary-button" disabled={busy} type="submit">{busy ? "Сохраняем…" : "Сохранить и рассчитать"}</button> : <p>У вас доступ для просмотра. Сохранение доступно владельцу и менеджеру.</p>}
        </form>
        {message ? <p role="status">{message}</p> : null}
        <p>Ниже — последняя сохранённая версия {data.revision || "(данных нет)"}; изменения полей вступают в расчёт после сохранения.</p>
        {calculation?.missing.length ? <p className="warning-text">Оценка не рассчитана. Заполните: {fields.filter(([key]) => calculation.missing.includes(key)).map(([, label]) => label).join(", ")}.</p> : null}
        {calculation?.negative_base ? <p className="warning-text">Возвраты превышают указанный доход. Проверьте документы и сверяйте итог с ФНС; оценка этого месяца ограничена нулём.</p> : null}
        <dl className="tax-reconciliation-grid">{([
          ["Налоговая база", calculation?.base], ["Оценка АУСН · 8%", calculation?.estimate], ["До налога", data.values.profit_before_tax], ["После оценки налога", calculation?.after_estimate],
          ["Начислено ФНС", data.values.fns_assessed], ["ФНС − оценка", calculation?.fns_difference], ["После начисления ФНС", calculation?.after_fns], ["Оплачено", data.values.paid], ["Начислено − оплачено", calculation?.assessed_minus_paid],
        ] as [string, number | null | undefined][]).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{rub(value ?? null)}</dd></div>)}</dl>
        <p>Оплата не вычитается из прибыли повторно. Это сверка, она не добавляет налог в P&L или денежный журнал автоматически. Если налог уже входит в расходы, не вычитайте его ещё раз.</p>
        <button className="ghost-button" type="button" onClick={() => { const url = URL.createObjectURL(new Blob([taxReconciliationCsv(data)], { type: "text/csv;charset=utf-8" })); const link = document.createElement("a"); link.href = url; link.download = `ausn-${month}.csv`; link.click(); URL.revokeObjectURL(url); }}>Скачать сверку CSV</button>
        <details><summary>История изменений ({data.history.length}, последние 50)</summary>{data.history.map(item => <div key={item.revision}><strong>Версия {item.revision} · {new Date(item.created_at).toLocaleString("ru-RU")}</strong><p>{item.values.source}</p><dl>{fields.map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{rub(item.values[key])}</dd></div>)}</dl></div>)}</details>
        <a href="https://ausn.nalog.gov.ru/" target="_blank" rel="noreferrer">Как ФНС учитывает доходы и взаимозачёты АУСН</a>
      </> : null}
    </details>
  </section>;
}
