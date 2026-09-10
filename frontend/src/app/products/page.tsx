"use client";

import { useEffect, useState } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { PERIOD_OPTIONS } from "@/lib/overview";
import {
  fetchProducts,
  money0,
  pct1,
  dec1,
  dec2,
  plain,
  type Period,
  type ProductRow,
  type ProductsResponse,
} from "@/lib/products";

function formatDate(iso: string) {
  const d = new Date(iso);
  return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
}

// Same 15 columns, in the same order, as data.products -- pages/products.py
// hands the whole DataFrame to st.dataframe. `format` mirrors that page's
// column_config number formats one-for-one; the columns it leaves unformatted
// fall back to `plain`.
type Column = {
  key: keyof ProductRow;
  label: string;
  numeric: boolean;
  format: (row: ProductRow) => string;
};

const COLUMNS: Column[] = [
  { key: "nm_id", label: "Артикул WB", numeric: false, format: (r) => String(r.nm_id) },
  { key: "article", label: "Артикул продавца", numeric: false, format: (r) => r.article || "—" },
  { key: "product", label: "Товар", numeric: false, format: (r) => r.product || "—" },
  { key: "brand", label: "Бренд", numeric: false, format: (r) => r.brand || "—" },
  { key: "orders", label: "Заказы", numeric: true, format: (r) => plain(r.orders) },
  { key: "sales", label: "Продажи", numeric: true, format: (r) => plain(r.sales) },
  { key: "revenue", label: "Выручка", numeric: true, format: (r) => money0(r.revenue) },
  { key: "buyout", label: "Выкуп, %", numeric: true, format: (r) => pct1(r.buyout) },
  { key: "ad_spend", label: "Реклама", numeric: true, format: (r) => money0(r.ad_spend) },
  { key: "drr", label: "ДРР, %", numeric: true, format: (r) => pct1(r.drr) },
  { key: "stock", label: "Остаток", numeric: true, format: (r) => plain(r.stock) },
  { key: "daily_sales", label: "Продаж/день", numeric: true, format: (r) => dec2(r.daily_sales) },
  { key: "days_of_stock", label: "Запас, дней", numeric: true, format: (r) => dec1(r.days_of_stock) },
  { key: "unit_cost", label: "Себестоимость ед.", numeric: true, format: (r) => money0(r.unit_cost) },
  { key: "margin", label: "Маржа до комиссий WB", numeric: true, format: (r) => money0(r.margin) },
];

// The mobile card repeats the identifying columns in its header, so its
// metric grid carries the numeric ones only.
const METRIC_COLUMNS = COLUMNS.filter((c) => c.numeric);

function ProductCard({ row }: { row: ProductRow }) {
  return (
    <div className="flex flex-col gap-2.5 rounded-[14px] border bg-card p-3.5" style={{ borderColor: "var(--border)" }}>
      <div className="flex flex-col gap-0.5">
        <div className="text-[13px] font-semibold leading-snug">{row.product || "—"}</div>
        <div className="flex items-center gap-2 text-[11.5px] text-text-faint">
          <span className="font-mono">{row.article || "—"}</span>
          <span>·</span>
          <span className="font-mono">{row.nm_id}</span>
          {row.brand && (
            <>
              <span>·</span>
              <span>{row.brand}</span>
            </>
          )}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-2">
        {METRIC_COLUMNS.map((col) => (
          <div key={col.key} className="flex flex-col gap-0.5">
            <span className="text-[10.5px] uppercase tracking-wide text-text-faint">{col.label}</span>
            <span className="font-mono text-[12.5px]">{col.format(row)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function ProductsPage() {
  const [data, setData] = useState<ProductsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [period, setPeriod] = useState<Period>("30d");
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");

  useEffect(() => {
    fetchProducts(period, period === "custom" ? customStart : undefined, period === "custom" ? customEnd : undefined)
      .then(setData)
      .catch((e: Error) => setError(e.message));
  }, [period, customStart, customEnd]);

  if (error === "NO_DATA_YET") {
    return (
      <div className="p-10 text-sm text-muted-foreground">
        Данных ещё нет — подключите магазин Wildberries в Настройках.
      </div>
    );
  }
  if (error) {
    return <div className="p-10 text-sm text-critical">Не удалось загрузить данные: {error}</div>;
  }
  if (!data) {
    return (
      <div className="flex flex-col gap-5.5 px-4 py-5 sm:px-6 md:px-10 md:py-7.5">
        <div className="flex flex-col gap-2">
          <Skeleton className="h-6 w-32" />
          <Skeleton className="h-4 w-72" />
        </div>
        <Skeleton className="h-96 rounded-2xl" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5.5 px-4 py-5 sm:px-6 md:px-10 md:py-7.5 animate-content-in">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Товары</h1>
          <p className="mt-1 text-[13px] text-muted-foreground">
            {data.period_label}
            {data.start && `: ${formatDate(data.start)} – ${formatDate(data.end)}`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {PERIOD_OPTIONS.map((opt) => (
            <button
              key={opt.key}
              onClick={() => setPeriod(opt.key)}
              className={
                "rounded-full px-3.5 py-1.75 text-[12.5px] font-semibold transition-colors " +
                (period === opt.key
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:text-foreground")
              }
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {period === "custom" && (
        <div className="flex items-center gap-3 rounded-xl border bg-card p-3">
          <label className="flex items-center gap-2 text-[13px]">
            <span className="text-muted-foreground">С:</span>
            <input
              type="date"
              value={customStart}
              onChange={(e) => setCustomStart(e.target.value)}
              className="rounded-lg border bg-background px-2.5 py-1.5 text-[13px]"
            />
          </label>
          <label className="flex items-center gap-2 text-[13px]">
            <span className="text-muted-foreground">По:</span>
            <input
              type="date"
              value={customEnd}
              onChange={(e) => setCustomEnd(e.target.value)}
              className="rounded-lg border bg-background px-2.5 py-1.5 text-[13px]"
            />
          </label>
        </div>
      )}

      <div className="overflow-hidden rounded-2xl border bg-card shadow-[0_26px_50px_-22px_rgba(0,0,0,.55)]">
        <div className="border-b px-5 py-4">
          <h2 className="text-[14.5px] font-semibold">Экономика по артикулам</h2>
          <p className="mt-0.5 text-[12px] text-muted-foreground">
            По каждому товару: выручка, процент выкупа, расходы на рекламу, ДРР, скорость продаж,
            запас в днях, себестоимость и маржа до вычета комиссий WB.
          </p>
        </div>

        {data.products.length === 0 ? (
          <div className="p-6 text-sm text-muted-foreground">
            Нет данных по товарам за выбранный период.
          </div>
        ) : (
          <>
            {/* Desktop: the full 15-column table, horizontally scrollable --
                same columns Streamlit's st.dataframe shows. */}
            <div className="hidden overflow-x-auto md:block">
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="bg-secondary text-left">
                    {COLUMNS.map((col) => (
                      <th
                        key={col.key}
                        className={
                          "whitespace-nowrap px-3.5 py-2.25 text-[11px] font-semibold uppercase tracking-wide text-text-faint " +
                          (col.numeric ? "text-right" : "")
                        }
                      >
                        {col.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.products.map((row) => (
                    <tr key={row.nm_id} className="border-t hover:bg-secondary/60">
                      {COLUMNS.map((col) => (
                        <td
                          key={col.key}
                          className={
                            "px-3.5 py-3 " +
                            (col.numeric
                              ? "whitespace-nowrap text-right font-mono"
                              : col.key === "product"
                                ? "max-w-64 truncate"
                                : col.key === "nm_id"
                                  ? "whitespace-nowrap font-mono text-xs text-text-faint"
                                  : "whitespace-nowrap")
                          }
                          title={col.key === "product" ? row.product : undefined}
                        >
                          {col.format(row)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Mobile: table -> card list, not a squeezed 15-column table */}
            <div className="flex flex-col gap-2.5 p-3.5 md:hidden">
              {data.products.map((row) => (
                <ProductCard key={row.nm_id} row={row} />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
