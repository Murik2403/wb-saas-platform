export type PendingCandidate = {
  id: number;
  created_at: string;
  agent: string;
  marketplace: string;
  target_id: string;
  target_name: string | null;
  action: string;
  current_value: unknown;
  proposed_value: unknown;
  current_display: string;
  proposed_display: string;
  reason: string;
  status: string;
  can_apply: boolean;
};

export type HistoryItem = {
  id: number;
  status: string;
  text: string;
};

export type AgentsResponse = {
  connected: string[];
  caption: string;
  cost_count: number;
  empty_state: { title: string; note: string } | null;
  pending_section: {
    title: string;
    tooltip: string;
  };
  pending_empty_state: { title: string; note: string };
  pending: PendingCandidate[];
  history: HistoryItem[];
  history_empty_note: string;
};

export type RefreshResponse = {
  level: "success" | "error";
  message: string;
  warnings?: string[];
};

export type ApplyResponse = {
  level: "success" | "error";
  message: string;
};

export type RejectResponse = {
  level: "success" | "error";
  message: string;
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8020";

export async function fetchAgents(): Promise<AgentsResponse> {
  const res = await fetch(`${API_URL}/api/agents`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`API ${res.status}`);
  }
  return res.json();
}

export async function refreshRecommendations(): Promise<RefreshResponse> {
  const res = await fetch(`${API_URL}/api/agents/refresh`, {
    method: "POST",
    cache: "no-store",
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail);
  }
  return res.json();
}

export async function applyCandidate(candidateId: number): Promise<ApplyResponse> {
  const res = await fetch(`${API_URL}/api/agents/candidates/${candidateId}/apply`, {
    method: "POST",
    cache: "no-store",
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail);
  }
  return res.json();
}

export async function rejectCandidate(candidateId: number): Promise<RejectResponse> {
  const res = await fetch(`${API_URL}/api/agents/candidates/${candidateId}/reject`, {
    method: "POST",
    cache: "no-store",
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail);
  }
  return res.json();
}