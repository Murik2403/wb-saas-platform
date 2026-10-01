import { useEffect, useMemo, useRef, useState } from "react";
import { Columns3, Download, LayoutGrid, List, Table2 } from "lucide-react";
import type { ProductEconomicsRow, ProductsTrend } from "./data-source";
import { ProductSparkline } from "./ProductSparkline";
import { buildCsv } from "./csv";
import "./ProductEconomicsList.css";

export type ProductViewMode = "tiles" | "table" | "list";

const MODES: Array<{ id: ProductViewMode; label: string; Icon: typeof List }> = [
  { id: "tiles", label: "Плитка", Icon: LayoutGrid },
  { id: "table", label: "Таблица", Icon: Table2 },
  { id: "list", label: "Список", Icon: List },
];
const STORAGE_KEY = "mh.products.viewMode";
const COLUMNS_KEY = "mh.products.columns";

const rub = (value: number) => `${Math.round(value).toLocaleString("ru-RU")} ₽`;
const num = (value: number) => value.toLocaleString("ru-RU");
const pct = (value: number) => `${value.toFixed(1)}%`;
const marginOf = (item: ProductEconomicsRow) =>
  Number.isFinite(item.marginPct ?? NaN) ? (item.marginPct ?? 0) : item.revenue > 0 ? (item.margin / item.revenue) * 100 : 0;
const stockOf = (item: ProductEconomicsRow) =>
  `${item.stock.toLocaleString("ru-RU")} шт.${item.daysOfStock !== null ? ` · ${item.daysOfStock.toFixed(1)} дн.` : " · прогноз недоступен"}`;

// ABC (Pareto) classes: A = items up to and including the one that crosses 80% of the positive total, B = up to 95%, C = the rest.
export type AbcClass = "A" | "B" | "C" | "loss" | "none";
export const ABC_A = 0.8;
export const ABC_B = 0.95;
export function abcClasses(values: number[], kind: "revenue" | "profit"): AbcClass[] {
  const total = values.reduce((sum, v) => sum + (v > 0 ? v : 0), 0);
  const result: AbcClass[] = values.map((v) => (v < 0 && kind === "profit" ? "loss" : kind === "profit" && v === 0 ? "none" : "C"));
  const eps = total * 1e-9;
  let before = 0;
  values.map((_, i) => i).filter((i) => values[i] > 0).sort((a, b) => values[b] - values[a] || a - b).forEach((i) => {
    result[i] = before < total * ABC_A - eps ? "A" : before < total * ABC_B - eps ? "B" : "C";
    before += values[i];
  });
  return result;
}
// Days in the selected period (custom: both ends inclusive); null when it cannot be told.
export function periodLengthDays(period: { id: string; start?: string; end?: string }): number | null {
  const fixed: Record<string, number> = { today: 1, "7d": 7, "30d": 30, "90d": 90 };
  if (period.id in fixed) return fixed[period.id];
  if (period.id !== "custom" || !period.start || !period.end) return null;
  const days = (Date.parse(period.end) - Date.parse(period.start)) / 86_400_000 + 1;
  return Number.isFinite(days) && days >= 1 ? Math.round(days) : null;
}
const ABC_RANK: Record<AbcClass, number> = { A: 3, B: 2, C: 1, none: 0, loss: -1 };
const ABC_TEXT: Record<AbcClass, string> = { A: "A", B: "B", C: "C", none: "—", loss: "убыток" };
const abcAria = (cls: AbcClass, what: string) =>
  cls === "A" ? `Класс A по ${what}: входит в первые 80%`
    : cls === "B" ? `Класс B по ${what}: входит в следующие 15% (до 95%)`
    : cls === "C" ? `Класс C по ${what}: последние 5%`
    : cls === "loss" ? `Убыток: класс по ${what} не присваивается` : `Класс по ${what} не присваивается: значение равно нулю`;
const capitalOf = (item: ProductEconomicsRow) => (item.unitCost > 0 ? item.stock * item.unitCost : null);

type Ctx = { revenueAbc: Map<ProductEconomicsRow, AbcClass>; profitAbc: Map<ProductEconomicsRow, AbcClass>; periodDays: number | null };
const gmroiOf = (item: ProductEconomicsRow, ctx: Ctx) => {
  const cap = capitalOf(item);
  return cap && cap > 0 && ctx.periodDays ? Math.round(((item.margin * 365) / ctx.periodDays / cap) * 100) / 100 : null;
};

type ColumnKey = "product" | "revenue" | "margin" | "marginPct" | "drr" | "stock" | "trend" | "orders" | "sales" | "buyout" | "adSpend" | "dailySales" | "daysOfStock" | "unitCost" | "abcRevenue" | "abcProfit" | "capital" | "gmroi";
type Column = {
  key: ColumnKey;
  label: string;
  sortable?: false; // a chart column: no sorting, not exported
  optional?: boolean; // hidden until the user turns it on
  title?: string; // header tooltip: the definition
  needsPeriod?: boolean; // unavailable while the period length is unknown
  aria?: (item: ProductEconomicsRow, ctx: Ctx) => string;
  value: (item: ProductEconomicsRow, ctx: Ctx) => string | number | null; // sorting and numeric CSV
  text?: (item: ProductEconomicsRow, ctx: Ctx) => string; // what the cell shows
  csv?: "text"; // export the shown text instead of the raw number
  negative?: (item: ProductEconomicsRow, ctx: Ctx) => boolean;
};
const COLUMNS: Column[] = [
  { key: "product", label: "Товар", value: (i) => i.product },
  { key: "revenue", label: "Выручка", value: (i) => i.revenue, text: (i) => rub(i.revenue) },
  { key: "margin", label: "Расчётная прибыль", value: (i) => i.margin, text: (i) => rub(i.margin), negative: (i) => i.margin < 0 },
  { key: "marginPct", label: "Маржа", value: (i) => marginOf(i), text: (i) => pct(marginOf(i)), negative: (i) => marginOf(i) < 0 },
  { key: "drr", label: "ДРР", value: (i) => i.drr, text: (i) => pct(i.drr) },
  { key: "stock", label: "Запас", value: (i) => i.stock, text: stockOf, csv: "text" },
  { key: "trend", label: "Динамика выручки", sortable: false, value: () => null },
  { key: "orders", label: "Заказы, шт.", optional: true, value: (i) => i.orders, text: (i) => num(i.orders) },
  { key: "sales", label: "Продажи, шт.", optional: true, value: (i) => i.sales, text: (i) => num(i.sales) },
  // buyout is a percent (the "Выкуп, %" column of the products report), not a count
  { key: "buyout", label: "Выкуп, %", optional: true, value: (i) => i.buyout, text: (i) => pct(i.buyout) },
  { key: "adSpend", label: "Расходы на рекламу", optional: true, value: (i) => i.adSpend, text: (i) => rub(i.adSpend) },
  { key: "dailySales", label: "Продаж в день, шт.", optional: true, value: (i) => i.dailySales, text: (i) => num(Math.round(i.dailySales * 10) / 10) },
  { key: "daysOfStock", label: "Хватит дней", optional: true, value: (i) => i.daysOfStock, text: (i) => (i.daysOfStock === null ? "—" : i.daysOfStock.toFixed(1)) },
  { key: "unitCost", label: "Себестоимость за шт.", optional: true, value: (i) => i.unitCost, text: (i) => rub(i.unitCost) },
  { key: "abcRevenue", label: "ABC по выручке", optional: true, title: "Класс по выручке в текущем списке: A — товары, дающие первые 80% выручки (включая тот, что пересёк 80%), B — до 95%, C — остальные и товары без выручки.",
    value: (i, c) => ABC_RANK[c.revenueAbc.get(i) ?? "C"], text: (i, c) => ABC_TEXT[c.revenueAbc.get(i) ?? "C"], csv: "text", aria: (i, c) => abcAria(c.revenueAbc.get(i) ?? "C", "выручке") },
  { key: "abcProfit", label: "ABC по прибыли", optional: true, title: "Класс по расчётной прибыли в текущем списке: A — товары, дающие первые 80% положительной прибыли, B — до 95%, C — остальные. Нулевая прибыль — «—», отрицательная — «убыток» (класс не присваивается).",
    value: (i, c) => ABC_RANK[c.profitAbc.get(i) ?? "none"], text: (i, c) => ABC_TEXT[c.profitAbc.get(i) ?? "none"], csv: "text", aria: (i, c) => abcAria(c.profitAbc.get(i) ?? "none", "прибыли"), negative: (i, c) => c.profitAbc.get(i) === "loss" },
  { key: "capital", label: "Капитализация, ₽", optional: true, title: "Остаток на складе по себестоимости: запас × себестоимость за шт. на текущий момент. «—» — себестоимость не задана.",
    value: (i) => capitalOf(i), text: (i) => { const v = capitalOf(i); return v === null ? "—" : rub(v); } },
  { key: "gmroi", label: "GMROI", optional: true, needsPeriod: true, title: "Годовая расчётная прибыль на рубль запаса по себестоимости: (расчётная прибыль за период × 365 / дней в периоде) / капитализация. Это расчётная прибыль кабинета, а не бухгалтерская валовая маржа. «—» — нет себестоимости или запаса.",
    value: (i, c) => gmroiOf(i, c), text: (i, c) => { const v = gmroiOf(i, c); return v === null ? "—" : v.toFixed(2).replace(".", ","); }, negative: (i, c) => (gmroiOf(i, c) ?? 0) < 0 },
];
const DEFAULT_COLUMNS = COLUMNS.filter((c) => !c.optional).map((c) => c.key);

// localStorage can be blocked or throw (private window, cleared site data): the view must still work.
function readMode(): ProductViewMode {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    return MODES.some((mode) => mode.id === saved) ? (saved as ProductViewMode) : "tiles";
  } catch {
    return "tiles";
  }
}

// Anything that is not a JSON array falls back to the defaults; unknown keys are ignored; product is always on.
function readColumns(): ColumnKey[] {
  try {
    const saved: unknown = JSON.parse(window.localStorage.getItem(COLUMNS_KEY) ?? "null");
    if (!Array.isArray(saved)) return DEFAULT_COLUMNS;
    return COLUMNS.filter((c) => c.key === "product" || saved.includes(c.key)).map((c) => c.key);
  } catch {
    return DEFAULT_COLUMNS;
  }
}

function downloadCsv(content: string) {
  const url = URL.createObjectURL(new Blob([content], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `tovary-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

// "ABC по выручке — A: 3 SKU = 83% выручки · B: 5 · C: 12" (profit also counts loss-makers).
export function abcSummaryText(items: ProductEconomicsRow[], classes: Map<ProductEconomicsRow, AbcClass>, byRevenue: boolean): string {
  const metric = (i: ProductEconomicsRow) => (byRevenue ? i.revenue : i.margin);
  const count = (cls: AbcClass) => items.filter((i) => classes.get(i) === cls).length;
  const total = items.reduce((sum, i) => sum + Math.max(metric(i), 0), 0);
  const aSum = items.filter((i) => classes.get(i) === "A").reduce((sum, i) => sum + metric(i), 0);
  const share = total > 0 ? ` = ${Math.round((aSum / total) * 100)}% ${byRevenue ? "выручки" : "прибыли"}` : "";
  const loss = count("loss");
  return `${byRevenue ? "ABC по выручке" : "ABC по прибыли"} — A: ${count("A")} SKU${share} · B: ${count("B")} · C: ${count("C")}${loss ? ` · убыток: ${loss}` : ""}`;
}

// trend: lazily loaded daily revenue per SKU; while it is missing the chart cells show a dash.
export function ProductEconomicsList({ items, onOpen, trend = null, periodDays = null }: { items: ProductEconomicsRow[]; onOpen: (item: ProductEconomicsRow) => void; trend?: ProductsTrend | null; periodDays?: number | null }) {
  const [mode, setMode] = useState<ProductViewMode>(readMode);
  const [visible, setVisible] = useState<ColumnKey[]>(readColumns);
  const [pickerOpen, setPickerOpen] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);
  const pickerButtonRef = useRef<HTMLButtonElement>(null);
  // Sorting belongs to the table only; tiles and list keep the order the API returned.
  const [sort, setSort] = useState<{ key: ColumnKey; dir: "asc" | "desc" } | null>(null);
  const ctx = useMemo<Ctx>(() => {
    const toMap = (kind: "revenue" | "profit") => {
      const classes = abcClasses(items.map((i) => (kind === "revenue" ? i.revenue : i.margin)), kind);
      return new Map(items.map((item, n) => [item, classes[n]] as const));
    };
    return { revenueAbc: toMap("revenue"), profitAbc: toMap("profit"), periodDays };
  }, [items, periodDays]);
  const available = (c: Column) => !c.needsPeriod || periodDays !== null;
  const columns = COLUMNS.filter((c) => visible.includes(c.key) && available(c));
  // A column hidden while sorted by it must not keep reordering rows invisibly.
  const activeSort = sort && columns.some((c) => c.key === sort.key) ? sort : null;
  const sorted = useMemo(() => {
    if (!activeSort) return items;
    const column = COLUMNS.find((c) => c.key === activeSort.key)!;
    const sign = activeSort.dir === "asc" ? 1 : -1;
    return [...items].sort((a, b) => {
      const x = column.value(a, ctx);
      const y = column.value(b, ctx);
      if (x === null || y === null) return x === y ? 0 : x === null ? 1 : -1; // missing values go last in both directions
      return sign * (typeof x === "string" ? x.localeCompare(String(y), "ru") : x - Number(y));
    });
  }, [items, activeSort, ctx]);
  // First click: text A-Z, numbers high-to-low (what people look for); second click reverses.
  const toggleSort = (key: ColumnKey) =>
    setSort((current) => current?.key === key
      ? { key, dir: current.dir === "asc" ? "desc" : "asc" }
      : { key, dir: key === "product" ? "asc" : "desc" });
  const choose = (next: ProductViewMode) => {
    setMode(next);
    try { window.localStorage.setItem(STORAGE_KEY, next); } catch { /* per-viewer convenience only */ }
  };
  const saveColumns = (next: ColumnKey[]) => {
    setVisible(next);
    try { window.localStorage.setItem(COLUMNS_KEY, JSON.stringify(next)); } catch { /* per-viewer convenience only */ }
  };
  const toggleColumn = (key: ColumnKey) =>
    saveColumns(COLUMNS.filter((c) => c.key === "product" || (c.key === key ? !visible.includes(key) : visible.includes(c.key))).map((c) => c.key));
  const exportCsv = () => {
    const exported = columns.filter((c) => c.sortable !== false);
    const headers = exported.flatMap((c) => (c.key === "product" ? [c.label, "Артикул"] : [c.label]));
    const rows = sorted.map((item) => exported.flatMap((c) =>
      c.key === "product" ? [item.product, item.article || item.nmId] : [c.csv === "text" ? c.text!(item, ctx) : c.value(item, ctx)]));
    downloadCsv(buildCsv(headers, rows));
  };

  const abcSummary = columns.flatMap((c) => c.key === "abcRevenue" || c.key === "abcProfit" ? [{ key: c.key, text: abcSummaryText(items, c.key === "abcRevenue" ? ctx.revenueAbc : ctx.profitAbc, c.key === "abcRevenue") }] : []);

  useEffect(() => {
    if (!pickerOpen) return;
    const onPointer = (event: MouseEvent) => {
      if (!pickerRef.current?.contains(event.target as Node)) setPickerOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setPickerOpen(false); pickerButtonRef.current?.focus(); }
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onPointer); document.removeEventListener("keydown", onKey); };
  }, [pickerOpen]);

  return (
    <>
      <div className="product-list-toolbar">
        <div className="product-view-switch" role="group" aria-label="Вид списка товаров">
          {MODES.map(({ id, label, Icon }) => (
            <button key={id} type="button" className={mode === id ? "active" : ""} aria-pressed={mode === id} title={label} onClick={() => choose(id)}>
              <Icon size={16} aria-hidden="true" />
              <span>{label}</span>
            </button>
          ))}
        </div>
        {mode === "table" ? (
          <div className="product-table-tools">
            <div className="product-column-picker" ref={pickerRef}>
              <button type="button" className="product-tool-button" aria-expanded={pickerOpen} aria-controls="product-column-menu" ref={pickerButtonRef} onClick={() => setPickerOpen((open) => !open)}>
                <Columns3 size={16} aria-hidden="true" /><span>Колонки</span>
              </button>
              {pickerOpen ? (
                <div className="product-column-menu" id="product-column-menu" role="group" aria-label="Колонки таблицы">
                  {COLUMNS.map((c) => (
                    <label key={c.key} title={available(c) ? c.title : "Недоступно: длина периода неизвестна"}>
                      <input type="checkbox" checked={visible.includes(c.key)} disabled={c.key === "product" || !available(c)} onChange={() => toggleColumn(c.key)} />
                      <span>{c.key === "product" ? "Товар и артикул" : c.label}</span>
                    </label>
                  ))}
                  <button type="button" className="product-tool-button" onClick={() => saveColumns(DEFAULT_COLUMNS)}>Сбросить</button>
                </div>
              ) : null}
            </div>
            <button type="button" className="product-tool-button" onClick={exportCsv}>
              <Download size={16} aria-hidden="true" /><span>Скачать CSV</span>
            </button>
          </div>
        ) : null}
      </div>

      {mode === "tiles" ? (
        <div className="product-economics-cards">
          {items.map((item) => (
            <button className="product-economics-card detail-trigger" key={item.nmId} type="button" onClick={() => onOpen(item)}>
              <div className="product-economics-card-heading">
                <div><strong>{item.product}</strong><span>{item.article || item.nmId}</span></div>
                <strong className={marginOf(item) < 0 ? "negative" : ""}>{marginOf(item).toFixed(1)}%</strong>
              </div>
              <div className="product-economics-card-metrics">
                <span>Расчётная прибыль<strong>{rub(item.margin)}</strong></span>
                <span>ДРР<strong>{item.drr.toFixed(1)}%</strong></span>
                <span>Запас<strong>{stockOf(item)}</strong></span>
              </div>
            </button>
          ))}
        </div>
      ) : null}

      {mode === "table" ? abcSummary.map((line) => <p className="product-abc-summary" key={line.key}>{line.text}</p>) : null}
      {mode === "table" ? (
        <div className="product-table-scroll">
          <table className="product-economics-table">
            <thead>
              <tr>
                {columns.map(({ key, label, sortable, title }) => sortable === false ? <th key={key}>{label}</th> : (
                  <th key={key} title={title} aria-sort={activeSort?.key === key ? (activeSort.dir === "asc" ? "ascending" : "descending") : "none"}>
                    <button type="button" className="product-sort" onClick={() => toggleSort(key)}>
                      {label}<span aria-hidden="true">{activeSort?.key === key ? (activeSort.dir === "asc" ? " ▲" : " ▼") : ""}</span>
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((item) => (
                <tr key={item.nmId} onClick={() => onOpen(item)}>
                  {columns.map((c) => c.key === "product" ? (
                    <td key={c.key}>
                      <button type="button" className="product-row-open" onClick={(event) => { event.stopPropagation(); onOpen(item); }}>
                        <strong>{item.product}</strong><small>{item.article || item.nmId}</small>
                      </button>
                    </td>
                  ) : c.key === "trend" ? (
                    <td key={c.key} className="product-trend-cell">
                      <ProductSparkline values={trend?.series[String(item.nmId)]?.revenue ?? []} />
                    </td>
                  ) : (
                    <td key={c.key} className={c.negative?.(item, ctx) ? "negative" : ""} aria-label={c.aria?.(item, ctx)}>{c.text!(item, ctx)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {mode === "list" ? (
        <ul className="product-economics-rows">
          {items.map((item) => (
            <li key={item.nmId}>
              <button type="button" className="product-economics-row detail-trigger" onClick={() => onOpen(item)}>
                <span className="product-row-name"><strong>{item.product}</strong><small>{item.article || item.nmId}</small></span>
                <span className={marginOf(item) < 0 ? "negative" : ""}>{rub(item.margin)} · {marginOf(item).toFixed(1)}%</span>
                <span>ДРР {item.drr.toFixed(1)}%</span>
                <span>{stockOf(item)}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </>
  );
}
