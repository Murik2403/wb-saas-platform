"use client";

import { useEffect, useState } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchAds, PERIOD_OPTIONS, type Period, type AdsResponse, type AdsKpi } from "@/lib/ads";

function KpiCard({ kpi, index }: { kpi: AdsKpi; index: number }) {
  return (
    <div
      className="flex flex-col gap-1.5 rounded-2xl border bg-card p-4 animate-content-in"
      style={{
        borderColor: "var(--border)",
        animationDelay: `${index * 30}ms`,
      }}
    >
      <div className="text-[11.5px] text-text-faint">{kpi.label}</div>
      <div className="font-mono text-2xl font-semibold tracking-tight text-foreground">
        {kpi.value}
      </div>
      <div className="text-[11.5px] text-muted-foreground">{kpi.note}</div>
    </div>
  );
}

export default function AdsPage() {
  const [data, setData] = useState<AdsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [period, setPeriod] = useState<Period>("30d");
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");

  useEffect(() => {
    fetchAds(period, period === "custom" ? customStart : undefined, period === "custom" ? customEnd : undefined)
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
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24 rounded-2xl" />
          ))}
        </div>
        <Skeleton className="h-64 rounded-2xl" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5.5 px-4 py-5 sm:px-6 md:px-10 md:py-7.5 animate-content-in">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Продвижение</h1>
          <p className="mt-1 text-[13px] text-muted-foreground">
            Сколько заказов принесла реклама, сколько на неё потрачено, доля рекламных расходов (ДРР) и средний CTR за выбранный период.
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
        <div className="flex items-center gap-3 rounded-xl border bg-card p-3 animate-fade-in">
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

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {data.kpis.map((kpi, i) => (
          <KpiCard key={kpi.key} kpi={kpi} index={i} />
        ))}
      </div>

      <div className="text-[12px] text-text-faint">
        {data.kpi_note}
      </div>

      <div className="rounded-2xl border bg-card p-5 shadow-[0_26px_50px_-22px_rgba(0,0,0,.55)]">
        <h2 className="text-[14.5px] font-semibold">Рекламные кампании</h2>
        <p className="mt-0.5 mb-4 text-[12px] text-muted-foreground">
          Расход, рекламная выручка, CTR, цена клика (CPC) и ДРР по каждой кампании за период.
        </p>

        {data.campaigns.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 py-10 text-center animate-fade-in">
            <div className="text-[14.5px] font-semibold text-foreground">{data.empty_state.title}</div>
            <div className="max-w-md text-[13px] leading-relaxed text-muted-foreground">
              {data.empty_state.note}
            </div>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b text-left">
                  <th className="px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-text-faint">Кампания</th>
                  <th className="px-3 py-2 text-right text-[11px] font-semibold uppercase tracking-wide text-text-faint">Расход</th>
                  <th className="px-3 py-2 text-right text-[11px] font-semibold uppercase tracking-wide text-text-faint">Рекламная выручка</th>
                  <th className="px-3 py-2 text-right text-[11px] font-semibold uppercase tracking-wide text-text-faint">CTR, %</th>
                  <th className="px-3 py-2 text-right text-[11px] font-semibold uppercase tracking-wide text-text-faint">CPC</th>
                  <th className="px-3 py-2 text-right text-[11px] font-semibold uppercase tracking-wide text-text-faint">ДРР, %</th>
                </tr>
              </thead>
              <tbody>
                {data.campaigns.map((c, i) => (
                  <tr key={i} className="border-t hover:bg-secondary/60">
                    <td className="px-3 py-2.5 text-foreground">{c.campaign || "—"}</td>
                    <td className="px-3 py-2.5 text-right font-mono text-foreground">{c.spend.toLocaleString("ru-RU", { maximumFractionDigits: 0 })} ₽</td>
                    <td className="px-3 py-2.5 text-right font-mono text-foreground">{c.ad_revenue.toLocaleString("ru-RU", { maximumFractionDigits: 0 })} ₽</td>
                    <td className="px-3 py-2.5 text-right font-mono text-foreground">{c.ctr.toFixed(2)}%</td>
                    <td className="px-3 py-2.5 text-right font-mono text-foreground">{c.cpc.toFixed(2)} ₽</td>
                    <td className="px-3 py-2.5 text-right font-mono text-foreground">{c.drr.toFixed(1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
