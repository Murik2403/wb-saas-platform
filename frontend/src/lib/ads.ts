export type Period = "today" | "7d" | "30d" | "90d" | "custom";

export type AdsKpi = {
  key: string;
  label: string;
  value: string;
  note: string;
};

export type Campaign = {
  campaign: string | number;
  spend: number;
  views: number;
  clicks: number;
  orders: number;
  ad_revenue: number;
  ctr: number;
  cpc: number;
  drr: number;
};

export type AdsResponse = {
  period: Period;
  period_label: string;
  start: string;
  end: string;
  kpis: AdsKpi[];
  kpi_note: string;
  empty_state: {
    title: string;
    note: string;
  };
  campaigns: Campaign[];
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8020";

export async function fetchAds(
  period: Period,
  start?: string,
  end?: string
): Promise<AdsResponse> {
  const params = new URLSearchParams({ period });
  if (period === "custom" && start && end) {
    params.set("start", start);
    params.set("end", end);
  }
  const res = await fetch(`${API_URL}/api/ads?${params}`, { cache: "no-store" });
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
