"use client";

import { useEffect, useState } from "react";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from "recharts";
import { Skeleton } from "@/components/ui/skeleton";
import {
  fetchOverview,
  formatDelta,
  PERIOD_OPTIONS,
  type Period,
  type OverviewResponse,
  type OverviewKpi,
  type FunnelStage,
} from "@/lib/overview";

function formatDate(iso: string) {
  const d = new Date(iso);
  return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
}

const TONE_STYLE = {
  good: { background: "var(--good-soft)", color: "var(--good)" },
  bad: { background: "var(--critical-soft)", color: "var(--critical)" },
  neutral: { background: "var(--secondary)", color: "var(--muted-foreground)" },
} as const;

function KpiCard({ kpi, index }: { kpi: OverviewKpi; index: number }) {
  const delta = formatDelta(kpi.delta);
  return (
    <div
      className={
        kpi.hero
          ? "col-span-2 flex flex-col gap-2 rounded-2xl border bg-card p-5"
          : "flex flex-col gap-1.5 rounded-2xl border bg-card p-4"
      }
      style={{
        borderColor: kpi.tone === "warn" ? "var(--warn)" : "var(--border)",
        boxShadow:
          "0 16px 32px -18px rgba(0,0,0,.5)" +
          (kpi.tone === "warn" ? ", 0 0 0 1px rgba(242,184,75,.12)" : ""),
        animationDelay: `${index * 30}ms`,
      }}
    >
      <div className="text-[11.5px] text-text-faint">{kpi.label}</div>
      <div
        className={"font-mono tracking-tight " + (kpi.hero ? "text-3xl font-bold" : "text-2xl font-semibold")}
        style={{ color: kpi.tone === "warn" ? "var(--warn)" : undefined }}
      >
        {kpi.value}
      </div>
      <div className="flex items-center gap-2">
        <div className="text-[11.5px] text-muted-foreground">{kpi.note}</div>
        {delta && (
          <span
            className="rounded-full px-2 py-0.5 text-[10.5px] font-semibold"
            style={TONE_STYLE[delta.tone]}
          >
            {delta.text}
          </span>
        )}
      </div>
    </div>
  );
}

function FunnelBar({ stage }: { stage: FunnelStage }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between text-[12.5px]">
        <span className="text-muted-foreground">{stage.label}</span>
        <span className="font-mono font-semibold">{stage.value_text}</span>
      </div>
      <div className="h-2.5 w-full rounded-full bg-secondary">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${stage.width_pct}%`, backgroundColor: stage.color }}
        />
      </div>
    </div>
  );
}

export default function OverviewPage() {
  const [data, setData] = useState<OverviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [period, setPeriod] = useState<Period>("30d");
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");

  useEffect(() => {
    fetchOverview(period, period === "custom" ? customStart : undefined, period === "custom" ? customEnd : undefined)
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
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-7">
          {Array.from({ length: 7 }).map((_, i) => (
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
          <h1 className="text-2xl font-bold tracking-tight">Обзор</h1>
          <p className="mt-1 text-[13px] text-muted-foreground">
            {data.period_label}
            {data.start && `: ${formatDate(data.start)} – ${formatDate(data.end)}`}
            {data.has_prev && data.prev_start && data.prev_end && (
              <span className="ml-2 text-text-faint">
                (сравн. с {formatDate(data.prev_start)}–{formatDate(data.prev_end)})
              </span>
            )}
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

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-7">
        {data.kpis.map((kpi, i) => (
          <KpiCard key={kpi.key} kpi={kpi} index={i} />
        ))}
      </div>

      <div className="grid gap-5 md:grid-cols-2">
        <div className="rounded-2xl border bg-card p-5 shadow-[0_26px_50px_-22px_rgba(0,0,0,.55)]">
          <h2 className="text-[14.5px] font-semibold">Воронка заказов</h2>
          <p className="mt-0.5 mb-4 text-[12px] text-muted-foreground">
            Сколько заказано, выкуплено и возвращено за период.
          </p>
          <div className="flex flex-col gap-3">
            {data.funnel.map((stage) => (
              <FunnelBar key={stage.label} stage={stage} />
            ))}
          </div>
        </div>

        <div className="rounded-2xl border bg-card p-5 shadow-[0_26px_50px_-22px_rgba(0,0,0,.55)]">
          <h2 className="text-[14.5px] font-semibold">Динамика</h2>
          <p className="mt-0.5 mb-4 text-[12px] text-muted-foreground">
            Сумма заказов, выручка и реклама по {data.chart.x_key === "hour" ? "часам" : "дням"}.
          </p>
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data.chart.rows} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                <XAxis
                  dataKey="x"
                  tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
                  axisLine={{ stroke: "var(--border)" }}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
                  axisLine={{ stroke: "var(--border)" }}
                  tickLine={false}
                  tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "var(--card)",
                    border: "1px solid var(--border)",
                    borderRadius: "8px",
                  }}
                  labelStyle={{ color: "var(--foreground)" }}
                />
                <Legend />
                {data.chart.series.map((s) => (
                  <Line
                    key={s.key}
                    type="monotone"
                    dataKey={s.key}
                    name={s.name}
                    stroke={s.color}
                    strokeWidth={2}
                    dot={false}
                    strokeDasharray={s.dashed ? "4 4" : undefined}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      <div className="grid gap-5 lg:grid-cols-[1.2fr_1.8fr]">
        <div className="rounded-2xl border bg-card p-5 shadow-[0_26px_50px_-22px_rgba(0,0,0,.55)]">
          <h2 className="text-[14.5px] font-semibold">Требует внимания</h2>
          <p className="mt-0.5 mb-4 text-[12px] text-muted-foreground">
            Товары с убытком или аномалией за период.
          </p>
          {data.problem_products.length === 0 ? (
            <div className="text-sm text-good">Проблемных товаров нет.</div>
          ) : (
            <div className="flex flex-col gap-2.5">
              {data.problem_products.map((p, i) => (
                <div
                  key={i}
                  className="flex flex-col gap-1.5 rounded-xl border p-3"
                  style={{
                    borderColor: p.severity === "critical" ? "var(--critical)" : "var(--warn)",
                    background: p.severity === "critical" ? "var(--critical-soft)" : "var(--warn-soft)",
                  }}
                >
                  <div className="flex items-center justify-between">
                    <span
                      className="text-[12px] font-semibold"
                      style={{ color: p.severity === "critical" ? "var(--critical)" : "var(--warn)" }}
                    >
                      {p.status}
                    </span>
                    <span className="font-mono text-[12px]">{p.profit}</span>
                  </div>
                  <div className="text-[12.5px] text-muted-foreground">{p.name}</div>
                  <div className="text-[11.5px] text-text-faint">{p.reason}</div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="rounded-2xl border bg-card p-5 shadow-[0_26px_50px_-22px_rgba(0,0,0,.55)]">
          <h2 className="text-[14.5px] font-semibold">Товары</h2>
          <p className="mt-0.5 mb-4 text-[12px] text-muted-foreground">
            Заказы, продажи, выручка, ДРР, остаток и запас по артикулам.
          </p>
          {data.products.length === 0 ? (
            <div className="text-sm text-muted-foreground">Нет товаров за период.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="border-b text-left">
                    <th className="px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-text-faint">
                      Артикул WB
                    </th>
                    <th className="px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-text-faint">
                      Артикул продавца
                    </th>
                    <th className="px-3 py-2 text-right text-[11px] font-semibold uppercase tracking-wide text-text-faint">
                      Заказы
                    </th>
                    <th className="px-3 py-2 text-right text-[11px] font-semibold uppercase tracking-wide text-text-faint">
                      Продажи
                    </th>
                    <th className="px-3 py-2 text-right text-[11px] font-semibold uppercase tracking-wide text-text-faint">
                      Выручка
                    </th>
                    <th className="px-3 py-2 text-right text-[11px] font-semibold uppercase tracking-wide text-text-faint">
                      ДРР
                    </th>
                    <th className="px-3 py-2 text-right text-[11px] font-semibold uppercase tracking-wide text-text-faint">
                      Остаток
                    </th>
                    <th className="px-3 py-2 text-right text-[11px] font-semibold uppercase tracking-wide text-text-faint">
                      Запас
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.products.slice(0, 10).map((p, i) => (
                    <tr key={i} className="border-t hover:bg-secondary/60">
                      <td className="px-3 py-2.5 font-mono text-xs text-text-faint">{p.nm_id}</td>
                      <td className="px-3 py-2.5">{p.article || "—"}</td>
                      <td className="px-3 py-2.5 text-right font-mono">{p.orders}</td>
                      <td className="px-3 py-2.5 text-right font-mono">{p.sales}</td>
                      <td className="px-3 py-2.5 text-right font-mono">{p.revenue.toLocaleString("ru-RU")} ₽</td>
                      <td className="px-3 py-2.5 text-right font-mono">{p.drr.toFixed(1)}%</td>
                      <td className="px-3 py-2.5 text-right font-mono">{p.stock}</td>
                      <td className="px-3 py-2.5 text-right font-mono">{p.days_of_stock.toFixed(1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {data.products.length > 10 && (
                <div className="mt-2 text-[12px] text-text-faint">
                  Показано 10 из {data.products.length} товаров
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
