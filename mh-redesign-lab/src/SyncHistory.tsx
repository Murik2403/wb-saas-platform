import { useState } from "react";
import type { TenantSyncRun } from "./data-source";
import { historyDate, syncSummary } from "./history-format";

export function SyncHistory({ runs }: { runs: TenantSyncRun[] }) {
  const [expanded, setExpanded] = useState(false);
  if (!runs.length) return <p className="plan-empty">Запусков синхронизации пока нет.</p>;
  return <div className="sync-history">
    {(expanded ? runs : runs.slice(0, 4)).map((run) => {
      const summary = syncSummary(run.status, run.details);
      const state = ["success", "partial", "running"].includes(run.status) ? run.status : "error";
      return <article className={`sync-history-row ${state}`} key={run.id}>
        <div className="sync-history-time">
          <time dateTime={run.started_at ?? undefined}>{historyDate(run.started_at)}</time>
          <span>Запуск #{run.id}</span>
          {run.finished_at ? <span>Завершён в {run.finished_at.slice(11, 16)}</span> : null}
        </div>
        <div className="sync-history-result">
          <strong>{summary.title}</strong>
          {summary.counts ? <p className="sync-history-counts">Обработано — {summary.counts}</p> : null}
          {summary.notice ? <p className="sync-history-notice">{summary.notice}</p> : null}
          {run.details ? <details className="sync-history-details"><summary>Технические подробности запуска #{run.id}</summary><pre>{run.details}</pre></details> : null}
        </div>
        <span className={`history-badge ${state}`}>{state === "success" ? "Готово" : state === "partial" ? "Частично" : state === "running" ? "В процессе" : "Ошибка"}</span>
      </article>;
    })}
    {runs.length > 4 ? <button className="ghost-button history-more" type="button" onClick={() => setExpanded(!expanded)}>{expanded ? "Свернуть историю" : `Показать ещё ${runs.length - 4} запусков`}</button> : null}
  </div>;
}

export function BackupHistory({ backups }: { backups: Array<Record<string, unknown>> }) {
  if (!backups.length) return <p className="plan-empty">Резервных копий пока нет.</p>;
  const ordered = [...backups].sort((a, b) => {
    const first = Date.parse(String(a.modified_at ?? ""));
    const second = Date.parse(String(b.modified_at ?? ""));
    return Number.isFinite(first) && Number.isFinite(second) ? second - first : 0;
  });
  const rows = (items: Array<Record<string, unknown>>) => <div className="backup-history">{items.map((item) => <article className="backup-history-row" key={String(item.name)}>
    <time>{historyDate(String(item.modified_at ?? ""))}</time>
    <div><strong>{String(item.kind ?? "Резервная")} копия</strong><span title={String(item.name ?? "")}>{String(item.name ?? "Архив")}</span></div>
    <span className="backup-size">{(Number(item.size_bytes ?? 0) / 1048576).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} МБ</span>
    <span className="history-badge success">Сохранена</span>
  </article>)}</div>;
  return <>
    {rows(ordered.slice(0, 3))}
    {ordered.length > 3 ? <details className="backup-older"><summary>Все резервные копии · ещё {ordered.length - 3}</summary>{rows(ordered.slice(3))}</details> : null}
  </>;
}
