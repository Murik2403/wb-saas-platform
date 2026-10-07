import { useEffect, useState } from "react";
import { buildCsv } from "./csv";
import { getProductsHeatmap, type DashboardPeriod, type HeatmapMetric, type ProductsHeatmap as Heatmap } from "./data-source";
import "./ProductsHeatmap.css";
import { saveBlob } from "./download";

export const HEATMAP_METRICS: Array<{ id: HeatmapMetric; label: string }> = [
  { id: "orders", label: "Заказы, шт" },
  { id: "orders_rub", label: "Заказы, ₽" },
  { id: "sales", label: "Выкупы, шт" },
  { id: "revenue", label: "Выручка, ₽" },
  { id: "ad_spend", label: "Расход на рекламу, ₽" },
  { id: "ad_orders", label: "Заказы с рекламы, шт" },
];

const MAX_ALPHA = 0.5; // keeps #f3f6fb text >= 4.5:1 on the strongest cell over the dark surface

/** Background strength 0..MAX_ALPHA; zero stays quiet (0), the scale is global for the whole matrix. */
export function cellAlpha(value: number, maxAbs: number): number {
  if (!Number.isFinite(value) || value === 0 || !(maxAbs > 0)) return 0;
  return 0.1 + (MAX_ALPHA - 0.1) * Math.min(1, Math.abs(value) / maxAbs);
}

export function formatCell(value: number | null | undefined, unit: "pcs" | "rub"): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  const rounded = unit === "rub" ? Math.round(value) : Math.round(value * 100) / 100;
  const text = Math.abs(rounded).toLocaleString("ru-RU");
  return rounded < 0 ? `−${text}` : text;
}

export function dateLabel(iso: string): string {
  const [, month, day] = iso.split("-");
  return day && month ? `${day}.${month}` : iso;
}

export function heatmapCsv(data: Heatmap, rows = data.rows): string {
  const headers = ["Артикул WB", "Артикул продавца", "Товар", ...data.dates.map(dateLabel), "Итого"];
  const lines: Array<Array<string | number | null>> = rows.map((r) => [r.nm_id, r.vendor_code, r.name, ...r.values, r.total]);
  if (data.other) lines.push(["", "", `Прочие (${data.other.count})`, ...data.other.values, data.other.total]);
  lines.push(["", "", "Итого", ...data.column_totals, data.grand_total]);
  return buildCsv(headers, lines);
}

function downloadCsv(content: string, metric: string) {
  void saveBlob(new Blob([content], { type: "text/csv;charset=utf-8" }), `heatmap-${metric}-${new Date().toISOString().slice(0, 10)}.csv`);
}

type State = { data: Heatmap | null; loading: boolean; error: string | null };

function useHeatmap(open: boolean, metric: HeatmapMetric, period: DashboardPeriod): State {
  const [state, setState] = useState<State>({ data: null, loading: false, error: null });
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    let active = true;
    setState({ data: null, loading: true, error: null });
    getProductsHeatmap(metric, period, controller.signal).then(
      (data) => { if (active) setState({ data, loading: false, error: null }); },
      (error: unknown) => { if (active) setState({ data: null, loading: false, error: error instanceof Error ? error.message : "ошибка запроса" }); },
    );
    return () => { active = false; controller.abort(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, metric, period.id, period.start, period.end]);
  return state;
}

type Named = { name: string; vendor_code: string; nm_id: number };
const label = (r: Named) => r.name || r.vendor_code || String(r.nm_id);

export function ProductsHeatmap({ period }: { period: DashboardPeriod }) {
  const [open, setOpen] = useState(false);
  const [metric, setMetric] = useState<HeatmapMetric>("orders");
  const [sortBy, setSortBy] = useState<"total" | "name">("total");
  const { data, loading, error } = useHeatmap(open, metric, period);
  const rows = data ? (sortBy === "name" ? [...data.rows].sort((a, b) => label(a).localeCompare(label(b), "ru")) : data.rows) : [];
  const maxAbs = data ? Math.max(Math.abs(data.min ?? 0), Math.abs(data.max ?? 0)) : 0;

  return (
    <section className="work-section heatmap" aria-label="Тепловая карта товаров по дням">
      <button className="heatmap-toggle" type="button" aria-expanded={open} aria-controls="heatmap-body" onClick={() => setOpen((v) => !v)}>
        <span aria-hidden="true">{open ? "▾" : "▸"}</span> Тепловая карта: товар × день
      </button>
      {open ? (
        <div id="heatmap-body">
          <div className="heatmap-controls">
            <label>Показатель <select value={metric} onChange={(e) => setMetric(e.target.value as HeatmapMetric)}>{HEATMAP_METRICS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}</select></label>
            <label>Сортировка <select value={sortBy} onChange={(e) => setSortBy(e.target.value as "total" | "name")}><option value="total">по сумме</option><option value="name">по названию</option></select></label>
            {data && data.rows.length ? <button className="secondary-button" type="button" onClick={() => downloadCsv(heatmapCsv(data, rows), data.metric)}>Скачать CSV</button> : null}
            <span className="heatmap-legend">
              меньше <i className="heatmap-ramp" aria-hidden="true" /> больше
            </span>
          </div>
          {loading ? <p className="heatmap-note" role="status">Загружаем матрицу…</p> : null}
          {error ? <p className="heatmap-note heatmap-error" role="alert">Не удалось загрузить тепловую карту: {error}</p> : null}
          {data && !data.rows.length && !loading ? <p className="heatmap-note">За выбранный период по этому показателю данных нет.</p> : null}
          {data && data.rows.length ? (
            <>
              <div className="heatmap-scroll" tabIndex={0} role="region" aria-label={`Матрица: ${data.label}`}>
                <table className="heatmap-table">
                  <caption className="sr-only">{data.label} по товарам и дням</caption>
                  <thead>
                    <tr>
                      <th scope="col">Товар</th>
                      {data.dates.map((d) => <th scope="col" key={d} title={data.bucket_days > 1 ? `${d}, ${data.bucket_days} дн.` : d}>{dateLabel(d)}</th>)}
                      <th scope="col">Итого</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.nm_id}>
                        <th scope="row"><span>{label(r)}</span><small>{r.vendor_code && r.name ? `${r.vendor_code} · ` : ""}{r.nm_id}</small></th>
                        {r.values.map((v, i) => {
                          const a = cellAlpha(v, maxAbs);
                          const rgb = v < 0 ? "255,107,122" : "46,230,183";
                          return <td key={i} className={v === 0 ? "heatmap-zero" : undefined} style={a ? { background: `rgba(${rgb},${a.toFixed(3)})` } : undefined} title={`${label(r)}, ${data.dates[i]}: ${formatCell(v, data.unit)}`}>{formatCell(v, data.unit)}</td>;
                        })}
                        <td className="heatmap-total">{formatCell(r.total, data.unit)}</td>
                      </tr>
                    ))}
                    {data.other ? (
                      <tr>
                        <th scope="row"><span>Прочие товары ({data.other.count})</span></th>
                        {data.other.values.map((v, i) => <td key={i} className={v === 0 ? "heatmap-zero" : undefined}>{formatCell(v, data.unit)}</td>)}
                        <td className="heatmap-total">{formatCell(data.other.total, data.unit)}</td>
                      </tr>
                    ) : null}
                  </tbody>
                  <tfoot>
                    <tr>
                      <th scope="row">Итого</th>
                      {data.column_totals.map((v, i) => <td key={i}>{formatCell(v, data.unit)}</td>)}
                      <td className="heatmap-total">{formatCell(data.grand_total, data.unit)}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
              <p className="heatmap-note">
                {data.bucket_days > 1 ? `Период длинный: в колонке сумма за ${data.bucket_days} дн. ` : ""}
                Показаны первые {data.rows.length} товаров по сумме{data.other ? ", остальные собраны в строку «Прочие»" : ""}. Возвраты вычитаются.
                {data.campaign_level_total ? ` Реклама на уровне кампании без разбивки по товарам (${formatCell(data.campaign_level_total, data.unit)}) в матрицу не входит.` : ""}
              </p>
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
