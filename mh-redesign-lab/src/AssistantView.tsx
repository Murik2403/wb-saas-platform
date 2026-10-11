import { useDialogFocus } from "./dialog-focus";
import { useState } from "react";
import { Bot, CheckCircle2, Command, ShieldCheck, X, Zap } from "lucide-react";
import type { ActionRecommendation, AgentCandidate, AssistantAutomation, DashboardMetric } from "./contracts";
import { decideAgentCandidate } from "./data-source";
import { AutomationRow, MetricGrid, SectionHeader, StatusPill, cx, notifyUser } from "./cabinet-shared";

export function AssistantView({
  agentCandidates,
  agentHistory,
  actions,
  assistantMetrics,
  automations,
  onAction,
  onRefresh,
}: {
  agentCandidates: AgentCandidate[];
  agentHistory: AgentCandidate[];
  actions: ActionRecommendation[];
  assistantMetrics: DashboardMetric[];
  automations: AssistantAutomation[];
  onAction: (label: string, itemLabel: string) => void;
  onRefresh: () => Promise<void>;
}) {
  const [confirmCandidate, setConfirmCandidate] = useState<AgentCandidate | null>(null);
  const [decisionBusy, setDecisionBusy] = useState(false);
  const [candidateFilter, setCandidateFilter] = useState<"all" | "ad" | "price">("all");
  const [marketplaceFilter, setMarketplaceFilter] = useState("all");
  const [actionFilter, setActionFilter] = useState("all");
  const visibleCandidates = agentCandidates.filter((candidate) =>
    (candidateFilter === "all" || candidate.agent === candidateFilter)
    && (marketplaceFilter === "all" || candidate.marketplace === marketplaceFilter)
    && (actionFilter === "all" || candidate.action === actionFilter)
  );
  const marketplaces = [...new Set(agentCandidates.map((candidate) => candidate.marketplace))];
  const actionOptions = [...new Set(agentCandidates.map((candidate) => candidate.action))];
  return (
    <div className="workspace-view">
      <section className="hero-strip assistant-hero" aria-label="Агенты цены и рекламы">
        <div>
          <div className="eyebrow">
            <Bot size={16} />
            Агенты
          </div>
          <h2>Агенты цены и рекламы</h2>
          <p>Рекомендации по рекламе и ценам. Рискованные операции остаются на ручном подтверждении.</p>
        </div>
        <div className="hero-actions">
          <button className="secondary-button" type="button" onClick={() => onAction("Агент", "очередь")}>
            <Zap size={17} />
            <span>Запустить</span>
          </button>
        </div>
      </section>

      <MetricGrid metrics={assistantMetrics} onMetricClick={(metric) => onAction("Агент", metric.label)} />

      <div className={cx("content-grid", !agentCandidates.length && "assistant-empty-layout")}>
        <section className="work-section">
          <SectionHeader kicker="Решения" title="Очередь AI-агентов">
            {agentCandidates.length ? <div className="agent-filters">
              <select aria-label="Тип агента" className="filter-select" value={candidateFilter} onChange={(event) => setCandidateFilter(event.target.value as "all" | "ad" | "price")}>
                <option value="all">Все типы</option><option value="ad">Реклама</option><option value="price">Цены</option>
              </select>
              <select aria-label="Площадка" className="filter-select" value={marketplaceFilter} onChange={(event) => setMarketplaceFilter(event.target.value)}>
                <option value="all">Все площадки</option>{marketplaces.map((marketplace) => <option key={marketplace} value={marketplace}>{marketplace.toUpperCase()}</option>)}
              </select>
              <select aria-label="Действие" className="filter-select" value={actionFilter} onChange={(event) => setActionFilter(event.target.value)}>
                <option value="all">Все действия</option>{actionOptions.map((action) => <option key={action} value={action}>{action}</option>)}
              </select>
            </div> : null}
          </SectionHeader>
          {visibleCandidates.length ? <div className="action-list">
            {visibleCandidates.slice(0, 8).map((candidate) => <AgentCandidateRow candidate={candidate} key={candidate.id} onDecision={async (outcome) => {
              try {
                if (outcome === "apply") {
                  setConfirmCandidate(candidate);
                  return;
                }
                await decideAgentCandidate(candidate.id, outcome);
                await onRefresh();
              } catch (error) {
                await notifyUser(error instanceof Error ? error.message : "Решение не выполнено");
              }
            }} />)}
          </div> : <p className="plan-empty">Очередь AI-агентов пуста</p>}
        </section>

        <aside className="insight-column">
          <section className="panel">
            <SectionHeader compact kicker="Правила" title="Автоматизация">
              <Command size={20} />
            </SectionHeader>
            {automations.length ? <div className="automation-list">
              {automations.map((automation) => <AutomationRow item={automation} key={automation.title} onOpen={() => onAction("Агент", automation.title)} />)}
            </div> : <p className="plan-empty">Правила автоматизации не переданы API</p>}
          </section>

          <section className="panel">
            <SectionHeader compact kicker="История" title="Последние решения">
              <CheckCircle2 size={20} />
            </SectionHeader>
            {agentHistory.length ? <div className="automation-list">{agentHistory.slice(0, 6).map((item) => <article className="automation-row" key={item.id}><div><strong>{item.targetName}</strong><span>{item.marketplace.toUpperCase()} · {item.agent} · {item.action}</span></div><StatusPill tone={item.status === "rejected" ? "danger" : "good"}>{item.status === "rejected" ? "отклонено" : "применено"}</StatusPill></article>)}</div> : <p className="plan-empty">Решений пока нет</p>}
          </section>

          <section className="panel">
            <SectionHeader compact kicker="Границы" title="Что не делать авто">
              <ShieldCheck size={20} />
            </SectionHeader>
            <div className="guardrail-list">
              <StatusPill tone="danger">списания FIFO</StatusPill>
              <StatusPill tone="warn">цены ниже маржи</StatusPill>
              <StatusPill tone="info">новые поставщики</StatusPill>
            </div>
          </section>
        </aside>
      </div>
      {confirmCandidate ? <AgentDecisionDialog
        busy={decisionBusy}
        candidate={confirmCandidate}
        onCancel={() => setConfirmCandidate(null)}
        onConfirm={async () => {
          setDecisionBusy(true);
          try {
            await decideAgentCandidate(confirmCandidate.id, "apply");
            setConfirmCandidate(null);
            await onRefresh();
          } catch (error) {
            await notifyUser(error instanceof Error ? error.message : "Решение не выполнено");
          } finally {
            setDecisionBusy(false);
          }
        }}
      /> : null}
    </div>
  );
}

function AgentCandidateRow({ candidate, onDecision }: { candidate: AgentCandidate; onDecision: (outcome: "apply" | "reject") => Promise<void> }) {
  return (
    <article className="action-row warn">
      <div className="action-row-icon"><Bot size={18} /></div>
      <div className="action-row-copy"><span>{candidate.marketplace.toUpperCase()} · {candidate.agent} · {candidate.action}</span><h3>{candidate.targetName}</h3><p>{candidate.reason}</p><small>{candidate.currentValue} → {candidate.proposedValue}</small></div>
      <div className="action-row-actions"><button className="secondary-button" type="button" onClick={() => void onDecision("apply")}>Применить</button><button className="icon-button" type="button" aria-label="Отклонить рекомендацию" onClick={() => void onDecision("reject")}><X size={16} /></button></div>
    </article>
  );
}

function AgentDecisionDialog({ candidate, busy, onCancel, onConfirm }: { candidate: AgentCandidate; busy: boolean; onCancel: () => void; onConfirm: () => Promise<void> }) {
  const dialogRef=useDialogFocus(onCancel);
  return (
    <div className="drawer-backdrop operation-backdrop" role="presentation">
      <section className="plan-drawer operation-dialog" role="dialog" aria-modal="true" ref={dialogRef} aria-labelledby="agent-confirm-title">
        <div className="drawer-header"><div><span className="eyebrow">подтверждение</span><h2 id="agent-confirm-title">Применить рекомендацию?</h2><p>{candidate.marketplace.toUpperCase()} · {candidate.targetName}</p></div><button className="icon-button" type="button" aria-label="Закрыть" onClick={onCancel}><X size={18} /></button></div>
        <div className="operation-form"><p className="confirm-copy">{candidate.reason}</p><p className="confirm-values">{candidate.currentValue} → {candidate.proposedValue}</p><div className="operation-actions"><button className="ghost-button" type="button" onClick={onCancel}>Отмена</button><button className="primary-button" type="button" disabled={busy} onClick={() => void onConfirm()}><CheckCircle2 size={16} />{busy ? "Применяем" : "Применить"}</button></div></div>
      </section>
    </div>
  );
}
