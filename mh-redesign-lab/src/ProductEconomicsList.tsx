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

type ColumnKey = "product" | "revenue" | "margin" | "marginPct" | "drr" | "stock" | "trend" | "orders" | "sales" | "buyout" | "adSpend" | "dailySales" | "daysOfStock" | "unitCost";
type Column = {
  key: ColumnKey;
  label: string;
  sortable?: false; // a chart column: no sorting, not exported
  optional?: boolean; // hidden until the user turns it on
  value: (item: ProductEconomicsRow) => string | number | null; // sorting and numeric CSV
  text?: (item: ProductEconomicsRow) => string; // what the cell shows
  csv?: "text"; // export the shown text instead of the raw number
  negative?: (item: ProductEconomicsRow) => boolean;
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

// trend: lazily loaded daily revenue per SKU; while it is missing the chart cells show a dash.
export function ProductEconomicsList({ items, onOpen, trend = null }: { items: ProductEconomicsRow[]; onOpen: (item: ProductEconomicsRow) => void; trend?: ProductsTrend | null }) {
  const [mode, setMode] = useState<ProductViewMode>(readMode);
  const [visible, setVisible] = useState<ColumnKey[]>(readColumns);
  const [pickerOpen, setPickerOpen] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);
  const pickerButtonRef = useRef<HTMLButtonElement>(null);
  // Sorting belongs to the table only; tiles and list keep the order the API returned.
  const [sort, setSort] = useState<{ key: ColumnKey; dir: "asc" | "desc" } | null>(null);
  const columns = COLUMNS.filter((c) => visible.includes(c.key));
  // A column hidden while sorted by it must not keep reordering rows invisibly.
  const activeSort = sort && visible.includes(sort.key) ? sort : null;
  const sorted = useMemo(() => {
    if (!activeSort) return items;
    const column = COLUMNS.find((c) => c.key === activeSort.key)!;
    const sign = activeSort.dir === "asc" ? 1 : -1;
    return [...items].sort((a, b) => {
      const x = column.value(a);
      const y = column.value(b);
      if (x === null || y === null) return x === y ? 0 : x === null ? 1 : -1; // missing values go last in both directions
      return sign * (typeof x === "string" ? x.localeCompare(String(y), "ru") : x - Number(y));
    });
  }, [items, activeSort]);
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
      c.key === "product" ? [item.product, item.article || item.nmId] : [c.csv === "text" ? c.text!(item) : c.value(item)]));
    downloadCsv(buildCsv(headers, rows));
  };

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
                    <label key={c.key}>
                      <input type="checkbox" checked={visible.includes(c.key)} disabled={c.key === "product"} onChange={() => toggleColumn(c.key)} />
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

      {mode === "table" ? (
        <div className="product-table-scroll">
          <table className="product-economics-table">
            <thead>
              <tr>
                {columns.map(({ key, label, sortable }) => sortable === false ? <th key={key}>{label}</th> : (
                  <th key={key} aria-sort={activeSort?.key === key ? (activeSort.dir === "asc" ? "ascending" : "descending") : "none"}>
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
                    <td key={c.key} className={c.negative?.(item) ? "negative" : ""}>{c.text!(item)}</td>
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
