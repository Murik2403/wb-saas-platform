import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AdsAnalyticsPanel, adsDelta, fmtAds } from "./AdsAnalyticsPanel";

const m = (o: Record<string, number | null>) => ({
  spend: 550, views: 3500, clicks: 150, ctr: 4.29, cpc: 3.67, cpm: 157.14, carts: 30, cart_conversion: 20, orders: 15,
  order_conversion: 10, cpo: 36.67, ad_revenue: 4000, drr: 13.75, canceled: 3, ...o,
});
const payload = (extra: Record<string, unknown> = {}) => ({
  start: "2026-09-01", end: "2026-09-02", previous_start: "2026-08-30", previous_end: "2026-08-31", bucket_days: 1,
  has_data: true, has_previous_data: true,
  totals: m({}), previous_totals: m({ spend: 500, ctr: 3.82, cpc: 4, cpo: null }),
  by_day: [{ day: "2026-09-01", ...m({ spend: 200 }) }, { day: "2026-09-02", ...m({ spend: 350 }) }],
  campaigns: [{ campaign_id: 77, ...m({}) }], ...extra,
});
const period = { id: "7d" as const };
const mockFetch = (body: unknown, ok = true) => vi.stubGlobal("fetch", vi.fn(async () => ({ ok, status: ok ? 200 : 500, json: async () => body, text: async () => JSON.stringify(body) })));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("ads analytics helpers", () => {
  it("delta is relative and null without previous", () => {
    expect(adsDelta(550, 500)).toBeCloseTo(10);
    expect(adsDelta(5, 0)).toBeNull();
    expect(adsDelta(5, null)).toBeNull();
    expect(adsDelta(null, 5)).toBeNull();
  });
  it("null shows a dash", () => {
    expect(fmtAds(null, "money")).toBe("—");
    expect(fmtAds(Number.NaN, "pct")).toBe("—");
  });
});

describe("AdsAnalyticsPanel", () => {
  it("renders KPI deltas with arrows, signs and tones", async () => {
    mockFetch(payload());
    render(<AdsAnalyticsPanel period={period} />);
    await screen.findByText("Эффективность рекламы"); await screen.findByText("По дням");
    expect(screen.getByText(/▲ \+10,0%/)).toBeTruthy(); // spend 500 -> 550
    expect(screen.getByText(/▲ \+12,3%/)).toBeTruthy(); // ctr 3.82 -> 4.29
    expect(screen.getAllByText("нет данных за прошлый период").length).toBeGreaterThan(0); // cpo previous null
    expect(screen.getByText(/ДРР по рекламным заказам/, { selector: "dt" })).toBeTruthy();
    expect(screen.getByText(/зонам показа/)).toBeTruthy();
  });
  it("sorts by-day table", async () => {
    mockFetch(payload());
    render(<AdsAnalyticsPanel period={period} />);
    await screen.findByText("По дням");
    const days = () => screen.getAllByRole("rowheader").map((c) => c.textContent).filter((t) => /^\d\d\.\d\d\.\d{4}$/.test(t ?? ""));
    expect(days()).toEqual(["01.09.2026", "02.09.2026"]);
    await userEvent.click(screen.getByRole("button", { name: /День/ }));
    expect(days()).toEqual(["02.09.2026", "01.09.2026"]);
  });
  it("shows empty and error states", async () => {
    mockFetch(payload({ has_data: false }));
    render(<AdsAnalyticsPanel period={period} />);
    await screen.findByText(/статистики нет/);
    cleanup();
    mockFetch({}, false);
    render(<AdsAnalyticsPanel period={period} />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
  });
});
