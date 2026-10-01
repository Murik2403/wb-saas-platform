import type { DashboardMetric } from "./contracts";

type Change = NonNullable<DashboardMetric["change"]>;

const nf = (digits: number) => new Intl.NumberFormat("ru-RU", { maximumFractionDigits: digits });
const signed = (n: number, digits = 0) => `${n > 0 ? "+" : n < 0 ? "−" : ""}${nf(digits).format(Math.abs(n))}`;
export const NO_PREVIOUS = "нет данных за прошлый период";

function tone(diff: number, higherIsBetter: boolean): Change["tone"] {
  if (diff === 0) return "neutral";
  return diff > 0 === higherIsBetter ? "good" : "bad";
}

/** Absolute + % change. Never divides by zero: previous <= 0 gives NO_PREVIOUS. */
export function compareAmount(current: number, previous: number | null | undefined, unit: string, higherIsBetter = true): Change {
  if (previous == null || !(previous > 0)) return { text: NO_PREVIOUS, tone: "neutral" };
  const diff = current - previous;
  const pct = (diff / previous) * 100;
  return { text: `${signed(diff)}${unit ? ` ${unit}` : ""} (${signed(pct, 1)}%) к прошлому периоду`, tone: tone(diff, higherIsBetter) };
}

/** Percentage-point change of a ratio (margin). */
export function comparePoints(current: number, previous: number | null | undefined): Change {
  if (previous == null) return { text: NO_PREVIOUS, tone: "neutral" };
  const diff = current - previous;
  if (Math.round(diff * 10) === 0) return { text: "без изменений к прошлому периоду", tone: "neutral" };
  return { text: `${signed(diff, 1)} п.п. к прошлому периоду`, tone: tone(Math.round(diff * 10) / 10, true) };
}

/** Change of a count (SKU); higherIsBetter null = no judgement. */
export function compareCount(current: number, previous: number | null | undefined, unit: string, higherIsBetter: boolean | null): Change {
  if (previous == null) return { text: NO_PREVIOUS, tone: "neutral" };
  const diff = current - previous;
  if (diff === 0) return { text: "без изменений к прошлому периоду", tone: "neutral" };
  return { text: `${signed(diff)}${unit ? ` ${unit}` : ""} к прошлому периоду`, tone: higherIsBetter === null ? "neutral" : tone(diff, higherIsBetter) };
}
