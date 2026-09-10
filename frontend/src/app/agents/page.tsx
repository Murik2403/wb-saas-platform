"use client";

import { useEffect, useState } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import {
  fetchAgents,
  refreshRecommendations,
  applyCandidate,
  rejectCandidate,
  type AgentsResponse,
  type PendingCandidate,
} from "@/lib/agents";

function PendingCard({
  candidate,
  onApply,
  onReject,
  applying,
  rejecting,
}: {
  candidate: PendingCandidate;
  onApply: () => void;
  onReject: () => void;
  applying: boolean;
  rejecting: boolean;
}) {
  const targetDisplay = candidate.target_name || candidate.target_id;
  const header = `${candidate.marketplace.toUpperCase()} · ${candidate.agent} · ${candidate.action}`;

  return (
    <div className="flex flex-col gap-2 rounded-2xl border bg-card p-4 animate-content-in">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-[14px] font-semibold text-foreground">
            {header} — {targetDisplay}
          </div>
          <div className="mt-1 text-[12px] text-muted-foreground">
            {candidate.current_display} → {candidate.proposed_display}
          </div>
          <div className="mt-1 text-[12px] leading-relaxed text-text-faint">
            {candidate.reason}
          </div>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button
            size="sm"
            variant="default"
            disabled={!candidate.can_apply || applying}
            onClick={onApply}
          >
            {applying ? "Применение..." : "Применить"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={rejecting}
            onClick={onReject}
          >
            {rejecting ? "Отклонение..." : "Отклонить"}
          </Button>
        </div>
      </div>
    </div>
  );
}

export default function AgentsPage() {
  const [data, setData] = useState<AgentsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [actioningId, setActioningId] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);

  const load = () => {
    fetchAgents()
      .then(setData)
      .catch((e: Error) => setError(e.message));
  };

  useEffect(load, []);

  if (error) {
    return (
      <div className="p-10 text-sm text-critical">Не удалось загрузить данные: {error}</div>
    );
  }

  if (!data) {
    return (
      <div className="flex flex-col gap-5.5 px-4 py-5 sm:px-6 md:px-10 md:py-7.5">
        <div className="flex flex-col gap-2">
          <Skeleton className="h-6 w-32" />
          <Skeleton className="h-4 w-72" />
        </div>
        <Skeleton className="h-10 w-48" />
        <div className="flex flex-col gap-3">
          {Array.from({ length: 2 }).map((_, i) => (
            <Skeleton key={i} className="h-24 rounded-2xl" />
          ))}
        </div>
      </div>
    );
  }

  // Empty state: no marketplace connected
  if (data.empty_state) {
    return (
      <div className="flex flex-col gap-5.5 px-4 py-5 sm:px-6 md:px-10 md:py-7.5 animate-content-in">
        <div className="text-2xl font-bold tracking-tight">Агенты</div>
        <div className="text-[13px] text-muted-foreground">
          Рекомендации по ценам и рекламе для WB и Ozon — ничего не меняется без вашего явного подтверждения
        </div>
        <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
          <div className="text-[18px] font-semibold text-foreground">
            {data.empty_state.title}
          </div>
          <div className="max-w-md text-[13px] leading-relaxed text-muted-foreground">
            {data.empty_state.note}
          </div>
        </div>
      </div>
    );
  }

  const handleRefresh = async () => {
    setRefreshing(true);
    setActionError(null);
    try {
      const result = await refreshRecommendations();
      if (result.level === "success") {
        load();
      } else {
        setActionError(result.message);
      }
    } catch (e: unknown) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setRefreshing(false);
    }
  };

  const handleApply = async (candidate: PendingCandidate) => {
    const confirmText =
      `${candidate.marketplace.toUpperCase()} / ${candidate.agent} / ${candidate.action}\n` +
      `${candidate.target_name || candidate.target_id}\n` +
      `${candidate.current_display} → ${candidate.proposed_display}`;
    if (!confirm(`Применить изменение?\n\n${confirmText}`)) {
      return;
    }

    setActioningId(candidate.id);
    setActionError(null);
    try {
      const result = await applyCandidate(candidate.id);
      if (result.level === "success") {
        load();
      } else {
        setActionError(result.message);
      }
    } catch (e: unknown) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setActioningId(null);
    }
  };

  const handleReject = async (candidateId: number) => {
    setActioningId(candidateId);
    setActionError(null);
    try {
      const result = await rejectCandidate(candidateId);
      if (result.level === "success") {
        load();
      } else {
        setActionError(result.message);
      }
    } catch (e: unknown) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setActioningId(null);
    }
  };

  return (
    <div className="flex flex-col gap-5.5 px-4 py-5 sm:px-6 md:px-10 md:py-7.5 animate-content-in">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Агенты</h1>
        <p className="mt-1 text-[13px] text-muted-foreground">
          Рекомендации по ценам и рекламе для WB и Ozon — ничего не меняется без вашего явного подтверждения
        </p>
      </div>

      {actionError && (
        <div className="rounded-xl border border-critical/50 bg-critical-soft px-4 py-3 text-[13px] text-critical">
          {actionError}
        </div>
      )}

      <div className="text-[12px] text-muted-foreground">{data.caption}</div>

      <Button
        variant="default"
        disabled={refreshing}
        onClick={handleRefresh}
        className="w-fit"
      >
        {refreshing ? "Обновление..." : "Обновить рекомендации"}
      </Button>

      <div>
        <h2
          className="text-[14.5px] font-semibold text-foreground"
          title={data.pending_section.tooltip}
        >
          {data.pending_section.title}
        </h2>
        <p className="mt-0.5 text-[12px] text-muted-foreground">
          {data.pending_section.tooltip}
        </p>
      </div>

      {data.pending.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-2 py-10 text-center">
          <div className="text-[14.5px] font-semibold text-foreground">
            {data.pending_empty_state.title}
          </div>
          <div className="max-w-md text-[13px] leading-relaxed text-muted-foreground">
            {data.pending_empty_state.note}
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {data.pending.map((candidate) => (
            <PendingCard
              key={candidate.id}
              candidate={candidate}
              applying={actioningId === candidate.id}
              rejecting={actioningId === candidate.id}
              onApply={() => handleApply(candidate)}
              onReject={() => handleReject(candidate.id)}
            />
          ))}
        </div>
      )}

      <button
        onClick={() => setHistoryOpen(!historyOpen)}
        className="flex items-center gap-2 text-[13px] font-semibold text-muted-foreground hover:text-foreground"
      >
        <span
          className="inline-block transition-transform"
          style={{ transform: historyOpen ? "rotate(90deg)" : "rotate(0deg)" }}
        >
          ▸
        </span>
        История решений
      </button>

      {historyOpen && (
        <div className="rounded-2xl border bg-card p-4 animate-fade-in">
          {data.history.length === 0 ? (
            <div className="text-[12px] text-muted-foreground">
              {data.history_empty_note}
            </div>
          ) : (
            <div className="flex flex-col gap-1 font-mono text-[11.5px] text-text-faint">
              {data.history.map((item) => (
                <div key={item.id}>{item.text}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
