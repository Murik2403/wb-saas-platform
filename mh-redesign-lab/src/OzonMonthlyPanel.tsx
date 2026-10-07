import { useEffect, useState } from "react";
import { API_BASE_URL, apiHeaders } from "./data-source";
import { scopedFetch } from "./selected-shop";
import "./OzonAccounting.css";
import { previewMoney } from "./ozon-preview-money";

type Month = { month: string; document_lines: number; revenue_rub: string | null; net_document_total_rub: string | null; historical_cogs_rub: string | null; result_after_fifo_rub: string | null; unknown_cost_units: number | null };
type Report = { items: Month[]; blockers: string[]; note: string };
type Estimate = { estimate: { status: string; missing: string[]; estimate: string | null; income_tax: string | null; vat_to_pay: string | null } | null; note: string };
const money = (value: string | number | null) => value === null ? "Нет данных" : previewMoney(String(value));
const regimes = [["ausn_income", "АУСН — доходы"], ["ausn_income_expense", "АУСН — доходы минус расходы"], ["usn_income", "УСН — доходы"], ["usn_income_expense", "УСН — доходы минус расходы"], ["osno_ip", "ОСНО — ИП"], ["osno_company", "ОСНО — организация"]];
const fields = [["received", "Получено денег"], ["offset", "Взаимозачёты"], ["compensation", "Компенсации"], ["other_income", "Другие доходы"], ["refunds", "Возвращено покупателям"], ["expenses", "Подтверждённые расходы"], ["loss", "Переносимые убытки"], ["insurance", "Страховые взносы"], ["vat_output", "Исходящий НДС"], ["vat_input", "Входящий НДС"], ["advances", "Уплаченные авансы"], ["other_ndfl_base", "Другой доход для НДФЛ"]];

export function OzonMonthlyPanel({ start, end, canWrite }: { start: string; end: string; canWrite: boolean }) {
  const [from, setFrom] = useState(start);
  const [to, setTo] = useState(end);
  const [data, setData] = useState<Report | null>(null);
  const [error, setError] = useState("");
  const [regime, setRegime] = useState("ausn_income");
  const [month, setMonth] = useState(end.slice(0, 7));
  const [vat, setVat] = useState("exempt");
  const [rate, setRate] = useState("");
  const [staff, setStaff] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [tax, setTax] = useState<Estimate | null>(null);
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [taxEpoch, setTaxEpoch] = useState(0);
  const query = `?start_date=${from}&end_date=${to}&namespace=main`;
  useEffect(() => {
    const controller = new AbortController(); setData(null); setError("");
    scopedFetch(`${API_BASE_URL}/api/v1/ozon-insights/monthly${query}`, { headers: apiHeaders(), signal: controller.signal })
      .then(async response => { if (!response.ok) throw new Error(`Ozon: HTTP ${response.status}`); return response.json() as Promise<Report>; })
      .then(result => { if (!controller.signal.aborted) setData(result); })
      .catch(caught => { if (!controller.signal.aborted) setError(caught.message); });
    return () => controller.abort();
  }, [query, revision]);
  useEffect(() => {
    const controller = new AbortController(); setTax(null);
    if (!taxEpoch) return () => controller.abort();
    setBusy(true);
    const options = { month, regime, vat_mode: vat, has_staff: staff, ...(rate ? { rate: rate.replace(",", ".") } : {}), ...Object.fromEntries(Object.entries(values).filter(([, value]) => value.trim()).map(([key, value]) => [key, value.replace(",", ".")])) };
    scopedFetch(`${API_BASE_URL}/api/v1/ozon-insights/tax-input`, { method: "POST", headers: apiHeaders(true), signal: controller.signal, body: JSON.stringify({ start_date: from, end_date: to, namespace: "main", options }) })
      .then(async response => { const result = await response.json(); if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : `HTTP ${response.status}`); return result as Estimate; })
      .then(result => { if (!controller.signal.aborted) setTax(result); })
      .catch(caught => { if (!controller.signal.aborted) setError(caught.message); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
    // Changes to inputs reset taxEpoch before a new explicit calculation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taxEpoch]);
  const resetTax = () => { setTaxEpoch(0); setTax(null); setBusy(false); };
  const exportCsv = async () => {
    try {
      const response = await scopedFetch(`${API_BASE_URL}/api/v1/ozon-insights/monthly.csv${query}`, { headers: apiHeaders() });
      if (!response.ok) throw new Error(`CSV: HTTP ${response.status}`);
      const url = URL.createObjectURL(await response.blob()); const a = document.createElement("a"); a.href = url; a.download = "ozon-monthly.csv"; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Не удалось выгрузить CSV"); }
  };
  return <section className="ozon-accounting" aria-label="Помесячный результат Ozon">
    <h3>ОПиУ Ozon по месяцам</h3>
    <p>Расчёт по документальному журналу и историческим партиям. Неполная себестоимость оставляет результат незаполненным.</p>
    <div className="ozon-controls"><label>Начало отчёта<input type="date" value={from} onChange={e => { setFrom(e.target.value); resetTax(); }} /></label><label>Конец отчёта<input type="date" value={to} onChange={e => { setTo(e.target.value); resetTax(); }} /></label><button type="button" className="secondary-button" onClick={() => setRevision(v => v + 1)}>Обновить ОПиУ</button><button type="button" className="secondary-button" onClick={() => void exportCsv()}>Скачать ОПиУ CSV</button></div>
    {error && <p role="alert">{error}</p>}
    {data ? <><div className="ozon-table-wrap" tabIndex={0} role="region" aria-label="Ozon по месяцам"><table><thead><tr><th>Месяц</th><th>Документов</th><th>Выручка</th><th>Начисления после удержаний</th><th>FIFO</th><th>Результат до налогов</th><th>Без стоимости, ед.</th></tr></thead><tbody>{data.items.map(row => <tr key={row.month}><td>{row.month}</td><td>{row.document_lines}</td><td>{money(row.revenue_rub)}</td><td>{money(row.net_document_total_rub)}</td><td>{money(row.historical_cogs_rub)}</td><td>{money(row.result_after_fifo_rub)}</td><td>{row.unknown_cost_units ?? "Нет данных"}</td></tr>)}</tbody></table></div><p>{data.note}</p>{data.blockers.map(reason => <p key={reason}>{reason}</p>)}</> : <p>Загрузка ОПиУ…</p>}
    <details><summary>Оценка налогов Ozon</summary>
      <p>Введите подтверждённые налоговые суммы отдельно. Начисления и документальная выручка не подставляются как полученный доход. Пустое поле означает отсутствие данных; подтверждённый ноль введите явно.</p>
      <div className="ozon-controls"><label>Налоговый месяц<input type="month" value={month} onChange={e => { setMonth(e.target.value); resetTax(); }} /></label><label>Режим<select value={regime} onChange={e => { setRegime(e.target.value); setVat("exempt"); resetTax(); }}>{regimes.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label><label>Своя ставка, %<input value={rate} inputMode="decimal" onChange={e => { setRate(e.target.value); resetTax(); }} placeholder="По умолчанию для режима" /></label><label>НДС<select value={vat} onChange={e => { setVat(e.target.value); resetTax(); }}><option value="exempt">Освобождение</option>{!regime.startsWith("ausn") && <><option value="general0">0%</option><option value="general10">10%</option><option value={month.startsWith("2025") ? "general20" : "general22"}>{month.startsWith("2025") ? "20%" : "22%"}</option>{regime.startsWith("usn") && <><option value="special5">5%</option><option value="special7">7%</option></>}</>}</select></label><label><input type="checkbox" checked={staff} onChange={e => { setStaff(e.target.checked); resetTax(); }} /> Есть сотрудники</label></div>
      <p>{regime.startsWith("ausn") ? "АУСН: суммы за выбранный месяц." : "УСН / ОСНО: суммы нарастающим итогом с начала года до выбранного месяца."}</p>
      <div className="ozon-controls">{fields.map(([id, label]) => <label key={id}>{label}, ₽<input inputMode="decimal" value={values[id] ?? ""} onChange={e => { setValues(v => ({ ...v, [id]: e.target.value })); resetTax(); }} /></label>)}</div>
      <button type="button" className="secondary-button" disabled={busy || !canWrite} onClick={() => { setError(""); setTaxEpoch(v => v + 1); }}>Рассчитать оценку налога</button>
      {tax?.estimate && <p role="status">{tax.estimate.status === "incomplete" ? `Заполните: ${tax.estimate.missing.map(id => fields.find(([key]) => key === id)?.[1] ?? id).join(", ")}` : `Оценка налогов: ${money(tax.estimate.estimate)}. Налог на доход: ${money(tax.estimate.income_tax)}. НДС к уплате: ${money(tax.estimate.vat_to_pay)}.`}</p>}
      {!canWrite && <p>Расчёт доступен владельцу и менеджеру.</p>}
      <p>Оценка требует сверки с бухгалтером. Она не изменяет настройки WB и не включается повторно в результат Ozon.</p>
    </details>
  </section>;
}
