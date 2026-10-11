import { cabinetText, checkPriority } from "./cabinet-copy";
import { WorkspaceTabs } from "./WorkspaceTabs";
import { ClipboardCheck } from "lucide-react";
import type { PageId, Tone } from "./contracts";
import { type DataQualityData } from "./data-source";
import { MetricGrid, SectionHeader, StatusPill } from "./cabinet-shared";

export function ControlView({
  data,
  target,
  onNavigate,
  onOpenCheck,
  onOpenFifo,
}: {
  data: DataQualityData | null;
  target?: string | null;
  onNavigate: (page: PageId, target?: string) => void;
  onOpenCheck: (row: Record<string, unknown>) => void;
  onOpenFifo: () => void;
}) {
  if (!data) return <div className="workspace-view"><section className="work-section"><p className="plan-empty">Проверки качества данных загружаются.</p></section></div>;
  const summary = data.summary;
  const checks = [...data.checks].sort((a,b) => checkPriority(a.Статус)-checkPriority(b.Статус));
  const actionableChecks = checks.filter(row => checkPriority(row.Статус) !== 3);
  const readyChecks = checks.filter(row => checkPriority(row.Статус) === 3);
  const score = Number(summary.score ?? 0);
  const tone: Tone = Number(summary.critical ?? 0) > 0 ? "danger" : Number(summary.warnings ?? 0) > 0 ? "warn" : "good";
  const checkTone = (status: string): Tone => checkPriority(status) === 0 ? "danger" : checkPriority(status) === 1 ? "warn" : checkPriority(status) === 3 ? "good" : "info";
  const openCheck = (row: Record<string, unknown>) => onOpenCheck(row);
  return <div className="workspace-view"><section className="hero-strip"><div><div className="eyebrow"><ClipboardCheck size={16} /> контроль</div><h2>{score.toFixed(0)}% готовности данных</h2><p>{String(data.fifo_guard.reason ?? "Проверка полноты и надёжности расчётов.")}</p></div><div className="hero-actions"><button className="secondary-button" type="button" onClick={onOpenFifo}><ClipboardCheck size={17} /><span>Открыть FIFO-сверку</span></button></div></section><MetricGrid metrics={[{label:"Критично",value:String(summary.critical ?? 0),delta:"блокирует",detail:"требует действий",tone:Number(summary.critical ?? 0) ? "danger" : "good"},{label:"Внимание",value:String(summary.warnings ?? 0),delta:"с оговорками",detail:"можно проверить",tone:"warn"},{label:"Готово",value:String(summary.ready ?? 0),delta:"проверок",detail:"можно использовать",tone:"good"},{label:"FIFO",value:String(data.fifo_guard.status ?? "—"),delta:"защита",detail:cabinetText(String(data.fifo_guard.guard_mode ?? "")),tone}]} onMetricClick={(metric) => metric.label === "FIFO" ? onOpenFifo() : onNavigate("control")} /><p className="form-hint">Нормы материала и себестоимость можно проверить в <button className="ghost-button" type="button" onClick={() => onNavigate("settings")}>настройках производства</button>.</p><WorkspaceTabs ariaLabel="Разделы страницы Контроль" storageKey="mh.tab.control" tabs={[{id:"attention",label:`Требует внимания · ${actionableChecks.length}`},{id:"ok",label:`В порядке · ${readyChecks.length}`}]} target={target} targetMap={{"control-checks":"attention"}} initial={actionableChecks.length ? "attention" : "ok"} panels={{attention:<section className="work-section" id="control-checks"><SectionHeader kicker="Проверки" title="Надёжность расчётов"><ClipboardCheck size={18} /></SectionHeader>{actionableChecks.length ? <div className="data-quality-list">{actionableChecks.map((row,index) => { const status = String(row.Статус ?? ""); return <button className="data-quality-card detail-trigger" key={`${String(row.Проверка ?? "check")}-${index}`} type="button" onClick={() => openCheck(row)}><div className="data-quality-heading"><div><span>{cabinetText(String(row.Модуль ?? "Контроль"))}</span><strong>{cabinetText(String(row.Проверка ?? ""))}</strong></div><StatusPill tone={checkTone(status)}>{status}</StatusPill></div><p><span>Детали</span>{String(row.Детали ?? "—")}</p><p><span>Рекомендуемое действие</span>{String(row["Что сделать"] ?? "—")}</p></button>; })}</div> : <p className="plan-empty">{checks.length ? "Проверок, требующих внимания, нет" : "Проверок пока нет"}</p>}</section>,ok:<section className="work-section"><SectionHeader kicker="Проверки" title="Надёжность расчётов"><ClipboardCheck size={18} /></SectionHeader>{readyChecks.length ? <div className="data-quality-list">{readyChecks.map((row,index) => <button className="data-quality-card detail-trigger" type="button" key={index} onClick={() => openCheck(row)}><strong>{cabinetText(String(row.Проверка ?? ""))}</strong><p>{String(row.Детали ?? "—")}</p></button>)}</div> : <p className="plan-empty">Проверок в порядке пока нет</p>}</section>}} /></div>;
}
