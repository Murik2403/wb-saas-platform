export type ControlKpi = {
  key: string;
  label: string;
  value: string;
  note: string;
};

export type ControlBanner = {
  tone: "good" | "warn";
  message: string;
};

export type FifoGuard = {
  tone: "good" | "info" | "warn";
  status: string;
  message: string;
};

export type ReliabilityRow = {
  module: string;
  reliability: string;
  basis: string;
};

export type CheckRow = {
  module: string;
  check: string;
  status: string;
  detail: string;
  action: string;
};

export type CostRow = {
  nm_id: string | number;
  article: string;
  product: string;
  source: string;
  rate: number | null;
  match: string;
  status: string;
};

export type IssueRow = {
  priority: string;
  module: string;
  check: string;
  detail: string;
  action: string;
};

export type ControlResponse = {
  title: string;
  subtitle: string;
  kpis: ControlKpi[];
  summary: { score: number; critical: number; waiting: number; warnings: number; ready: number; optional: number };
  banner: ControlBanner;
  optional_note: string;
  fifo_guard: FifoGuard;
  reliability: {
    rows: ReliabilityRow[];
    caption: string;
  };
  all_checks: {
    rows: CheckRow[];
    cost_quality: {
      title: string;
      empty_note: string;
      ok_note: string;
      issue_count: number;
      rows: CostRow[];
    };
  };
  todo: {
    empty_note: string;
    issues: IssueRow[];
    cost_issues: {
      title: string;
      note: string;
      caption: string;
      rows: CostRow[];
    };
    optional: {
      title: string;
      subtitle: string;
      rows: CheckRow[];
    };
  };
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8020";

export async function fetchControl(): Promise<ControlResponse> {
  const res = await fetch(`${API_URL}/api/control`, { cache: "no-store" });
  if (!res.ok) {
    if (res.status === 409) throw new Error("NO_DATA_YET");
    throw new Error(`API ${res.status}`);
  }
  return res.json();
}

export async function fifoRecheck(): Promise<{ message: string }> {
  const res = await fetch(`${API_URL}/api/control/fifo-recheck`, { method: "POST" });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `API ${res.status}`);
  }
  return res.json();
}

// CSV download URLs — browser handles the download directly
export function qualityCsvUrl(): string {
  return `${API_URL}/api/control/quality.csv`;
}

export function costQualityCsvUrl(): string {
  return `${API_URL}/api/control/cost-quality.csv`;
}
