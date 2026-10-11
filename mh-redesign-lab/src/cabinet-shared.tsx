import { createDialogFocusScope } from "./dialog-focus";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Sparkles } from "lucide-react";
import type { AssistantAutomation, DashboardMetric, InventoryWatchRow, Tone } from "./contracts";
import { type DashboardPeriod, type DashboardPeriodId } from "./data-source";

export function cx(...classes: Array<string | false | null | undefined>) {
  return classes.filter(Boolean).join(" ");
}

/** Small accessible replacement for browser prompt/alert/confirm.
 * It is deliberately DOM based so legacy inline action handlers can use it
 * without coupling every section to a second piece of React state.
 */
export let interactionDialogSequence=0;
export function openInteractionDialog(options: { title: string; message?: string; input?: string; confirm?: boolean }): Promise<string | boolean | null> {
  if (typeof document === "undefined") return Promise.resolve(options.input ?? (options.confirm ? false : null));
  const opener=document.activeElement instanceof HTMLElement?document.activeElement:null;
  const dialog = document.createElement("dialog");
  const titleId=`interaction-dialog-title-${++interactionDialogSequence}`;
  dialog.setAttribute("aria-labelledby",titleId);dialog.setAttribute("aria-modal","true");
  dialog.className = "plan-drawer operation-dialog interaction-dialog";
  const heading = document.createElement("h3");heading.id=titleId; heading.textContent = options.title;
  const message = document.createElement("p"); message.textContent = options.message ?? "";
  if(options.message){message.id=titleId+"-message";dialog.setAttribute("aria-describedby",message.id);}
  const form = document.createElement("form"); form.method = "dialog";
  const controls = document.createElement("div"); controls.className = "dialog-actions";
  const cancel = document.createElement("button"); cancel.type = "button"; cancel.className = "ghost-button"; cancel.textContent = "Отмена";
  const submit = document.createElement("button"); submit.type = "submit"; submit.className = "primary-button"; submit.textContent = options.confirm ? "Подтвердить" : "Сохранить";
  const input = options.input !== undefined ? document.createElement("textarea") : null;
  if (input) { input.className = "dialog-input"; input.value = options.input ?? ""; input.rows = 4; input.setAttribute("aria-label", options.title); }
  controls.append(cancel, submit);
  form.append(controls); dialog.append(heading, message); if (input) dialog.append(input); dialog.append(form);
  document.body.appendChild(dialog);
  return new Promise((resolve) => {
    let settled = false;let disposeFocus:(()=>void)|undefined;
    const finish = (value: string | boolean | null) => { if (settled) return; settled = true; dialog.remove();disposeFocus?.(); resolve(value); };
    cancel.addEventListener("click", () => finish(null));
    dialog.addEventListener("cancel", () => finish(null));
    form.addEventListener("submit", (event) => { event.preventDefault(); finish(options.confirm ? true : (input?.value ?? "")); });
    dialog.addEventListener("close", () => finish(null));
    if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", "");
    disposeFocus=createDialogFocusScope(dialog,()=>finish(null),opener);
  });
}

export const askText = (title: string, initial = "") => openInteractionDialog({ title, input: initial }) as Promise<string | null>;
export const askConfirm = (message: string) => openInteractionDialog({ title: "Подтверждение", message, confirm: true }) as Promise<boolean>;
export const notifyUser = (message: string) => openInteractionDialog({ title: "Результат", message }).then(() => undefined);

export const dashboardPeriodOptions: Array<{ id: DashboardPeriodId; label: string }> = [
  { id: "today", label: "Сегодня" },
  { id: "7d", label: "7 дней" },
  { id: "30d", label: "30 дней" },
  { id: "90d", label: "90 дней" },
  { id: "custom", label: "Свои даты" },
];

export function dateInputValue(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function defaultCustomPeriod(): DashboardPeriod {
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - 29);
  return { id: "custom", start: dateInputValue(start), end: dateInputValue(end) };
}

export function gatewayUrlFor(path: string) {
  const { protocol, hostname, host } = window.location;
  if (hostname === "localhost" || hostname === "127.0.0.1" || hostname.endsWith(".localhost")) {
    return `${protocol}//${host}${path}`;
  }

  const hostParts = hostname.split(".");
  const appIndex = hostParts.findIndex((part) => part === "app");
  if (appIndex >= 0 && appIndex + 1 < hostParts.length) {
    return `${protocol}//${hostParts.slice(appIndex + 1).join(".")}${path}`;
  }

  return `${protocol}//${host}${path}`;
}

export function DashboardPeriodControl({
  period,
  setPeriod,
}: {
  period: DashboardPeriod;
  setPeriod: (period: DashboardPeriod) => void;
}) {
  const [draftRange, setDraftRange] = useState(() => ({
    start: period.start ?? defaultCustomPeriod().start ?? "",
    end: period.end ?? defaultCustomPeriod().end ?? "",
  }));

  useEffect(() => {
    if (period.id === "custom") {
      setDraftRange({
        start: period.start ?? defaultCustomPeriod().start ?? "",
        end: period.end ?? defaultCustomPeriod().end ?? "",
      });
    }
  }, [period.id, period.start, period.end]);

  const selectPeriod = (id: DashboardPeriodId) => {
    if (id === "custom") {
      const next = period.id === "custom" ? period : defaultCustomPeriod();
      setDraftRange({ start: next.start ?? "", end: next.end ?? "" });
      setPeriod(next);
      return;
    }
    setPeriod({ id });
  };

  const validDraft = Boolean(draftRange.start && draftRange.end && draftRange.start <= draftRange.end);
  const rangeChanged = draftRange.start !== period.start || draftRange.end !== period.end;

  return (
    <div className="period-control" aria-label="Период данных">
      <span className="period-caption">Период данных</span>
      <div className="segmented period-segments" role="group" aria-label="Быстрый выбор периода">
        {dashboardPeriodOptions.map((option) => (
          <button
            className={period.id === option.id ? "selected" : ""}
            key={option.id}
            type="button"
            onClick={() => selectPeriod(option.id)}
          >
            {option.label}
          </button>
        ))}
      </div>
      {period.id === "custom" ? (
        <div className="date-range-inputs">
          <label>
            <span>С</span>
            <input
              aria-label="Дата начала"
              type="date"
              value={draftRange.start}
              max={draftRange.end || undefined}
              onChange={(event) => setDraftRange((range) => ({ ...range, start: event.target.value }))}
            />
          </label>
          <label>
            <span>По</span>
            <input
              aria-label="Дата окончания"
              type="date"
              value={draftRange.end}
              min={draftRange.start || undefined}
              onChange={(event) => setDraftRange((range) => ({ ...range, end: event.target.value }))}
            />
          </label>
          <button
            className="period-apply-button"
            disabled={!validDraft || !rangeChanged}
            type="button"
            onClick={() => setPeriod({ id: "custom", start: draftRange.start, end: draftRange.end })}
          >
            Применить
          </button>
        </div>
      ) : null}
    </div>
  );
}

export function MetricGrid({ metrics, onMetricClick }: { metrics: DashboardMetric[]; onMetricClick?: (metric: DashboardMetric) => void }) {
  return (
    <section className="metrics-grid" aria-label="Ключевые показатели">
      {metrics.map((metric) => (
        <MetricCard key={metric.label} metric={metric} onClick={onMetricClick ? () => onMetricClick(metric) : undefined} />
      ))}
    </section>
  );
}

export function MetricCard({ metric, onClick }: { metric: DashboardMetric; onClick?: () => void }) {
  const content = <>
      <span>{metric.label}</span>
      <strong>{metric.value}</strong>
      <div>
        <em>{metric.delta}</em>
        {metric.change ? <small className={cx("metric-change", metric.change.tone)}>{metric.change.text}</small> : null}
        <p>{metric.detail}</p>
      </div>
    </>;
  return onClick ? <button className={cx("metric-card", "metric-card-button", metric.tone)} type="button" onClick={onClick}>{content}</button> : <article className={cx("metric-card", metric.tone)}>{content}</article>;
}

export function SectionHeader({
  children,
  compact = false,
  kicker,
  title,
}: {
  children?: ReactNode;
  compact?: boolean;
  kicker: string;
  title: string;
}) {
  return (
    <div className={cx("section-header", compact && "compact")}>
      <div>
        <span>{kicker}</span>
        <h2>{title}</h2>
      </div>
      {children}
    </div>
  );
}

export function AutomationRow({ item, onOpen }: { item: AssistantAutomation; onOpen?: () => void }) {
  const content = <>
      <div>
        <strong>{item.title}</strong>
        <span>{item.detail}</span>
      </div>
      <StatusPill tone={item.tone}>{item.status}</StatusPill>
    </>;
  return onOpen ? <button className={cx("automation-row", "detail-trigger", item.tone)} type="button" onClick={onOpen}>{content}</button> : <article className={cx("automation-row", item.tone)}>{content}</article>;
}

export function InventoryTable({ inventory, onOpen }: { inventory: InventoryWatchRow[]; onOpen?: (row: InventoryWatchRow) => void }) {
  const sharedSignal = inventory.length > 1 && inventory.every(row => row.signal === inventory[0].signal) ? inventory[0].signal : null;
  const displayRows = sharedSignal ? inventory.map(row => ({...row,signal:"—"})) : inventory;
  return (
    <section className="table-section" id="inventory-watch">
      <SectionHeader kicker="SKU под наблюдением" title="Остатки, маржа, сигнал">
        <button className="ghost-button desktop-only" type="button" disabled={!onOpen} onClick={() => { if (onOpen && inventory[0]) onOpen(inventory[0]); }}>
          <Sparkles size={17} />
          <span>AI сводка</span>
        </button>
      </SectionHeader>

      {sharedSignal ? <p className="form-hint">{sharedSignal}</p> : null}
      {inventory.some(row => row.margin.trim().startsWith("−") || row.margin.trim().startsWith("-")) ? <p className="form-hint">Отрицательная маржа означает, что учтённые затраты превышают выручку. При небольшой выручке процент может быть большим; проверьте период и состав затрат.</p> : null}
      <div className="desktop-table">
        <table>
          <thead>
            <tr>
              <th>SKU</th>
              <th>Товар</th>
              <th>Запас</th>
              <th>Маржа</th>
              <th>Сигнал</th>
            </tr>
          </thead>
          <tbody>
            {displayRows.map((row) => (
            <InventoryTableRow row={row} key={row.sku} onOpen={onOpen ? () => onOpen(inventory.find(original => original.sku === row.sku) ?? row) : undefined} />
            ))}
          </tbody>
        </table>
      </div>

      <div className="mobile-sku-list">
        {displayRows.map((row) => (
          <article className="mobile-sku-card detail-trigger" key={row.sku} tabIndex={onOpen ? 0 : undefined} role={onOpen ? "button" : undefined} onClick={onOpen ? () => onOpen(inventory.find(original => original.sku === row.sku) ?? row) : undefined} onKeyDown={onOpen ? (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpen(inventory.find(original => original.sku === row.sku) ?? row); } } : undefined}>
            <div>
              <span>{row.sku}</span>
              <h3>{row.name}</h3>
            </div>
            <div className="mobile-sku-meta">
              <strong>{row.stock}</strong>
              <span className="mobile-sku-margin">Маржа {row.margin}</span>
              <StatusPill tone={row.tone}>{row.signal}</StatusPill>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

export function InventoryTableRow({ onOpen, row }: { onOpen?: () => void; row: InventoryWatchRow }) {
  return (
    <tr className={onOpen ? "detail-trigger" : undefined} tabIndex={onOpen ? 0 : undefined} onClick={onOpen} onKeyDown={onOpen ? (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpen(); } } : undefined}>
      <td>{row.sku}</td>
      <td>{row.name}</td>
      <td>{row.stock}</td>
      <td>{row.margin}</td>
      <td>
        <StatusPill tone={row.tone}>{row.signal}</StatusPill>
      </td>
    </tr>
  );
}

export function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function StatusPill({ children, tone }: { children: ReactNode; tone: Tone }) {
  return <span className={cx("status-pill", tone)}>{children}</span>;
}
