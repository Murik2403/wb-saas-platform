import { useEffect, useState } from "react";
import { getManagementPnlMonthly } from "./data-source";
import type { DashboardPeriod, MonthlyPnl, MonthlyPnlLines, MonthlyTax } from "./data-source";
import "./ManagementPnl.css";

type LineKey = Exclude<keyof MonthlyPnlLines, "units" | "report_rows">;
type Kind = "total" | "income" | "expense";
type Line = { key: LineKey; label: string };

const EXPENSES: Line[] = [
  { key: "cost", label: "Себестоимость" },
  { key: "logistics", label: "Логистика" },
  { key: "commission", label: "Комиссия WB" },
  { key: "acquiring", label: "Эквайринг" },
  { key: "penalties", label: "Штрафы" },
  { key: "storage", label: "Хранение" },
  { key: "advertising", label: "Реклама" },
  { key: "other", label: "Прочее (удержания, приёмка, доплаты, остальное из отчёта WB)" },
];

export function directExpenses(lines: MonthlyPnlLines): number {
  return EXPENSES.reduce((sum, line) => sum + lines[line.key], 0);
}

export function monthLabel(month: string): string {
  const [year, number] = month.split("-").map(Number);
  return new Date(Date.UTC(year, number - 1, 1)).toLocaleDateString("ru-RU", { month: "short", year: "numeric", timeZone: "UTC" }).replace(" г.", "");
}

function rubles(value: number): string {
  const rounded = Math.round(value);
  const text = Math.abs(rounded).toLocaleString("ru-RU");
  return rounded < 0 ? `−${text}` : text;
}

/** Share of realization; null when there is no realization to compare against. */
export function shareOfRealization(value: number, realization: number): number | null {
  return realization > 0 ? (value / realization) * 100 : null;
}

function percent(value: number | null): string {
  if (value === null) return "—";
  const text = Math.abs(value).toLocaleString("ru-RU", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  return value < 0 && text !== "0,0" ? `−${text}%` : `${text}%`;
}

type Row = { label: string; kind: Kind; sub?: boolean; value: (lines: MonthlyPnlLines) => number };

/** Expense rows hold what the line does to the result, so costs are negative. */
export function monthlyRows(): Row[] {
  return [
    { label: "Реализация (цена после скидки продавца)", kind: "total", value: (l) => l.realization },
    { label: "Скидка МП / СПП", kind: "income", sub: true, value: (l) => l.spp_discount },
    { label: "Фактические продажи", kind: "total", value: (l) => l.sales },
    { label: "Прямые расходы", kind: "expense", value: (l) => -directExpenses(l) },
    ...EXPENSES.map((line): Row => ({ label: line.label, kind: "expense", sub: true, value: (l) => -l[line.key] })),
    { label: "Результат (финансовый результат WB)", kind: "total", value: (l) => l.result },
  ];
}

export const TAX_DISCLAIMER = "Оценка, не бухгалтерский учёт; окончательный расчёт — с бухгалтером.";
const TAX_RATES_MISSING = "ставка не задана";

export const hasTax = (data: MonthlyPnl) => data.tax_estimates_enabled === true && (data.months.some((m) => m.tax) || Boolean(data.total.tax));

/** Tax rows: taxes reduce the result, so they are negative; null means "show a placeholder". */
export function taxRows(): Array<{ label: string; value: (tax: MonthlyTax) => number | null }> {
  return [
    { label: "Налоги (оценка)", value: (t) => (t.total === null ? null : -t.total) },
    { label: "Результат после налогов (оценка)", value: (t) => t.profit_after_tax_estimate },
  ];
}

function taxText(tax: MonthlyTax | null | undefined, value: number | null): string {
  if (!tax) return "—";
  if (tax.status === "rates_missing" || value === null) return TAX_RATES_MISSING;
  return `${rubles(value)} ₽`;
}

function csvNumber(value: number, digits: number): string {
  const text = value.toFixed(digits);
  return (/^-0(\.0+)?$/.test(text) ? text.slice(1) : text).replace(".", ",");
}

/** UTF-8 BOM + ';' separator so Russian Excel opens it without an import wizard. */
export function buildMonthlyPnlCsv(data: MonthlyPnl): string {
  const columns: Array<{ title: string; lines: MonthlyPnlLines }> = data.months.map((m) => ({ title: monthLabel(m.month) + (m.partial ? " (часть месяца)" : ""), lines: m }));
  if (data.months.length > 1) columns.push({ title: "Итого", lines: data.total });
  const header = ["Показатель", ...columns.flatMap((c) => [`${c.title}, ₽`, `${c.title}, % от реализации`])];
  const taxBody = hasTax(data) ? taxRows().map((row) => [row.label, ...[...data.months, ...(data.months.length > 1 ? [data.total] : [])].flatMap((c) => {
    const tax = c.tax;
    const value = tax && tax.status !== "rates_missing" ? row.value(tax) : null;
    return [value === null ? (tax ? TAX_RATES_MISSING : "") : csvNumber(value, 2), ""];
  })]) : [];
  const body = monthlyRows().map((row) => [row.label, ...columns.flatMap((c) => {
    const value = row.value(c.lines);
    const share = shareOfRealization(value, c.lines.realization);
    return [csvNumber(value, 2), share === null ? "" : csvNumber(share, 1)];
  })]);
  const quote = (cell: string) => (/[;"\n]/.test(cell) ? `"${cell.replace(/"/g, '""')}"` : cell);
  return "﻿" + [header, ...body, ...taxBody].map((r) => r.map(quote).join(";")).join("\r\n") + "\r\n";
}

function downloadCsv(data: MonthlyPnl) {
  const blob = new Blob([buildMonthlyPnlCsv(data)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `opiu-po-mesyacam_${data.period.start}_${data.period.end}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function ManagementPnlMonthly({ period, onOpenTaxSettings }: { period: DashboardPeriod; onOpenTaxSettings?: () => void }) {
  const [data, setData] = useState<MonthlyPnl | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    getManagementPnlMonthly(period, controller.signal)
      .then((result) => setData(result))
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Не удалось загрузить ОПиУ по месяцам");
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [period.id, period.start, period.end]);

  const ready = data && !loading && data.data_status === "ok" ? data : null;
  const showTotal = (ready?.months.length ?? 0) > 1;
  const flagged = ready?.months.some((m) => m.partial || m.data_incomplete) ?? false;
  return <section className="panel pnl-monthly" aria-label="ОПиУ по месяцам">
    <div className="section-header">
      <div><div className="eyebrow">ОПиУ · по месяцам</div><h2>Отчёт о прибылях и убытках по месяцам</h2></div>
      {ready ? <button className="secondary-button" type="button" onClick={() => downloadCsv(ready)}>Скачать CSV</button> : null}
    </div>
    {loading ? <p className="form-hint">Считаем ОПиУ по месяцам…</p> : null}
    {error ? <p className="operation-error" role="alert">{error}</p> : null}
    {data && !loading && !ready ? <p className="form-hint">Нет финансовых данных WB за выбранный период.</p> : null}
    {ready ? <>
      <div className="pnl-monthly-scroll">
        <table className="pnl-monthly-table">
          <thead><tr>
            <th scope="col">Показатель</th>
            {ready.months.map((m) => <th scope="col" key={m.month}>{monthLabel(m.month)}{m.partial ? <small>часть месяца</small> : null}{m.data_incomplete ? <small>данные WB не полные</small> : null}</th>)}
            {showTotal ? <th scope="col" className="pnl-total-col">Итого</th> : null}
          </tr></thead>
          <tbody>
            {monthlyRows().map((row) => <tr key={row.label} className={`pnl-row-${row.kind}${row.sub ? " pnl-row-sub" : ""}`}>
              <th scope="row">{row.label}</th>
              {[...ready.months, ...(showTotal ? [ready.total] : [])].map((lines, index) => {
                const value = row.value(lines);
                return <td key={index} className={showTotal && index === ready.months.length ? "pnl-total-col" : undefined}><span>{rubles(value)} ₽</span><small>{percent(shareOfRealization(value, lines.realization))}</small></td>;
              })}
            </tr>)}
            {hasTax(ready) ? taxRows().map((row) => <tr key={row.label} className="pnl-row-expense pnl-row-tax">
              <th scope="row">{row.label}</th>
              {[...ready.months, ...(showTotal ? [ready.total] : [])].map((column, index) => {
                const tax = column.tax;
                const value = tax && tax.status !== "rates_missing" ? row.value(tax) : null;
                return <td key={index} className={showTotal && index === ready.months.length ? "pnl-total-col" : undefined}><span>{taxText(tax, value)}</span>{tax?.status === "partial" ? <small>не все месяцы</small> : tax?.loss ? <small>убыток по базе</small> : null}</td>;
              })}
            </tr>) : null}
          </tbody>
        </table>
      </div>
      {hasTax(ready)
        ? <p className="form-hint pnl-monthly-note">{TAX_DISCLAIMER}</p>
        : ready.tax_estimates_enabled === true ? <p className="form-hint pnl-monthly-note">Налоги: {onOpenTaxSettings ? <button className="ghost-button" type="button" onClick={onOpenTaxSettings}>укажите режим</button> : "укажите режим"} в настройках, чтобы увидеть оценку налогов и результат после них.</p> : null}
      <p className="form-hint pnl-monthly-note">{ready.note} Под суммой — доля от реализации. Результат — финансовый результат WB с hybrid FIFO-себестоимостью; подтверждённые внешние расходы из сводки выше не вычитаются.{ready.data_through ? ` Отчёты WB загружены по ${ready.data_through}.` : ""}{flagged ? " Месяцы с пометками показаны за часть месяца или не полностью: сравнивать их с полными месяцами нельзя." : ""}</p>
    </> : null}
  </section>;
}
