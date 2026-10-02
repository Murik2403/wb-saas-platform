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
  ["fns_assessed", "Начислено по документам", "Необязательно: совокупное начисление налога и НДС за тот же период, без пеней и других налогов."],
  ["paid", "Оплачено в счёт этого начисления", "Необязательно: не весь платёж на ЕНС, а сумма, отнесённая к налогу этого месяца."],
  ["expenses", "Подтверждённые налоговые расходы", "Расходы, допустимые именно для вашего режима. Управленческая себестоимость сама по себе не подтверждает налоговый расход."],
  ["loss", "Переносимый налоговый убыток", "По документам прошлых периодов. Для УСН — только итог года; для организаций на ОСНО применяется ограничение 50% базы."],
  ["insurance", "Взносы и допустимые уменьшения УСН", "Сумма, которую разрешено вычесть из налога по документам. Взносы в расходах повторно здесь не указывайте."],
  ["vat_output", "Исходящий НДС за период", "Уже рассчитанная сумма по книге продаж / документам. Доходы в форме указываются без этого НДС."],
  ["vat_input", "НДС к вычету за период", "Подтверждённый допустимый вычет; не вся сумма НДС в закупках. По ставкам 5/7% вычета нет."],
  ["advances", "Ранее начисленные авансы по основному налогу", "Необязательно: авансы этого года, чтобы оценить доплату. Не включайте НДС, платежи ЕНС и уже учтённые расходы."],
  ["other_ndfl_base", "Другие доходы в общей шкале НДФЛ", "Налоговая база резидента с начала года, попадающая в ту же пятиступенчатую шкалу. Не включайте её повторно в доходы магазина."],
] as const;
type Key = typeof fields[number][0];
export const taxRegimes = {
  ausn_income: ["АУСН «Доходы»", 8], ausn_income_expense: ["АУСН «Доходы − расходы»", 20],
  usn_income: ["УСН «Доходы»", 6], usn_income_expense: ["УСН «Доходы − расходы»", 15],
  osno_ip: ["ОСНО · ИП", null], osno_company: ["ОСНО · организация", 25],
} as const;
type Regime = keyof typeof taxRegimes;
type Values = Partial<Record<Key, number | null>> & { month: string; regime: string; source: string; confirmed: boolean; period_mode?: string; rate?: number | null; vat_mode?: string; has_staff?: boolean; taxpayer_type?: string };
type Result = { revision: number; can_write: boolean; values: Values; calculation: { status: string; missing: string[]; base: number | null; estimate: number | null; after_estimate: number | null; fns_difference: number | null; after_fns: number | null; assessed_minus_paid: number | null; negative_base: boolean; income_tax?: number | null; minimum_tax?: number | null; vat_to_pay?: number | null; vat_credit?: number | null; income_tax_to_pay?: number | null; advance_excess?: number | null; loss_used?: number | null; insurance_used?: number | null }; history: { revision: number; created_at: string; values: Values }[] };
const rub = (value: number | null) => value === null ? "Не указано" : `${value.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽`;
const empty = () => Object.fromEntries(fields.map(([key]) => [key, ""])) as Record<Key, string>;

export function buildTaxInput(cells: Record<Key, string>) {
  const values = {} as Record<Key, number | null>;
  for (const [key, label] of fields) {
    const text = (cells[key] ?? "").trim().replace(",", ".");
    if (!text) { values[key] = null; continue; }
    if (!/^-?\d+(\.\d{1,2})?$/.test(text)) throw new Error(`${label}: введите сумму с точностью до копейки.`);
    const number = Number(text);
    if (!Number.isFinite(number) || Math.abs(number) > 1e12 || (key !== "profit_before_tax" && number < 0)) throw new Error(`${label}: недопустимая сумма.`);
    values[key] = number;
  }
  return values;
}

export function taxReconciliationCsv(data: Result) {
  const rows = [["Месяц", data.values.month], ["Режим", taxRegimes[data.values.regime as Regime]?.[0] ?? data.values.regime], ["Период", data.values.period_mode === "ytd" ? "С начала года" : "Календарный месяц"], ["Ставка", String(data.values.rate ?? taxRegimes[data.values.regime as Regime]?.[1] ?? "Прогрессивная шкала")], ["НДС", data.values.vat_mode ?? "exempt"], ["Статус", data.calculation.status],
    ...fields.map(([key, label]) => [label, data.values[key] == null ? "Не указано" : String(data.values[key]).replace(".", ",")]),
    ["База", rub(data.calculation.base)], ["Оценка налога", rub(data.calculation.estimate)], ["После оценки", rub(data.calculation.after_estimate)],
    ["ФНС минус оценка", rub(data.calculation.fns_difference)], ["После начисления ФНС", rub(data.calculation.after_fns)], ["Начислено минус оплачено", rub(data.calculation.assessed_minus_paid)], ["Основной налог", rub(data.calculation.income_tax ?? null)], ["НДС к уплате (оценка)", rub(data.calculation.vat_to_pay ?? null)], ["Превышение вычетов НДС", rub(data.calculation.vat_credit ?? null)], ["Налогоплательщик", data.values.taxpayer_type ?? "ip"], ["Есть работники / ограничение 50%", data.values.has_staff ? "Да" : "Нет"], ["Основание", data.values.source]];
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
  const [regime, setRegime] = useState<Regime>("ausn_income");
  const [rate, setRate] = useState("8");
  const [vat, setVat] = useState("exempt");
  const [taxpayer, setTaxpayer] = useState("ip");
  const [staff, setStaff] = useState(false);
  const [tab, setTab] = useState("calculation");
  const [dirty, setDirty] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const fill = (next: Result) => { setData(next); setCells(Object.fromEntries(fields.map(([key]) => [key, next.values[key] == null ? "" : String(next.values[key])])) as Record<Key, string>); setSource(next.values.source); setConfirmed(next.values.confirmed); setRegime(next.values.regime as Regime); setRate(String(next.values.rate ?? taxRegimes[next.values.regime as Regime]?.[1] ?? "")); setVat(next.values.vat_mode ?? "exempt"); setStaff(next.values.has_staff ?? false); setTaxpayer(next.values.taxpayer_type ?? (next.values.regime === "osno_company" ? "company" : "ip")); setDirty(false); };
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
      if (regime !== "osno_ip" && (!rate.trim() || !/^\d+(\.\d{1,2})?$/.test(rate))) throw new Error("Укажите ставку с точностью до двух знаков.");
      const input = buildTaxInput(cells);
      const response = await scopedFetch(`${API_BASE_URL}/api/v1/settings/tax-reconciliation`, { method: "PUT", headers: apiHeaders(true), body: JSON.stringify({ ...input, month, regime, period_mode: regime.startsWith("ausn") ? "month" : "ytd", rate: regime === "osno_ip" ? null : Number(rate), vat_mode: vat, has_staff: staff, taxpayer_type: taxpayer, source, confirmed, revision: data.revision }) });
      if (!response.ok) throw await writeError(response);
      fill(await response.json() as Result); setMessage("Сверка сохранена. Предыдущие версии остались в истории.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось сохранить"); }
    finally { setBusy(false); }
  }
  const calculation = data?.calculation;
  const ausn = regime.startsWith("ausn");
  const expenseMode = regime.endsWith("expense") || regime.startsWith("osno");
  const commonKeys: Key[] = ["received", "offset", "compensation", "other_income", "refunds"];
  const specificKeys: Key[] = [...(expenseMode ? ["expenses" as Key] : []),
    ...(regime === "ausn_income_expense" || regime === "osno_company" || (regime === "usn_income_expense" && month.endsWith("-12")) ? ["loss" as Key] : []),
    ...(regime === "usn_income" ? ["insurance" as Key] : []), ...(regime === "osno_ip" ? ["other_ndfl_base" as Key] : []),
    ...(vat !== "exempt" ? ["vat_output" as Key] : []), ...(vat.startsWith("general") ? ["vat_input" as Key] : []),
    ...(!ausn ? ["advances" as Key] : []), "profit_before_tax"];
  const changed = () => { setDirty(true); setMessage(""); setConfirmed(false); };
  function chooseRegime(next: Regime) {
    setRegime(next); setRate(String(taxRegimes[next][1] ?? "")); setVat("exempt"); setStaff(false); setTaxpayer(next === "osno_company" ? "company" : "ip");
    setCells(previous => ({ ...previous, ...Object.fromEntries(["expenses", "loss", "insurance", "other_ndfl_base", "vat_output", "vat_input", "advances"].map(key => [key, ""])) })); changed();
  }
  const inputFields = (keys: Key[]) => <div className="tax-reconciliation-grid">{fields.filter(([key]) => keys.includes(key)).map(([key, label, hint]) => <label key={key}>{key === "received" && regime.startsWith("osno") ? "Признанный налоговый доход" : label}<input inputMode="decimal" value={cells[key]} disabled={busy || !data?.can_write} placeholder="Не указано" onChange={event => { setCells(previous => ({ ...previous, [key]: event.target.value })); changed(); }} /><small>{(key === "received" && regime.startsWith("osno") ? "Доход, признанный по документам налогового учёта с начала года; не банковская выплата." : hint).replace("за месяц", "за выбранный период").replace("этого месяца", "выбранного периода").replace("тот же месяц", "тот же период").replace("налогу этого месяца", "налогу выбранного периода")}</small></label>)}</div>;
  const stats = (items: [string, number | null | undefined][]) => <dl className="tax-reconciliation-grid tax-results">{items.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{rub(value ?? null)}</dd></div>)}</dl>;
  return <section className="work-section tax-reconciliation" id="tax-reconciliation" aria-label="Налоговый подраздел Финансов">
    <details open={typeof location !== "undefined" && location.hash === "#tax-reconciliation" ? true : undefined}>
      <summary className="tax-heading"><span><small>Финансы / Налоги</small><strong>Налоги · расчёт и сверка</strong></span><span className="tax-heading-badge">6 режимов</span></summary>
      <p className="form-hint">Выберите режим, внесите подтверждённые данные и сравните расчёт с начислением. Налоговый журнал выбранного магазина; данные других магазинов и бизнеса не подтягиваются автоматически.</p>
      <div className="tax-toolbar"><label>Месяц сверки<input type="month" min="2000-01" max="2099-12" value={month} disabled={busy} onChange={event => { if (event.target.value && (!dirty || window.confirm("Несохранённые изменения будут потеряны. Сменить период?"))) setMonth(event.target.value); }} /></label>
      <label>Налоговый режим<select value={regime} disabled={busy || !data?.can_write} onChange={event => chooseRegime(event.target.value as Regime)}>{Object.entries(taxRegimes).map(([key, [label]]) => <option value={key} key={key}>{label}</option>)}</select></label>
      <div className="tax-period"><small>Период исходных данных</small><strong>{ausn ? `${month}-01 — конец месяца` : `${month.slice(0, 4)}-01-01 — конец ${month}`}</strong><span>{ausn ? "Календарный месяц" : month.endsWith("-12") ? "Итог календарного года" : "Нарастающим итогом с начала года"}</span></div></div>
      <div className="tax-tabs" role="tablist" aria-label="Налоговый подраздел">{[["calculation", "Расчёт"], ["reconciliation", "Начисление и оплата"], ["history", "История"]].map(([id, title]) => <button key={id} id={`tax-tab-${id}`} aria-controls={`tax-panel-${id}`} type="button" role="tab" aria-selected={tab === id} tabIndex={tab === id ? 0 : -1} onKeyDown={event => {
          const ids = ["calculation", "reconciliation", "history"];
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault(); const index = ids.indexOf(id);
          const next = event.key === "Home" ? ids[0] : event.key === "End" ? ids[2] : ids[(index + (event.key === "ArrowRight" ? 1 : 2)) % 3];
          setTab(next); document.getElementById(`tax-tab-${next}`)?.focus();
        }} onClick={() => setTab(id)}>{title}</button>)}</div>
      {!data && !error ? <p role="status">Загружаем сверку…</p> : null}
      {error ? <p role="alert">{error} <button type="button" className="ghost-button" disabled={busy} onClick={() => { if (!dirty || window.confirm("Загрузить сохранённую версию вместо черновика?")) setRefresh(value => value + 1); }}>Загрузить актуальную версию</button></p> : null}
      {data ? <>
        {tab !== "history" ? <form onSubmit={event => { event.preventDefault(); void save(); }}>
          <div role="tabpanel" id={`tax-panel-${tab}`} aria-labelledby={`tax-tab-${tab}`}>
          {tab === "calculation" ? <>
            <div className="tax-note">{ausn ? "Доходы по поступлениям и взаимозачётам, а не по дате продажи WB." : regime.startsWith("osno") ? "Доходы и допустимые расходы по правилам ОСНО с начала года. Для организаций соблюдайте свой метод признания; для ИП — документы налогового учёта." : "Доходы и допустимые расходы по правилам УСН с начала года. Не используйте только один месяц для годовой базы."} При НДС суммы доходов и расходов вводите без НДС, который исключается из базы или принимается к вычету. Пустое поле — не ноль.</div>
            <h3>Доходы и возвраты</h3>{inputFields(commonKeys)}
            <h3>Налоговые параметры</h3><div className="tax-toolbar">
              <label>Налогоплательщик<select value={taxpayer} disabled={regime.startsWith("osno") || busy || !data.can_write} onChange={event => { setTaxpayer(event.target.value); changed(); }}><option value="ip">Индивидуальный предприниматель</option><option value="company">Организация</option></select></label>
              <label>Ставка основного налога, %{regime === "osno_ip" ? <strong>НДФЛ: 13 / 15 / 18 / 20 / 22%</strong> : <input inputMode="decimal" value={rate} disabled={ausn || busy || !data.can_write} onChange={event => { setRate(event.target.value.replace(",", ".")); changed(); }} />}<small>{ausn ? "Фиксированная ставка" : "Пониженную ставку подтвердите основанием; право на режим не определяется автоматически."}</small></label>
              <label>Режим НДС<select value={vat} disabled={ausn || busy || !data.can_write} onChange={event => { setVat(event.target.value); setCells(previous => ({ ...previous, vat_output: "", vat_input: "" })); changed(); }}><option value="exempt">Освобождение от НДС</option>{!ausn ? <><option value="general0">Общий НДС · 0%</option><option value="general10">Общий НДС · 10%</option><option value={month.startsWith("2025") ? "general20" : "general22"}>Общий НДС · {month.startsWith("2025") ? 20 : 22}%</option>{regime.startsWith("usn") ? <><option value="special5">Специальный НДС · 5%, без вычетов</option><option value="special7">Специальный НДС · 7%, без вычетов</option></> : null}</> : null}</select><small>Вносите суммы НДС из документов за тот же период. Смешанные ставки отражайте в общей сумме; декларация НДС составляется поквартально.</small></label>
            </div>
            {regime === "usn_income" ? <label className="tax-reconciliation-confirm"><input type="checkbox" checked={staff || taxpayer === "company"} disabled={busy || !data.can_write || taxpayer === "company"} onChange={event => { setStaff(event.target.checked); changed(); }} />Есть работники: уменьшение УСН ограничено 50% налога. Для организаций отметка обязательна.</label> : null}
            {inputFields(specificKeys)}
            <p className="form-hint">{regime === "ausn_income_expense" ? "Минимум — 3% дохода за месяц." : regime === "usn_income_expense" ? "Минимум 1% сравнивается только по итогам года (декабрь). Перенос убытка — только годовой расчёт." : regime === "osno_company" ? "Перенос убытков ограничен 50% положительной базы. Особые ставки и исключения подтверждаются документами." : regime === "osno_ip" ? "Шкала применяется к общей соответствующей базе НДФЛ резидента. Другие доходы учитываются для ставки; налог на них повторно не добавляется." : ""}</p>
          </> : <><h3>Сверка с начислением</h3><p>Введите начисление основного налога и НДС за тот же период. Не включайте пени и иные налоги. Оплата не уменьшает прибыль повторно.</p>{inputFields(["fns_assessed", "paid"])}</>}
          </div>
          <label>Основание / документы<input maxLength={500} value={source} required disabled={busy || !data.can_write} onChange={event => { setSource(event.target.value); changed(); }} /></label>
          <label className="tax-reconciliation-confirm"><input type="checkbox" checked={confirmed} required disabled={busy || !data.can_write} onChange={event => { setConfirmed(event.target.checked); setDirty(true); }} />Проверил даты, состав доходов и возвратов, право на ставки и вычеты; одни суммы не включены дважды.</label>
          {data.can_write ? <button className="primary-button" disabled={busy || !confirmed} type="submit">{busy ? "Сохраняем…" : "Сохранить и рассчитать"}</button> : <p>У вас доступ для просмотра. Сохранение доступно владельцу и менеджеру.</p>}
        </form> : <div role="tabpanel" id="tax-panel-history" aria-labelledby="tax-tab-history"><h3>История изменений ({data.history.length}, последние 50)</h3>{data.history.length ? data.history.map(item => <details className="tax-history-row" key={item.revision}><summary>Версия {item.revision} · {new Date(item.created_at).toLocaleString("ru-RU")} · {taxRegimes[item.values.regime as Regime]?.[0]}</summary><p>{item.values.source}</p><p>Период: {item.values.period_mode === "ytd" ? "с начала года" : "месяц"} · ставка {item.values.rate ?? taxRegimes[item.values.regime as Regime]?.[1] ?? "шкала НДФЛ"} · НДС {item.values.vat_mode ?? "exempt"}</p>{stats(fields.map(([key, label]) => [label, item.values[key]]))}</details>) : <p className="plan-empty">Сохранённых расчётов пока нет.</p>}</div>}
        {message ? <p role="status">{message}</p> : null}
        {tab !== "history" ? <div className="tax-saved-result"><h3>Сохранённый результат</h3><p className="form-hint">Версия {data.revision || "(данных нет)"} · {taxRegimes[data.values.regime as Regime]?.[0]} · {data.values.month} ({data.values.period_mode === "ytd" ? "с начала года" : "месяц"}){dirty ? " · Есть несохранённые изменения. Результат ниже ещё не обновлён." : ""}</p>
        {calculation?.missing.length ? <p className="warning-text">Оценка не рассчитана. Заполните: {fields.filter(([key]) => calculation.missing.includes(key)).map(([, label]) => label).join(", ")}.</p> : null}
        {calculation?.negative_base ? <p className="warning-text">База отрицательная. Проверьте документы: основной налог ограничен нулём, но минимальный налог может сохраняться.</p> : null}
        {stats(tab === "calculation" ? [["Налоговая база", calculation?.base], ["Основной налог (оценка)", calculation?.income_tax], ["Минимальный налог", calculation?.minimum_tax], ["НДС к уплате (оценка)", calculation?.vat_to_pay], ["Превышение вычетов НДС", calculation?.vat_credit], ["Всего налогов (оценка)", calculation?.estimate], ["После оценки налога", calculation?.after_estimate], ["Взносы зачтены", calculation?.insurance_used], ["Убыток зачтён", calculation?.loss_used], ["Доплата основного налога после авансов", calculation?.income_tax_to_pay], ["Авансы сверх оценки", calculation?.advance_excess]] : [["Начислено по документам", data.values.fns_assessed], ["Начисление − оценка", calculation?.fns_difference], ["После начисления", calculation?.after_fns], ["Оплачено", data.values.paid], ["Начислено − оплачено", calculation?.assessed_minus_paid]])}
        <p className="form-hint">Превышение вычетов НДС — не автоматическое возмещение. Расчёт не меняет P&L и денежный журнал, не подаёт декларации. Для нескольких магазинов сведите данные налогоплательщика; ФНС / бухгалтер подтверждают итог.</p></div> : null}
        <div className="tax-footer"><button className="ghost-button" type="button" onClick={() => { const url = URL.createObjectURL(new Blob([taxReconciliationCsv(data)], { type: "text/csv;charset=utf-8" })); const link = document.createElement("a"); link.href = url; link.download = `tax-${month}.csv`; link.click(); URL.revokeObjectURL(url); }}>Скачать сверку CSV</button><a href="https://www.nalog.gov.ru/new2026/" target="_blank" rel="noreferrer">Правила ФНС · 2026</a></div>
      </> : null}
    </details>
  </section>;
}
