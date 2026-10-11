// Days in the selected period (custom: both ends inclusive); null when it cannot be told.
export function periodLengthDays(period: { id: string; start?: string; end?: string }): number | null {
  const fixed: Record<string, number> = { today: 1, "7d": 7, "30d": 30, "90d": 90 };
  if (period.id in fixed) return fixed[period.id];
  if (period.id !== "custom" || !period.start || !period.end) return null;
  const days = (Date.parse(period.end) - Date.parse(period.start)) / 86_400_000 + 1;
  return Number.isFinite(days) && days >= 1 ? Math.round(days) : null;
}
