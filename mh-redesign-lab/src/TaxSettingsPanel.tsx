import { useEffect, useState } from "react";
import { getTaxSettings, saveTaxSettings } from "./data-source";
import type { TaxRate, TaxRegime } from "./data-source";
import "./TaxSettingsPanel.css";

const REGIMES: Array<{ id: string; label: string; supported: boolean; why?: string }> = [
  { id: "usn_income", label: "УСН «Доходы»", supported: true },
  { id: "usn_income_expense", label: "УСН «Доходы−Расходы», НДС по ставке из таблицы", supported: true },
  { id: "usn_income_expense_vat22", label: "УСН «Доходы−Расходы» с НДС 22%", supported: false, why: "нужен входящий НДС по поставкам — пока не считается" },
  { id: "osno_ip", label: "ИП на ОСНО", supported: false, why: "нужен входящий НДС и НДФЛ — пока не считается" },
  { id: "osno_ooo", label: "ООО на ОСНО", supported: false, why: "нужен входящий НДС и налог на прибыль — пока не считается" },
];
const QUARTERS = ["I квартал", "II квартал", "III квартал", "IV квартал"];
const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];

export type TaxCell = { usn: string; vat: string };
const emptyCells = (): TaxCell[] => Array.from({ length: 12 }, () => ({ usn: "", vat: "" }));

/** Number or null when empty; NaN for garbage so validation can reject it. */
const parse = (text: string): number | null => (text.trim() === "" ? null : Number(text.replace(",", ".")));
const valid = (n: number) => Number.isFinite(n) && n >= 0 && n <= 100;

/** Pure: grid state -> rates to send, or a Russian error message. */
export function buildRates(cells: TaxCell[]): { rates: TaxRate[] } | { error: string } {
  const rates: TaxRate[] = [];
  for (let i = 0; i < 12; i += 1) {
    const usn = parse(cells[i].usn);
    const vat = parse(cells[i].vat);
    if (usn === null && vat === null) continue;
    if (usn === null || vat === null) return { error: `${MONTHS[i]}: заполните обе ставки — УСН и НДС — или очистите обе.` };
    if (!valid(usn) || !valid(vat)) return { error: `${MONTHS[i]}: ставка должна быть числом от 0 до 100.` };
    rates.push({ month: i + 1, usn_pct: usn, vat_pct: vat });
  }
  return { rates };
}

export function TaxSettingsPanel() {
  const thisYear = new Date().getFullYear();
  const [enabled, setEnabled] = useState(false);
  const [year, setYear] = useState(thisYear);
  const [regime, setRegime] = useState<TaxRegime | null>(null);
  const [costOfficial, setCostOfficial] = useState(false);
  const [cells, setCells] = useState<TaxCell[]>(emptyCells);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setStatus(null);
    getTaxSettings(year, controller.signal)
      .then((data) => {
        setEnabled(data.enabled === true);
        if (data.enabled !== true) return;
        setRegime(data.regime);
        setCostOfficial(data.cost_official);
        const next = emptyCells();
        for (const rate of data.rates) next[rate.month - 1] = { usn: String(rate.usn_pct), vat: String(rate.vat_pct) };
        setCells(next);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setStatus({ kind: "error", text: reason instanceof Error ? reason.message : "Не удалось загрузить налоговые ставки" });
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [year]);

  const setCell = (months: number[], field: keyof TaxCell, value: string) => {
    setStatus(null);
    setCells((current) => current.map((cell, i) => (months.includes(i) ? { ...cell, [field]: value } : cell)));
  };
  // A quarter shows its value only while all three months agree.
  const quarterValue = (q: number, field: keyof TaxCell) => {
    const values = [0, 1, 2].map((i) => cells[q * 3 + i][field]);
    return values.every((v) => v === values[0]) ? values[0] : "";
  };

  const differ = (q: number, field: keyof TaxCell) => new Set([0, 1, 2].map((i) => cells[q * 3 + i][field])).size > 1;

  const save = async () => {
    if (!regime) { setStatus({ kind: "error", text: "Выберите режим налогообложения." }); return; }
    const built = buildRates(cells);
    if ("error" in built) { setStatus({ kind: "error", text: built.error }); return; }
    setBusy(true);
    setStatus(null);
    try {
      await saveTaxSettings({ year, regime, cost_official: regime === "usn_income_expense" && costOfficial, rates: built.rates });
      setStatus({ kind: "ok", text: "Сохранено. Налоги в ОПиУ по месяцам пересчитаются при следующей загрузке." });
    } catch (reason) {
      setStatus({ kind: "error", text: reason instanceof Error ? reason.message : "Не удалось сохранить налоговые ставки" });
    } finally {
      setBusy(false);
    }
  };

  const rateInput = (months: number[], field: keyof TaxCell, label: string, value: string, placeholder = "") =>
    <input type="number" inputMode="decimal" min={0} max={100} step="0.1" aria-label={label} value={value} placeholder={placeholder} disabled={loading || busy}
      onChange={(event) => setCell(months, field, event.target.value)} />;

  if (!enabled) return null;

  return <section className="work-section tax-settings" id="tax-settings" aria-label="Налоговые ставки">
    <div className="section-header"><div><div className="eyebrow">Экономика</div><h2>Налоговые ставки</h2></div></div>
    <p className="form-hint">Режим выбирается на год, ставки — по месяцам. Нужны только для оценки налогов в ОПиУ по месяцам. Это не бухгалтерский сервис: окончательный расчёт — с бухгалтером.</p>
    <label className="operation-field tax-year"><span>Год</span>
      <select value={year} disabled={busy} onChange={(event) => setYear(Number(event.target.value))}>
        {[thisYear - 2, thisYear - 1, thisYear, thisYear + 1].map((y) => <option key={y} value={y}>{y}</option>)}
      </select>
    </label>
    <fieldset className="tax-regimes" disabled={loading || busy}>
      <legend>Режим налогообложения за {year} год</legend>
      {REGIMES.map((item) => <label key={item.id} className={`tax-regime${item.supported ? "" : " tax-regime-disabled"}`}>
        <input type="radio" name="tax-regime" value={item.id} disabled={!item.supported} checked={regime === item.id}
          onChange={() => { setRegime(item.id as TaxRegime); setStatus(null); }} />
        <span>{item.label}{item.why ? <small>{item.why}</small> : null}</span>
      </label>)}
    </fieldset>
    {regime === "usn_income_expense" ? <label className="checkbox-field tax-cost-official">
      <input type="checkbox" checked={costOfficial} disabled={loading || busy} onChange={(event) => { setCostOfficial(event.target.checked); setStatus(null); }} />
      <span>Учитывать себестоимость товара как официальный расход</span>
    </label> : null}
    <div className="tax-grid-scroll">
      <table className="tax-grid">
        <thead><tr><th scope="col">Период</th><th scope="col">УСН, %</th><th scope="col">НДС, %</th></tr></thead>
        <tbody>
          {QUARTERS.map((quarter, q) => {
            const months = [q * 3, q * 3 + 1, q * 3 + 2];
            return [
              <tr key={quarter} className="tax-quarter">
                <th scope="row">{quarter}<small>значение заполнит все три месяца</small></th>
                <td>{rateInput(months, "usn", `УСН, % — ${quarter}`, quarterValue(q, "usn"), differ(q, "usn") ? "разные" : "")}</td>
                <td>{rateInput(months, "vat", `НДС, % — ${quarter}`, quarterValue(q, "vat"), differ(q, "vat") ? "разные" : "")}</td>
              </tr>,
              ...months.map((m) => <tr key={MONTHS[m]} className="tax-month">
                <th scope="row">{MONTHS[m]}</th>
                <td>{rateInput([m], "usn", `УСН, % — ${MONTHS[m]}`, cells[m].usn)}</td>
                <td>{rateInput([m], "vat", `НДС, % — ${MONTHS[m]}`, cells[m].vat)}</td>
              </tr>),
            ];
          })}
        </tbody>
      </table>
    </div>
    <p className="form-hint">Месяц без ставок не считается: в ОПиУ будет «ставка не задана». Минимальный налог (1% от доходов) не применяется.</p>
    <div className="tax-actions">
      <button className="primary-button" type="button" disabled={loading || busy} onClick={() => void save()}>{busy ? "Сохраняем" : "Сохранить ставки"}</button>
      {status ? <p className={status.kind === "ok" ? "form-hint tax-ok" : "operation-error"} role={status.kind === "ok" ? "status" : "alert"}>{status.text}</p> : null}
    </div>
  </section>;
}
