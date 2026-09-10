export type TodayKpi = {
  key: string;
  label: string;
  value: string;
  note: string;
  tone?: "warn" | "default";
};

export type TodaySignal = {
  priority: string;
  type: string;
  article: string;
  signal: string;
  action: string;
};

export type TodayResponse = {
  today: string;
  kpis: TodayKpi[];
  alert_count: number;
  sync_stale: boolean;
  signals: TodaySignal[];
  tabs: {
    shift: {
      date: string | null;
      material_covered: number;
      planned: number;
      tasks: Record<string, unknown>[];
      blocked_by_material: Record<string, unknown>[];
    };
    dispatch: {
      unposted: Record<string, unknown>[];
      pending_receipt: Record<string, unknown>[];
    };
    material: {
      rows: Record<string, unknown>[];
      shortages: boolean;
      unknown: boolean;
    };
    procurement: {
      material_to_buy: Record<string, unknown>[];
      products_to_buy: Record<string, unknown>[];
      active_orders: Record<string, unknown>[];
    };
    stock: { emergency_days: number; rows: Record<string, unknown>[] };
    overdue: Record<string, unknown>[];
  };
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8020";

export async function fetchToday(): Promise<TodayResponse> {
  const res = await fetch(`${API_URL}/api/today`, { cache: "no-store" });
  if (!res.ok) {
    if (res.status === 409) throw new Error("NO_DATA_YET");
    throw new Error(`API ${res.status}`);
  }
  return res.json();
}

export function priorityTone(priority: string): "critical" | "warn" | "neutral" {
  if (priority === "Критично" || priority === "Высокий") return "critical";
  if (priority === "Средний") return "warn";
  return "neutral";
}
