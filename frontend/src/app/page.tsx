"use client";

import { useEffect, useState } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { AlertIcon } from "@/components/icons";
import { fetchToday, priorityTone, type TodayResponse } from "@/lib/today";

function formatDate(iso: string) {
  const d = new Date(iso);
  return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
}

const TONE_STYLE = {
  critical: { background: "var(--critical-soft)", color: "var(--critical)" },
  warn: { background: "var(--warn-soft)", color: "var(--warn)" },
  neutral: { background: "var(--secondary)", color: "var(--muted-foreground)" },
} as const;

function PriorityPill({ priority }: { priority: string }) {
  return (
    <span
      className="rounded-full px-2.5 py-1 text-[11.5px] font-semibold"
      style={TONE_STYLE[priorityTone(priority)]}
    >
      {priority}
    </span>
  );
}

const KPI_TABS = [
  { key: "signals", label: "Сделать сейчас" },
  { key: "shift", label: "Смена" },
  { key: "dispatch", label: "Отгрузки" },
  { key: "material", label: "Сырьё" },
  { key: "procurement", label: "Закупить" },
  { key: "overdue", label: "Просрочено" },
] as const;

export default function TodayPage() {
  const [data, setData] = useState<TodayResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<(typeof KPI_TABS)[number]["key"]>("signals");

  useEffect(() => {
    fetchToday()
      .then(setData)
      .catch((e: Error) => setError(e.message));
  }, []);

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
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-6">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-24 rounded-2xl" />
          ))}
        </div>
        <Skeleton className="h-64 rounded-2xl" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5.5 px-4 py-5 sm:px-6 md:px-10 md:py-7.5 animate-content-in">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Сегодня</h1>
        <p className="mt-1 text-[13px] text-muted-foreground">
          Оперативная сводка на {formatDate(data.today)} (московское время)
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-6">
        {data.kpis.map((kpi, i) => (
          <div
            key={kpi.key}
            className="flex flex-col gap-1.5 rounded-2xl border bg-card p-4 animate-content-in"
            style={{
              borderColor: kpi.tone === "warn" ? "var(--warn)" : "var(--border)",
              boxShadow:
                "0 16px 32px -18px rgba(0,0,0,.5)" +
                (kpi.tone === "warn" ? ", 0 0 0 1px rgba(242,184,75,.12)" : ""),
              animationDelay: `${i * 30}ms`,
            }}
          >
            <div className="text-[11.5px] text-text-faint">{kpi.label}</div>
            <div
              className="font-mono text-2xl font-semibold tracking-tight"
              style={{ color: kpi.tone === "warn" ? "var(--warn)" : undefined }}
            >
              {kpi.value}
            </div>
            <div className="text-[11.5px] text-muted-foreground">{kpi.note}</div>
          </div>
        ))}
      </div>

      {data.alert_count > 0 && (
        <div
          className="flex flex-col items-start gap-3 rounded-2xl border px-4.5 py-3.25 md:flex-row md:items-center md:justify-between md:gap-4"
          style={{ background: "var(--warn-soft)", borderColor: "rgba(242,184,75,.32)" }}
        >
          <div className="flex items-center gap-2.75">
            <AlertIcon />
            <div className="text-[13.5px]">
              Найден <b>{data.alert_count}</b> сигнал{data.alert_count === 1 ? "" : data.alert_count < 5 ? "а" : "ов"}. Начните со вкладки «Сделать сейчас».
            </div>
          </div>
          <div
            className="w-full shrink-0 cursor-pointer rounded-full border px-4 py-1.75 text-center text-[12.5px] font-semibold transition-transform active:scale-[0.97] md:w-auto"
            style={{ borderColor: "rgba(242,184,75,.5)", color: "var(--warn)" }}
          >
            Открыть
          </div>
        </div>
      )}

      <div className="overflow-hidden rounded-2xl border bg-card shadow-[0_26px_50px_-22px_rgba(0,0,0,.55)]">
        <div className="flex flex-wrap items-center gap-1.5 border-b px-5 py-3.25">
          {KPI_TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={
                "rounded-full px-3.5 py-1.75 text-[12.5px] font-semibold transition-colors " +
                (tab === t.key ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:text-foreground")
              }
            >
              {t.label}
            </button>
          ))}
        </div>

        <div key={tab} className="animate-fade-in">
        {tab === "signals" && (
          data.signals.length === 0 ? (
            <div className="p-6 text-sm text-good">Оперативных действий нет.</div>
          ) : (
            <>
              {/* Desktop: dense table (Linear-style data density) */}
              <table className="hidden w-full text-[13px] md:table">
                <thead>
                  <tr className="bg-secondary text-left">
                    <th className="w-28 px-5 py-2.25 text-[11px] font-semibold uppercase tracking-wide text-text-faint">Приоритет</th>
                    <th className="w-36 px-3.5 py-2.25 text-[11px] font-semibold uppercase tracking-wide text-text-faint">Тип</th>
                    <th className="w-32 px-3.5 py-2.25 text-[11px] font-semibold uppercase tracking-wide text-text-faint">Артикул</th>
                    <th className="px-3.5 py-2.25 text-[11px] font-semibold uppercase tracking-wide text-text-faint">Сигнал</th>
                    <th className="w-28 px-5 py-2.25 text-right text-[11px] font-semibold uppercase tracking-wide text-text-faint">Действие</th>
                  </tr>
                </thead>
                <tbody>
                  {data.signals.map((s, i) => (
                    <tr key={i} className="border-t hover:bg-secondary/60">
                      <td className="px-5 py-3">
                        <PriorityPill priority={s.priority} />
                      </td>
                      <td className="px-3.5 py-3">{s.type}</td>
                      <td className="px-3.5 py-3 font-mono text-xs text-text-faint">{s.article || "—"}</td>
                      <td className="px-3.5 py-3 text-muted-foreground">{s.signal}</td>
                      <td className="px-5 py-3 text-right">
                        <span
                          className="inline-block cursor-pointer rounded-full border px-3.5 py-1.5 text-xs font-semibold transition-transform active:scale-[0.97]"
                          style={{ borderColor: "var(--border)" }}
                        >
                          Открыть
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {/* Mobile: table -> card list, not a squeezed table */}
              <div className="flex flex-col gap-2.5 p-3.5 md:hidden">
                {data.signals.map((s, i) => (
                  <div key={i} className="flex flex-col gap-2 rounded-[14px] border bg-card p-3.5" style={{ borderColor: "var(--border)" }}>
                    <div className="flex items-center justify-between">
                      <PriorityPill priority={s.priority} />
                      <span className="text-xs text-muted-foreground">{s.type}</span>
                    </div>
                    <div className="text-[12.5px] leading-relaxed text-muted-foreground">{s.signal}</div>
                    <div className="mt-0.5 flex items-center justify-between">
                      <span className="font-mono text-[11.5px] text-text-faint">{s.article || "—"}</span>
                      <span
                        className="cursor-pointer rounded-full border px-4.5 py-2.25 text-xs font-semibold transition-transform active:scale-[0.97]"
                        style={{ borderColor: "var(--border-strong,var(--border))" }}
                      >
                        Открыть
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </>
          )
        )}

        {tab === "shift" && (
          data.tabs.shift.tasks.length === 0 ? (
            <div className="p-6 text-sm text-muted-foreground">Ближайшее сменное задание не сформировано.</div>
          ) : (
            <div className="p-5 text-sm text-muted-foreground">
              Показывается смена {data.tabs.shift.date ? formatDate(data.tabs.shift.date) : "—"}. Сырьём обеспечено{" "}
              {data.tabs.shift.material_covered} из {data.tabs.shift.planned} комплектов.
            </div>
          )
        )}

        {tab === "dispatch" && (
          data.tabs.dispatch.unposted.length === 0 && data.tabs.dispatch.pending_receipt.length === 0 ? (
            <div className="p-6 text-sm text-good">Непроведённых отгрузок и приёмок нет.</div>
          ) : (
            <div className="flex flex-col gap-2 p-5 text-sm">
              {data.tabs.dispatch.unposted.length > 0 && (
                <div className="text-critical">Непроведённых отгрузок: {data.tabs.dispatch.unposted.length}</div>
              )}
              {data.tabs.dispatch.pending_receipt.length > 0 && (
                <div className="text-warn">Незакрытых приёмок: {data.tabs.dispatch.pending_receipt.length}</div>
              )}
            </div>
          )
        )}

        {tab === "material" && (
          data.tabs.material.rows.length === 0 ? (
            <div className="p-6 text-sm text-muted-foreground">Нет сменного задания или норм сырья для ближайшей смены.</div>
          ) : (
            <div className="p-5 text-sm">
              {data.tabs.material.shortages ? (
                <span className="text-critical">Сырья на ближайшую смену не хватает. Календарь ограничит выполнимый объём.</span>
              ) : data.tabs.material.unknown ? (
                <span className="text-warn">Для части материалов остаток не подтверждён.</span>
              ) : (
                <span className="text-good">Сырья на ближайшую смену достаточно.</span>
              )}
            </div>
          )
        )}

        {tab === "procurement" && (
          <div className="flex flex-col gap-1 p-5 text-sm text-muted-foreground">
            <div>Материалов к закупке: {data.tabs.procurement.material_to_buy.length}</div>
            <div>Товаров к закупке: {data.tabs.procurement.products_to_buy.length}</div>
            <div>Активных заявок: {data.tabs.procurement.active_orders.length}</div>
          </div>
        )}

        {tab === "overdue" && (
          data.tabs.overdue.length === 0 ? (
            <div className="p-6 text-sm text-good">Просроченных заданий нет.</div>
          ) : (
            <div className="p-5 text-sm text-critical">Просроченных заданий: {data.tabs.overdue.length}</div>
          )
        )}
        </div>
      </div>
    </div>
  );
}
