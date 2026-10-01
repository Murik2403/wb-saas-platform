import { useState } from "react";
import { buildCsv, type CsvCell } from "./csv";
import type { AdsMetrics, DashboardPeriod } from "./data-source";
import { useAdsAnalytics } from "./useProductsTrend";
import "./AdsAnalyticsPanel.css";

type Tone = "good" | "bad" | "neutral";
type Fmt = "money" | "int" | "pct";
type Def = { key: keyof AdsMetrics; label: string; fmt: Fmt; formula: string; up: Tone };

// up = tone of an increase; "neutral" metrics are volumes where more is not automatically better or worse.
export const ADS_DEFS: Def[] = [
  { key: "spend", label: "Расход", fmt: "money", formula: "Сумма расхода по итоговым строкам кампаний за день", up: "neutral" },
  { key: "views", label: "Показы", fmt: "int", formula: "Сумма показов", up: "neutral" },
  { key: "clicks", label: "Клики", fmt: "int", formula: "Сумма кликов", up: "neutral" },
  { key: "ctr", label: "CTR", fmt: "pct", formula: "CTR = клики / показы × 100", up: "good" },
  { key: "cpc", label: "CPC", fmt: "money", formula: "CPC = расход / клики", up: "bad" },
  { key: "cpm", label: "CPM", fmt: "money", formula: "CPM = расход / показы × 1000", up: "bad" },
  { key: "carts", label: "Корзины", fmt: "int", formula: "Сумма добавлений в корзину (atbs)", up: "good" },
  { key: "cartConversion", label: "Конв. в корзину", fmt: "pct", formula: "Конверсия в корзину = корзины / клики × 100", up: "good" },
  { key: "orders", label: "Заказы", fmt: "int", formula: "Сумма рекламных заказов (orders_count)", up: "good" },
  { key: "orderConversion", label: "Конв. в заказ", fmt: "pct", formula: "Конверсия в заказ = заказы / клики × 100", up: "good" },
  { key: "cpo", label: "CPO", fmt: "money", formula: "CPO = расход / рекламные заказы", up: "bad" },
  { key: "adRevenue", label: "Рекламная выручка", fmt: "money", formula: "Сумма заказов по атрибуции рекламного API", up: "good" },
  { key: "drr", label: "ДРР по рекламным заказам", fmt: "pct", formula: "ДРР по рекламным заказам = расход / рекламная выручка × 100. Не равен ДРР всего магазина (расход / выручка выкупов)", up: "bad" },
  { key: "canceled", label: "Отмены", fmt: "int", formula: "Сумма отменённых рекламных заказов", up: "bad" },
];

const NF: Record<Fmt, Intl.NumberFormat> = {
  money: new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
  int: new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }),
  pct: new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
};
export function fmtAds(value: number | null, fmt: Fmt): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return NF[fmt].format(value) + (fmt === "pct" ? " %" : fmt === "money" ? " ₽" : "");
}

/** Relative change in %, null when the previous value is missing or zero. */
export function adsDelta(cur: number | null, prev: number | null | undefined): number | null {
  if (cur === null || prev === null || prev === undefined || prev === 0) return null;
  return ((cur - prev) / Math.abs(prev)) * 100;
}

function Delta({ cur, prev, up }: { cur: number | null; prev: number | null | undefined; up: Tone }) {
  const d = adsDelta(cur, prev);
  if (d === null) return <span className="ads-delta none">нет данных за прошлый период</span>;
  const rounded = Math.round(d * 10) / 10;
  if (rounded === 0) return <span className="ads-delta flat">± 0,0%</span>;
  const tone = up === "neutral" ? "neutral" : (rounded > 0) === (up === "good") ? "good" : "bad";
  const text = `${rounded > 0 ? "+" : "−"}${Math.abs(rounded).toFixed(1).replace(".", ",")}%`;
  return (
    <span className={`ads-delta ${tone}`}>
      {rounded > 0 ? "▲" : "▼"} {text}
      {tone !== "neutral" ? <span className="ads-sr">{tone === "good" ? " (лучше)" : " (хуже)"}</span> : null}
    </span>
  );
}

type Row = AdsMetrics & { label: string };

function download(name: string, headers: string[], rows: CsvCell[][]) {
  const url = URL.createObjectURL(new Blob([buildCsv(headers, rows)], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function AdsTable({ title, firstCol, rows, file, sortable }: { title: string; firstCol: string; rows: Row[]; file: string; sortable?: boolean }) {
  const [desc, setDesc] = useState(false);
  const shown = sortable && desc ? [...rows].reverse() : rows;
  const exportCsv = () => download(file, [firstCol, ...ADS_DEFS.map((d) => d.label)], shown.map((r) => [r.label, ...ADS_DEFS.map((d) => r[d.key])]));
  return (
    <div className="ads-table-block">
      <div className="ads-table-head">
        <h3>{title}</h3>
        <button type="button" className="secondary-button" onClick={exportCsv} disabled={!shown.length}>Скачать CSV</button>
      </div>
      <div className="ads-table-scroll" tabIndex={0} role="region" aria-label={title}>
        <table className="ads-table">
          <thead>
            <tr>
              <th scope="col" aria-sort={sortable ? (desc ? "descending" : "ascending") : undefined}>
                {sortable ? <button type="button" className="ads-sort" onClick={() => setDesc(!desc)}>{firstCol} {desc ? "▼" : "▲"}</button> : firstCol}
              </th>
              {ADS_DEFS.map((d) => <th scope="col" key={d.key} title={d.formula}>{d.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.label}>
                <th scope="row">{r.label}</th>
                {ADS_DEFS.map((d) => <td key={d.key}>{fmtAds(r[d.key], d.fmt)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const ruDay = (iso: string) => iso.split("-").reverse().join(".");

export function AdsAnalyticsPanel({ period }: { period: DashboardPeriod }) {
  const { data, loading, error } = useAdsAnalytics(period);
  return (
    <section className="work-section ads-analytics" id="ads-analytics" aria-label="Аналитика рекламных кампаний">
      <div className="ads-analytics-head">
        <div><div className="eyebrow">Аналитика РК</div><h2>Эффективность рекламы</h2></div>
        {data?.hasData ? <p className="ads-period">Сравнение с {ruDay(data.previousStart)} — {ruDay(data.previousEnd)}</p> : null}
      </div>
      {loading ? <p className="plan-empty" role="status">Загружаем аналитику рекламы…</p> : null}
      {error ? <p className="plan-empty" role="alert">Не удалось загрузить аналитику рекламы: {error}</p> : null}
      {data && !data.hasData ? <p className="plan-empty">За выбранный период рекламной статистики нет</p> : null}
      {data?.hasData ? (
        <>
          <dl className="ads-kpi-strip">
            {ADS_DEFS.map((d) => (
              <div className="ads-kpi" key={d.key} title={d.formula}>
                <dt>{d.label}</dt>
                <dd>{fmtAds(data.totals[d.key], d.fmt)}</dd>
                <dd className="ads-prev">Было: {fmtAds(data.previousTotals?.[d.key] ?? null, d.fmt)}</dd>
                <dd><Delta cur={data.totals[d.key]} prev={data.previousTotals?.[d.key]} up={d.up} /></dd>
              </div>
            ))}
          </dl>
          <AdsTable title={data.bucketDays > 1 ? `По периодам (по ${data.bucketDays} дн.)` : "По дням"} firstCol={data.bucketDays > 1 ? "Начало периода" : "День"} sortable file="ads-by-day.csv"
            rows={data.byDay.map((r) => ({ ...r, label: ruDay(r.day) }))} />
          <AdsTable title="По кампаниям" firstCol="Кампания" file="ads-campaigns.csv" rows={data.campaigns.map((r) => ({ ...r, label: String(r.campaignId) }))} />
          <p className="ads-footnote">
            Суммы считаются по итоговой строке кампании за день, без дублей по товарам и приложениям, и совпадают с карточками выше.
            Разбивки по зонам показа (поиск, полки, каталог) в наших данных нет, поэтому здесь её нет. ДРР здесь считается только по рекламным заказам
            и не равен ДРР магазина. Наведите на название метрики, чтобы увидеть формулу.
          </p>
        </>
      ) : null}
    </section>
  );
}
