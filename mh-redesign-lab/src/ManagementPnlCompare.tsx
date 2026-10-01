import "./ManagementPnl.css";

export type PnlTone = "good" | "bad" | "neutral";
export type PnlComparison = { text: string; tone: PnlTone; hasBaseline: boolean };

const NO_BASELINE = "нет данных за прошлый период";

export function pnlMoney(value: number): string {
  return `${Math.round(value).toLocaleString("ru-RU")} ₽`;
}

function signed(value: number, text: string): string {
  return value > 0 ? `+${text}` : value < 0 ? `−${text.replace("-", "")}` : text;
}

/** Same convention as the overview badges: no baseline (missing or zero) means no percentage, never a division by zero. */
export function comparePnl(current: number | null | undefined, previous: number | null | undefined, goodWhen: "up" | "down" | "none" = "up"): PnlComparison {
  if (current == null || previous == null || !Number.isFinite(current) || !Number.isFinite(previous) || Math.abs(previous) < 1e-9) {
    return { text: NO_BASELINE, tone: "neutral", hasBaseline: false };
  }
  const diff = current - previous;
  const pct = (diff / Math.abs(previous)) * 100;
  const pctText = `${Math.abs(pct).toLocaleString("ru-RU", { maximumFractionDigits: 1 })}%`;
  const moved = Math.abs(pct) >= 0.05;
  const tone: PnlTone = !moved || goodWhen === "none" ? "neutral" : (diff > 0) === (goodWhen === "up") ? "good" : "bad";
  if (!moved) return { text: `без изменений · было ${pnlMoney(previous)}`, tone: "neutral", hasBaseline: true };
  const text = `${signed(diff, pnlMoney(Math.abs(diff)))} (${signed(diff, pctText)}) · было ${pnlMoney(previous)}`;
  return { text, tone, hasBaseline: true };
}

export function PnlDelta({ current, previous, goodWhen }: { current: number | null | undefined; previous: number | null | undefined; goodWhen?: "up" | "down" | "none" }) {
  const result = comparePnl(current, previous, goodWhen);
  return <small className={`pnl-delta ${result.tone}`}>{result.text}</small>;
}
