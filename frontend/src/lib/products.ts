import type { Period } from "@/lib/overview";

export type { Period };

// One row of tenant-app/calculations.py's `products` DataFrame, renamed by
// products_service.PRODUCT_COLUMNS. Verified against the real JSON, not just
// assumed: every numeric field serializes as a JSON number, and
// `days_of_stock` is null whenever there were no sales in the window (NaN in
// pandas -> None in json_safe) -- for period=today that is *every* row, so
// anything calling .toFixed() on it unguarded crashes on first paint.
export type ProductRow = {
  nm_id: number;
  article: string;
  product: string;
  brand: string;
  orders: number;
  sales: number;
  revenue: number;
  buyout: number;
  ad_spend: number;
  drr: number;
  stock: number;
  daily_sales: number;
  days_of_stock: number | null;
  unit_cost: number;
  margin: number;
};

export type ProductsResponse = {
  period: Period;
  period_label: string;
  start: string;
  end: string;
  products: ProductRow[];
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8020";

export async function fetchProducts(
  period: Period,
  start?: string,
  end?: string
): Promise<ProductsResponse> {
  const params = new URLSearchParams({ period });
  if (period === "custom" && start && end) {
    params.set("start", start);
    params.set("end", end);
  }
  const res = await fetch(`${API_URL}/api/products?${params}`, { cache: "no-store" });
  if (!res.ok) {
    if (res.status === 409) throw new Error("NO_DATA_YET");
    if (res.status === 422) throw new Error("INVALID_PERIOD");
    throw new Error(`API ${res.status}`);
  }
  return res.json();
}

// The four number formats pages/products.py declares in its column_config,
// plus a plain fallback for the columns it leaves unformatted (Заказы /
// Продажи / Остаток). All null-tolerant: a NaN cell arrives as null.
export function money0(value: number | null): string {
  if (value === null) return "—";
  return `${value.toLocaleString("ru-RU", { maximumFractionDigits: 0 })} ₽`;
}

export function pct1(value: number | null): string {
  return value === null ? "—" : `${value.toFixed(1)}%`;
}

export function dec2(value: number | null): string {
  return value === null ? "—" : value.toFixed(2);
}

export function dec1(value: number | null): string {
  return value === null ? "—" : value.toFixed(1);
}

export function plain(value: number | null): string {
  if (value === null) return "—";
  return value.toLocaleString("ru-RU", { maximumFractionDigits: 2 });
}
