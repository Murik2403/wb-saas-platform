"use client";

import { useEffect, useState } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import {
  fetchControl,
  fifoRecheck,
  qualityCsvUrl,
  costQualityCsvUrl,
  type ControlResponse,
  type ControlKpi,
} from "@/lib/control";

function KpiCard({ kpi, index }: { kpi: ControlKpi; index: number }) {
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

function StatusBanner({ banner }: { banner: { tone: "good" | "warn"; message: string } }) {
  const bg = banner.tone === "good" ? "var(--good-soft)" : "var(--warn-soft)";
  const color = banner.tone === "good" ? "var(--good)" : "var(--warn)";
  return (
    <div
      className="rounded-xl px-4 py-3 text-[13px]"
      style={{ background: bg, color }}
    >
      {banner.message}
    </div>
  );
}

function TabButton({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={
        "rounded-full px-4 py-2 text-[13px] font-medium transition-colors " +
        (active
          ? "bg-accent text-accent-foreground"
          : "text-muted-foreground hover:text-foreground hover:bg-secondary")
      }
    >
      {label}
    </button>
  );
}

function TableFromRows({
  rows,
  columns,
}: {
  rows: Record<string, string | number | null>[];
  columns: { key: string; label: string; align?: "left" | "right" }[];
}) {
  if (rows.length === 0) {
    return <div className="text-[13px] text-muted-foreground py-4">Нет данных</div>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-b text-left">
            {columns.map((col) => (
              <th
                key={col.key}
                className={
                  "px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-text-faint " +
                  (col.align === "right" ? "text-right" : "")
                }
              >
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-t hover:bg-secondary/60">
              {columns.map((col) => (
                <td
                  key={col.key}
                  className={
                    "px-3 py-2.5 " +
                    (col.align === "right" ? "text-right font-mono" : "")
                  }
                >
                  {row[col.key] ?? "—"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ControlPage() {
  const [data, setData] = useState<ControlResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"reliability" | "checks" | "todo">("reliability");
  const [recheckLoading, setRecheckLoading] = useState(false);
  const [recheckMessage, setRecheckMessage] = useState<string | null>(null);

  const load = () => {
    fetchControl()
      .then(setData)
      .catch((e: Error) => setError(e.message));
  };

  useEffect(load, []);

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
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-4 w-96" />
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-6">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-24 rounded-2xl" />
          ))}
        </div>
        <Skeleton className="h-16 rounded-xl" />
        <Skeleton className="h-64 rounded-2xl" />
      </div>
    );
  }

  const handleRecheck = async () => {
    setRecheckLoading(true);
    setRecheckMessage(null);
    try {
      const result = await fifoRecheck();
      setRecheckMessage(result.message);
      load(); // refresh data after recheck
    } catch (e) {
      setRecheckMessage((e as Error).message);
    } finally {
      setRecheckLoading(false);
    }
  };

  return (
    <div className="flex flex-col gap-5.5 px-4 py-5 sm:px-6 md:px-10 md:py-7.5 animate-content-in">
      <div className="flex flex-col gap-3">
        <h1 className="text-2xl font-bold tracking-tight">{data.title}</h1>
        <p className="text-[13px] text-muted-foreground">{data.subtitle}</p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-6">
        {data.kpis.map((kpi, i) => (
          <KpiCard key={kpi.key} kpi={kpi} index={i} />
        ))}
      </div>

      <StatusBanner banner={data.banner} />

      <div className="rounded-xl border bg-info-soft/40 px-4 py-3 text-[13px] text-info">
        {data.optional_note}
      </div>

      {/* FIFO recheck row */}
      <div className="flex flex-col gap-3 md:flex-row md:items-center">
        <button
          onClick={handleRecheck}
          disabled={recheckLoading}
          className={
            "rounded-xl px-4 py-2.5 text-[13px] font-semibold transition-colors " +
            (recheckLoading
              ? "bg-muted text-muted-foreground cursor-wait"
              : "bg-accent text-accent-foreground hover:opacity-90")
          }
        >
          {recheckLoading ? "Перепроверка..." : "Полная перепроверка FIFO"}
        </button>
        <div
          className="flex-1 rounded-xl border px-4 py-3 text-[13px]"
          style={{
            borderColor:
              data.fifo_guard.tone === "warn"
                ? "var(--warn)"
                : data.fifo_guard.tone === "info"
                ? "var(--info)"
                : "var(--good)",
            background:
              data.fifo_guard.tone === "warn"
                ? "var(--warn-soft)"
                : data.fifo_guard.tone === "info"
                ? "var(--info-soft)"
                : "var(--good-soft)",
            color:
              data.fifo_guard.tone === "warn"
                ? "var(--warn)"
                : data.fifo_guard.tone === "info"
                ? "var(--info)"
                : "var(--good)",
          }}
        >
          {data.fifo_guard.message}
        </div>
      </div>

      {recheckMessage && (
        <div
          className="rounded-xl px-4 py-3 text-[13px] animate-fade-in"
          style={{ background: "var(--good-soft)", color: "var(--good)" }}
        >
          {recheckMessage}
        </div>
      )}

      {/* Tabs */}
      <div className="flex flex-wrap gap-1.5">
        <TabButton
          active={tab === "reliability"}
          label="Надёжность расчётов"
          onClick={() => setTab("reliability")}
        />
        <TabButton
          active={tab === "checks"}
          label="Все проверки"
          onClick={() => setTab("checks")}
        />
        <TabButton
          active={tab === "todo"}
          label="Что исправить"
          onClick={() => setTab("todo")}
        />
      </div>

      {/* Tab content */}
      <div className="animate-fade-in">
        {tab === "reliability" && (
          <div className="rounded-2xl border bg-card p-5 shadow-[0_26px_50px_-22px_rgba(0,0,0,.55)]">
            <TableFromRows
              rows={data.reliability.rows as Record<string, string>[]}
              columns={[
                { key: "module", label: "Расчётный контур" },
                { key: "reliability", label: "Надёжность" },
                { key: "basis", label: "Основание" },
              ]}
            />
            <div className="mt-3 text-[11.5px] text-text-faint">
              {data.reliability.caption}
            </div>
          </div>
        )}

        {tab === "checks" && (
          <div className="rounded-2xl border bg-card p-5 shadow-[0_26px_50px_-22px_rgba(0,0,0,.55)]">
            <TableFromRows
              rows={data.all_checks.rows as Record<string, string>[]}
              columns={[
                { key: "module", label: "Модуль" },
                { key: "check", label: "Проверка" },
                { key: "status", label: "Статус" },
                { key: "detail", label: "Детали" },
                { key: "action", label: "Что сделать" },
              ]}
            />
            <a
              href={qualityCsvUrl()}
              download
              className="mt-4 inline-block rounded-lg bg-accent px-4 py-2 text-[13px] font-semibold text-accent-foreground hover:opacity-90"
            >
              Скачать отчёт качества данных CSV
            </a>

            {/* Cost diagnostics expander */}
            <details className="mt-6">
              <summary className="cursor-pointer text-[14.5px] font-semibold hover:text-accent">
                {data.all_checks.cost_quality.title}
              </summary>
              <div className="mt-3">
                {data.all_checks.cost_quality.rows.length === 0 ? (
                  <div className="text-[13px] text-muted-foreground py-2">
                    {data.all_checks.cost_quality.empty_note}
                  </div>
                ) : data.all_checks.cost_quality.issue_count === 0 ? (
                  <div className="text-[13px] text-good py-2">
                    {data.all_checks.cost_quality.ok_note}
                  </div>
                ) : (
                  <div className="text-[13px] text-warn py-2">
                    Найдены проблемные позиции: {data.all_checks.cost_quality.issue_count}. Они показаны первыми в таблице.
                  </div>
                )}
                {data.all_checks.cost_quality.rows.length > 0 && (
                  <>
                    <TableFromRows
                      rows={data.all_checks.cost_quality.rows as Record<string, string | number | null>[]}
                      columns={[
                        { key: "nm_id", label: "Артикул WB", align: "right" },
                        { key: "article", label: "Артикул продавца" },
                        { key: "product", label: "Товар" },
                        { key: "source", label: "Источник активности" },
                        { key: "rate", label: "Ставка, ₽", align: "right" },
                        { key: "match", label: "Сопоставление" },
                        { key: "status", label: "Статус" },
                      ]}
                    />
                    <a
                      href={costQualityCsvUrl()}
                      download
                      className="mt-4 inline-block rounded-lg bg-accent px-4 py-2 text-[13px] font-semibold text-accent-foreground hover:opacity-90"
                    >
                      Скачать диагностику себестоимости CSV
                    </a>
                  </>
                )}
              </div>
            </details>
          </div>
        )}

        {tab === "todo" && (
          <div className="flex flex-col gap-5">
            {data.todo.issues.length === 0 ? (
              <div
                className="rounded-xl px-4 py-3 text-[13px]"
                style={{ background: "var(--good-soft)", color: "var(--good)" }}
              >
                {data.todo.empty_note}
              </div>
            ) : (
              <div className="rounded-2xl border bg-card p-5 shadow-[0_26px_50px_-22px_rgba(0,0,0,.55)]">
                <TableFromRows
                  rows={data.todo.issues as Record<string, string>[]}
                  columns={[
                    { key: "priority", label: "Приоритет" },
                    { key: "module", label: "Модуль" },
                    { key: "check", label: "Проверка" },
                    { key: "detail", label: "Детали" },
                    { key: "action", label: "Что сделать" },
                  ]}
                />
              </div>
            )}

            {data.todo.cost_issues.rows.length > 0 && (
              <div className="rounded-2xl border bg-card p-5 shadow-[0_26px_50px_-22px_rgba(0,0,0,.55)]">
                <h3 className="text-[14.5px] font-semibold">{data.todo.cost_issues.title}</h3>
                <p className="mt-0.5 mb-4 text-[12px] text-muted-foreground">
                  {data.todo.cost_issues.note}
                </p>
                <TableFromRows
                  rows={data.todo.cost_issues.rows as Record<string, string | number | null>[]}
                  columns={[
                    { key: "nm_id", label: "Артикул WB", align: "right" },
                    { key: "article", label: "Артикул продавца" },
                    { key: "product", label: "Товар" },
                    { key: "rate", label: "Ставка, ₽", align: "right" },
                    { key: "match", label: "Сопоставление" },
                    { key: "status", label: "Статус" },
                  ]}
                />
                <div className="mt-3 text-[11.5px] text-text-faint">
                  {data.todo.cost_issues.caption}
                </div>
              </div>
            )}

            {data.todo.optional.rows.length > 0 && (
              <div className="rounded-2xl border bg-card p-5 shadow-[0_26px_50px_-22px_rgba(0,0,0,.55)]">
                <h3 className="text-[14.5px] font-semibold">{data.todo.optional.title}</h3>
                <p className="mt-0.5 mb-4 text-[12px] text-muted-foreground">
                  {data.todo.optional.subtitle}
                </p>
                <TableFromRows
                  rows={data.todo.optional.rows as Record<string, string>[]}
                  columns={[
                    { key: "module", label: "Модуль" },
                    { key: "check", label: "Проверка" },
                    { key: "detail", label: "Детали" },
                    { key: "action", label: "Что сделать" },
                  ]}
                />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
