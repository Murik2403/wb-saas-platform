"use client";

import { useEffect, useState } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import {
  fetchReports,
  createReport,
  deleteReport,
  generateReportNow,
  requestTelegramLinkCode,
  reportDownloadUrl,
  type ReportsResponse,
} from "@/lib/reports";

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-2xl border bg-card p-5 shadow-[0_16px_32px_-18px_rgba(0,0,0,.5)]" style={{ borderColor: "var(--border)" }}>
      {children}
    </div>
  );
}

function Pill({
  children,
  onClick,
  tone = "default",
  disabled,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  tone?: "default" | "primary" | "danger";
  disabled?: boolean;
}) {
  const style =
    tone === "primary"
      ? { background: "var(--accent)", color: "var(--accent-foreground)" }
      : tone === "danger"
        ? { background: "var(--critical-soft)", color: "var(--critical)" }
        : { borderColor: "var(--border)" };
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={
        "rounded-full px-3.5 py-1.75 text-[12.5px] font-semibold transition-transform active:scale-[0.97] disabled:opacity-50 " +
        (tone === "default" ? "border" : "")
      }
      style={style}
    >
      {children}
    </button>
  );
}

export default function ReportsPage() {
  const [data, setData] = useState<ReportsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<{ level: "success" | "error"; text: string } | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [linkCode, setLinkCode] = useState<{ code: string; ttl: number } | null>(null);

  function load() {
    fetchReports().then(setData).catch((e: Error) => setError(e.message));
  }

  useEffect(load, []);

  function flash(level: "success" | "error", text: string) {
    setMessage({ level, text });
    setTimeout(() => setMessage((m) => (m?.text === text ? null : m)), 5000);
  }

  if (error) return <div className="p-10 text-sm text-critical">Не удалось загрузить: {error}</div>;
  if (!data) {
    return (
      <div className="flex flex-col gap-5.5 px-4 py-5 sm:px-6 md:px-10 md:py-7.5">
        <Skeleton className="h-6 w-32" />
        <Skeleton className="h-24 rounded-2xl" />
        <Skeleton className="h-40 rounded-2xl" />
      </div>
    );
  }

  const c = data.copy;

  return (
    <div className="flex flex-col gap-5.5 px-4 py-5 sm:px-6 md:px-10 md:py-7.5 animate-content-in">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{c.title}</h1>
        <p className="mt-1 text-[13px] text-muted-foreground">{c.subtitle}</p>
      </div>

      {message && (
        <div
          className="rounded-xl px-4 py-3 text-[13px]"
          style={
            message.level === "success"
              ? { background: "var(--good-soft)", color: "var(--good)" }
              : { background: "var(--critical-soft)", color: "var(--critical)" }
          }
        >
          {message.text}
        </div>
      )}

      {data.telegram_configured && (
        <Card>
          <h3 className="text-[14.5px] font-semibold">{c.telegram_header}</h3>
          <p className="mt-0.5 mb-3 text-[12px] text-muted-foreground">{c.telegram_hint}</p>
          {data.telegram_linked ? (
            <div className="text-[13px] text-good">{c.telegram_linked}</div>
          ) : (
            <div className="flex flex-col gap-3">
              <p className="text-[12.5px] text-muted-foreground">{c.telegram_caption}</p>
              <div className="flex gap-2">
                <Pill
                  onClick={async () => {
                    try {
                      const r = await requestTelegramLinkCode();
                      setLinkCode({ code: r.code, ttl: r.ttl_minutes });
                    } catch (e) {
                      flash("error", (e as Error).message);
                    }
                  }}
                >
                  Получить код для привязки
                </Pill>
                <Pill onClick={load}>Обновить статус</Pill>
              </div>
              {linkCode && (
                <div className="rounded-xl px-4 py-3 text-[12.5px]" style={{ background: "var(--accent)", color: "var(--accent-foreground)" }}>
                  Откройте бота поддержки MARKETSHELPER в Telegram и отправьте команду:{" "}
                  <code className="font-mono">/link {linkCode.code}</code>. Код действует {linkCode.ttl} минут.
                </div>
              )}
            </div>
          )}
        </Card>
      )}

      <Card>
        <button onClick={() => setFormOpen((o) => !o)} className="flex w-full items-center justify-between text-left">
          <h3 className="text-[14.5px] font-semibold">Новый отчёт</h3>
          <span className="text-muted-foreground">{formOpen ? "−" : "+"}</span>
        </button>
        {formOpen && (
          <NewReportForm
            data={data}
            onCreated={() => {
              setFormOpen(false);
              flash("success", "Расписание сохранено");
              load();
            }}
            onError={(msg) => flash("error", msg)}
          />
        )}
      </Card>

      <div>
        <h3 className="mb-0.5 text-[14.5px] font-semibold">Расписания ({data.schedules.length})</h3>
        <p className="mb-3 text-[12px] text-muted-foreground">{c.schedules_hint}</p>
        {data.schedules.length === 0 ? (
          <div className="rounded-2xl border bg-card p-6 text-center text-[13px] text-muted-foreground" style={{ borderColor: "var(--border)" }}>
            <div className="font-semibold text-foreground">{c.schedules_empty_title}</div>
            <div className="mt-1">{c.schedules_empty_note}</div>
          </div>
        ) : (
          <div className="flex flex-col gap-2.5">
            {data.schedules.map((s) => (
              <Card key={s.id}>
                <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <div className="text-[13.5px] font-semibold">
                      {s.name} — {s.schedule_label}
                    </div>
                    <div className="mt-0.5 text-[12px] text-muted-foreground">
                      Метрики: {s.metric_names}
                      {s.delivery_note}. Последний запуск: {s.last_run_label}
                    </div>
                  </div>
                  <div className="flex shrink-0 gap-2">
                    <Pill
                      tone="primary"
                      onClick={async () => {
                        try {
                          const r = await generateReportNow(s.id);
                          flash("success", r.message);
                          load();
                        } catch (e) {
                          flash("error", (e as Error).message);
                        }
                      }}
                    >
                      Сгенерировать сейчас
                    </Pill>
                    <Pill
                      tone="danger"
                      onClick={async () => {
                        if (!confirm(`Удалить расписание «${s.name}»?`)) return;
                        try {
                          await deleteReport(s.id);
                          flash("success", "Расписание удалено");
                          load();
                        } catch (e) {
                          flash("error", (e as Error).message);
                        }
                      }}
                    >
                      Удалить
                    </Pill>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        )}
      </div>

      <div>
        <h3 className="mb-0.5 text-[14.5px] font-semibold">{c.files_header}</h3>
        <p className="mb-3 text-[12px] text-muted-foreground">{c.files_hint}</p>
        {data.runs.length === 0 ? (
          <div className="rounded-2xl border bg-card p-6 text-center text-[13px] text-muted-foreground" style={{ borderColor: "var(--border)" }}>
            <div className="font-semibold text-foreground">{c.files_empty_title}</div>
            <div className="mt-1">{c.files_empty_note}</div>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {data.runs.map((r) => (
              <div key={r.id} className="flex items-center justify-between rounded-xl border px-4 py-3" style={{ borderColor: "var(--border)" }}>
                <div className="text-[12.5px] text-muted-foreground">
                  {r.report_name} — {r.started_label}
                </div>
                <a href={reportDownloadUrl(r.id)} className="shrink-0">
                  <Pill>Скачать PDF</Pill>
                </a>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function NewReportForm({
  data,
  onCreated,
  onError,
}: {
  data: ReportsResponse;
  onCreated: () => void;
  onError: (msg: string) => void;
}) {
  const [name, setName] = useState("");
  const [metrics, setMetrics] = useState<string[]>(data.metric_options.map((m) => m.code));
  const [scheduleType, setScheduleType] = useState<"daily" | "weekly" | "monthly">("daily");
  const [time, setTime] = useState("10:00");
  const [weekday, setWeekday] = useState(0);
  const [day, setDay] = useState(1);
  const [emailEnabled, setEmailEnabled] = useState(false);
  const [telegramEnabled, setTelegramEnabled] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  function toggleMetric(code: string) {
    setMetrics((m) => (m.includes(code) ? m.filter((c) => c !== code) : [...m, code]));
  }

  async function submit() {
    setSubmitting(true);
    try {
      await createReport({
        name,
        metrics,
        schedule_type: scheduleType,
        schedule_time: time,
        schedule_weekday: scheduleType === "weekly" ? weekday : null,
        schedule_day: scheduleType === "monthly" ? day : null,
        email_enabled: emailEnabled,
        telegram_enabled: telegramEnabled,
      });
      onCreated();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  const inputClass = "w-full rounded-xl border bg-background px-3 py-2 text-[13px] outline-none focus:border-primary";

  return (
    <div className="mt-4 flex flex-col gap-3.5">
      <div>
        <label className="mb-1 block text-[12px] text-muted-foreground">Название отчёта</label>
        <input className={inputClass} style={{ borderColor: "var(--border)" }} value={name} onChange={(e) => setName(e.target.value)} placeholder="Еженедельная сводка" />
      </div>

      <div>
        <label className="mb-1 block text-[12px] text-muted-foreground">Что включить</label>
        <div className="flex flex-wrap gap-1.5">
          {data.metric_options.map((m) => (
            <button
              key={m.code}
              onClick={() => toggleMetric(m.code)}
              className="rounded-full border px-3 py-1.5 text-[12px] transition-colors"
              style={
                metrics.includes(m.code)
                  ? { background: "var(--accent)", color: "var(--accent-foreground)", borderColor: "transparent" }
                  : { borderColor: "var(--border)", color: "var(--muted-foreground)" }
              }
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-[12px] text-muted-foreground">Периодичность</label>
          <select
            className={inputClass}
            style={{ borderColor: "var(--border)" }}
            value={scheduleType}
            onChange={(e) => setScheduleType(e.target.value as "daily" | "weekly" | "monthly")}
          >
            {data.schedule_type_options.map((o) => (
              <option key={o.code} value={o.code}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-[12px] text-muted-foreground">Время (МСК)</label>
          <input type="time" className={inputClass} style={{ borderColor: "var(--border)" }} value={time} onChange={(e) => setTime(e.target.value)} />
        </div>
      </div>

      {scheduleType === "weekly" && (
        <div>
          <label className="mb-1 block text-[12px] text-muted-foreground">День недели</label>
          <select className={inputClass} style={{ borderColor: "var(--border)" }} value={weekday} onChange={(e) => setWeekday(Number(e.target.value))}>
            {data.weekdays.map((w, i) => (
              <option key={w} value={i}>
                {w}
              </option>
            ))}
          </select>
        </div>
      )}
      {scheduleType === "monthly" && (
        <div>
          <label className="mb-1 block text-[12px] text-muted-foreground">Число месяца</label>
          <input
            type="number"
            min={1}
            max={28}
            className={inputClass}
            style={{ borderColor: "var(--border)" }}
            value={day}
            onChange={(e) => setDay(Number(e.target.value))}
          />
        </div>
      )}

      <div className="flex flex-col gap-1.5">
        {data.email_configured ? (
          <label className="flex items-center gap-2 text-[12.5px]">
            <input type="checkbox" checked={emailEnabled} onChange={(e) => setEmailEnabled(e.target.checked)} />
            Также отправлять на email аккаунта
          </label>
        ) : (
          <div className="text-[12px] text-text-faint">{data.copy.email_unavailable}</div>
        )}
        {data.telegram_linked ? (
          <label className="flex items-center gap-2 text-[12.5px]">
            <input type="checkbox" checked={telegramEnabled} onChange={(e) => setTelegramEnabled(e.target.checked)} />
            Также отправлять в Telegram
          </label>
        ) : data.telegram_configured ? (
          <div className="text-[12px] text-text-faint">{data.copy.telegram_unavailable}</div>
        ) : null}
      </div>

      <div>
        <Pill tone="primary" disabled={submitting} onClick={submit}>
          Сохранить расписание
        </Pill>
      </div>
    </div>
  );
}
