import { useMemo, useState } from "react";
import { LayoutGrid, List, Table2 } from "lucide-react";
import type { ProductEconomicsRow } from "./data-source";
import "./ProductEconomicsList.css";

export type ProductViewMode = "tiles" | "table" | "list";

const MODES: Array<{ id: ProductViewMode; label: string; Icon: typeof List }> = [
  { id: "tiles", label: "Плитка", Icon: LayoutGrid },
  { id: "table", label: "Таблица", Icon: Table2 },
  { id: "list", label: "Список", Icon: List },
];
const STORAGE_KEY = "mh.products.viewMode";

type SortKey = "product" | "revenue" | "margin" | "marginPct" | "drr" | "stock";
const COLUMNS: Array<{ key: SortKey; label: string }> = [
  { key: "product", label: "Товар" },
  { key: "revenue", label: "Выручка" },
  { key: "margin", label: "Расчётная прибыль" },
  { key: "marginPct", label: "Маржа" },
  { key: "drr", label: "ДРР" },
  { key: "stock", label: "Запас" },
];

// localStorage can be blocked or throw (private window, cleared site data): the view must still work.
function readMode(): ProductViewMode {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    return MODES.some((mode) => mode.id === saved) ? (saved as ProductViewMode) : "tiles";
  } catch {
    return "tiles";
  }
}

const rub = (value: number) => `${Math.round(value).toLocaleString("ru-RU")} ₽`;
const marginOf = (item: ProductEconomicsRow) =>
  Number.isFinite(item.marginPct ?? NaN) ? (item.marginPct ?? 0) : item.revenue > 0 ? (item.margin / item.revenue) * 100 : 0;
const stockOf = (item: ProductEconomicsRow) =>
  `${item.stock.toLocaleString("ru-RU")} шт.${item.daysOfStock !== null ? ` · ${item.daysOfStock.toFixed(1)} дн.` : " · прогноз недоступен"}`;

const sortValue = (item: ProductEconomicsRow, key: SortKey): string | number =>
  key === "product" ? item.product : key === "marginPct" ? marginOf(item) : item[key];

export function ProductEconomicsList({ items, onOpen }: { items: ProductEconomicsRow[]; onOpen: (item: ProductEconomicsRow) => void }) {
  const [mode, setMode] = useState<ProductViewMode>(readMode);
  // Sorting belongs to the table only; tiles and list keep the order the API returned.
  const [sort, setSort] = useState<{ key: SortKey; dir: "asc" | "desc" } | null>(null);
  const sorted = useMemo(() => {
    if (!sort) return items;
    const sign = sort.dir === "asc" ? 1 : -1;
    return [...items].sort((a, b) => {
      const x = sortValue(a, sort.key);
      const y = sortValue(b, sort.key);
      return sign * (typeof x === "string" ? x.localeCompare(String(y), "ru") : x - Number(y));
    });
  }, [items, sort]);
  // First click: text A-Z, numbers high-to-low (what people look for); second click reverses.
  const toggleSort = (key: SortKey) =>
    setSort((current) => current?.key === key
      ? { key, dir: current.dir === "asc" ? "desc" : "asc" }
      : { key, dir: key === "product" ? "asc" : "desc" });
  const choose = (next: ProductViewMode) => {
    setMode(next);
    try { window.localStorage.setItem(STORAGE_KEY, next); } catch { /* per-viewer convenience only */ }
  };

  return (
    <>
      <div className="product-view-switch" role="group" aria-label="Вид списка товаров">
        {MODES.map(({ id, label, Icon }) => (
          <button key={id} type="button" className={mode === id ? "active" : ""} aria-pressed={mode === id} title={label} onClick={() => choose(id)}>
            <Icon size={16} aria-hidden="true" />
            <span>{label}</span>
          </button>
        ))}
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
                {COLUMNS.map(({ key, label }) => (
                  <th key={key} aria-sort={sort?.key === key ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
                    <button type="button" className="product-sort" onClick={() => toggleSort(key)}>
                      {label}<span aria-hidden="true">{sort?.key === key ? (sort.dir === "asc" ? " ▲" : " ▼") : ""}</span>
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((item) => (
                <tr key={item.nmId} onClick={() => onOpen(item)}>
                  <td>
                    <button type="button" className="product-row-open" onClick={(event) => { event.stopPropagation(); onOpen(item); }}>
                      <strong>{item.product}</strong><small>{item.article || item.nmId}</small>
                    </button>
                  </td>
                  <td>{rub(item.revenue)}</td>
                  <td className={item.margin < 0 ? "negative" : ""}>{rub(item.margin)}</td>
                  <td className={marginOf(item) < 0 ? "negative" : ""}>{marginOf(item).toFixed(1)}%</td>
                  <td>{item.drr.toFixed(1)}%</td>
                  <td>{stockOf(item)}</td>
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
