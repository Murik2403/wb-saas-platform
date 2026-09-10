export type Period = "today" | "7d" | "30d" | "90d" | "custom";

export type OverviewKpi = {
  key: string;
  hero: boolean;
  label: string;
  value: string;
  note: string;
  delta: number | null;
  spark_key: string | null;
  tone?: "warn";
};

export type FunnelStage = {
  label: string;
  value: number;
  value_text: string;
  color: string;
  width_pct: number;
};

export type ChartSeries = {
  key: string;
  name: string;
  color: string;
  dashed?: boolean;
};

export type ChartData = {
  x_key: "hour" | "day";
  series: ChartSeries[];
  rows: { x: string; [key: string]: string | number }[];
};

export type ProblemProduct = {
  status: string;
  severity: "critical" | "warn";
  name: string;
  profit: string;
  reason: string;
  action: string;
};

export type Alert = {
  priority: string;
  nm_id: string;
  signal: string;
};

export type Product = {
  nm_id: string;
  article: string;
  orders: number;
  sales: number;
  revenue: number;
  drr: number;
  stock: number;
  days_of_stock: number;
};

export type OverviewResponse = {
  period: Period;
  period_label: string;
  start: string;
  end: string;
  prev_start: string | null;
  prev_end: string | null;
  has_prev: boolean;
  kpis: OverviewKpi[];
  funnel: FunnelStage[];
  chart: ChartData;
  problem_products: ProblemProduct[];
  alerts: Alert[];
  products: Product[];
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8020";

export async function fetchOverview(
  period: Period,
  start?: string,
  end?: string
): Promise<OverviewResponse> {
  const params = new URLSearchParams({ period });
  if (period === "custom" && start && end) {
    params.set("start", start);
    params.set("end", end);
  }
  const res = await fetch(`${API_URL}/api/overview?${params}`, { cache: "no-store" });
  if (!res.ok) {
    if (res.status === 409) throw new Error("NO_DATA_YET");
    if (res.status === 422) throw new Error("INVALID_PERIOD");
    throw new Error(`API ${res.status}`);
  }
  return res.json();
}

export const PERIOD_OPTIONS: { key: Period; label: string }[] = [
  { key: "today", label: "Сегодня" },
  { key: "7d", label: "7 дней" },
  { key: "30d", label: "30 дней" },
  { key: "90d", label: "90 дней" },
  { key: "custom", label: "Свои даты" },
];

// Mirrors tenant-app/ui_helpers.py's _delta_badge_html exactly -- delta is a
// raw % change (float | None) from the API, not a pre-formatted string. Same
// +-0.05 dead zone so a rounding-noise 0.02% change doesn't paint as a trend.
export function formatDelta(delta: number | null): { text: string; tone: "good" | "bad" | "neutral" } | null {
  if (delta === null) return null;
  const tone = delta > 0.05 ? "good" : delta < -0.05 ? "bad" : "neutral";
  const arrow = delta > 0.05 ? "▲" : delta < -0.05 ? "▼" : "•";
  return {
    text: `${arrow} ${Math.abs(delta).toFixed(1)}%`,
    tone,
  };
}
