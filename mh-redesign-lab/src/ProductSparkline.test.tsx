import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, renderHook, screen, waitFor } from "@testing-library/react";
import { ProductSparkline } from "./ProductSparkline";
import { useProductsTrend } from "./useProductsTrend";
import { compareAmount, compareCount, comparePoints, NO_PREVIOUS } from "./productsDelta";
import * as ds from "./data-source";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const nb = (s: string) => s.replace(/ | /g, " ");

describe("ProductSparkline", () => {
  it("scales min..max into the box and describes the trend in aria-label", () => {
    render(<ProductSparkline values={[0, 100, 50]} width={100} height={20} />);
    const svg = screen.getByRole("img");
    expect(nb(svg.getAttribute("aria-label")!)).toBe("Выручка за период: от 0 ₽ до 100 ₽, итого 150 ₽");
    // pad=2: x 2,50,98 ; y: min->18, max->2, 50->10
    expect(svg.querySelector("polyline")!.getAttribute("points")).toBe("2.0,18.0 50.0,2.0 98.0,10.0");
  });
  it("draws flat and all-zero series as a middle line without NaN", () => {
    const { container, rerender } = render(<ProductSparkline values={[5, 5, 5]} height={20} />);
    expect(container.querySelector("polyline")!.getAttribute("points")).not.toMatch(/NaN/);
    rerender(<ProductSparkline values={[0, 0, 0]} height={20} />);
    expect(container.querySelector("polyline")!.getAttribute("points")).not.toMatch(/NaN/);
    expect(nb(screen.getByRole("img").getAttribute("aria-label")!)).toContain("без изменений, итого 0 ₽");
  });
  it("handles a single point and empty input", () => {
    const { container, rerender } = render(<ProductSparkline values={[7]} />);
    expect(container.querySelector("polyline")!.getAttribute("points")).not.toMatch(/NaN/);
    rerender(<ProductSparkline values={[]} />);
    expect(screen.queryByRole("img")).toBeNull();
  });
});

describe("compare helpers", () => {
  it("revenue up is good, down is bad, with absolute and percent", () => {
    expect(nb(compareAmount(1200, 1000, "₽").text)).toBe("+200 ₽ (+20%) к прошлому периоду");
    expect(compareAmount(1200, 1000, "₽").tone).toBe("good");
    expect(nb(compareAmount(750, 1000, "₽").text)).toBe("−250 ₽ (−25%) к прошлому периоду");
    expect(compareAmount(750, 1000, "₽").tone).toBe("bad");
  });
  it("never divides by zero", () => {
    for (const prev of [0, null, undefined, -5]) expect(compareAmount(100, prev, "₽")).toEqual({ text: NO_PREVIOUS, tone: "neutral" });
    expect(comparePoints(10, null).text).toBe(NO_PREVIOUS);
    expect(compareCount(2, undefined, "SKU", false).text).toBe(NO_PREVIOUS);
  });
  it("margin points and risk polarity", () => {
    expect(comparePoints(12.5, 10)).toEqual({ text: "+2,5 п.п. к прошлому периоду", tone: "good" });
    expect(comparePoints(10, 10)).toEqual({ text: "без изменений к прошлому периоду", tone: "neutral" });
    expect(compareCount(5, 3, "SKU", false)).toEqual({ text: "+2 SKU к прошлому периоду", tone: "bad" });
    expect(compareCount(3, 3, "SKU", false).text).toBe("без изменений к прошлому периоду");
    expect(compareCount(4, 0, "", null).tone).toBe("neutral"); // zero previous count is a real value
  });
});

describe("useProductsTrend", () => {
  const trend = (n: number) => ({ start: "", end: "", bucketDays: 1, dates: [], series: { "1": { revenue: [n], units: [n] } } });
  it("ignores a stale response when the period changes", async () => {
    let resolveOld!: (v: ds.ProductsTrend) => void;
    const spy = vi.spyOn(ds, "getProductsTrend")
      .mockImplementationOnce(() => new Promise((r) => { resolveOld = r; }))
      .mockImplementationOnce(async () => trend(2));
    const { result, rerender } = renderHook(({ id }) => useProductsTrend({ id }), { initialProps: { id: "7d" as ds.DashboardPeriodId } });
    expect(result.current.loading).toBe(true);
    rerender({ id: "30d" });
    await waitFor(() => expect(result.current.data?.series["1"].revenue).toEqual([2]));
    resolveOld(trend(1)); // late answer for the old period
    await new Promise((r) => setTimeout(r, 10));
    expect(result.current.data?.series["1"].revenue).toEqual([2]);
    expect(spy).toHaveBeenCalledTimes(2);
  });
  it("exposes errors without data", async () => {
    vi.spyOn(ds, "getProductsTrend").mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useProductsTrend({ id: "30d" }));
    await waitFor(() => expect(result.current.error).toBe("boom"));
    expect(result.current.data).toBeNull();
    expect(result.current.loading).toBe(false);
  });
});
