/** Format backend-confirmed Decimal money without a floating point conversion. */
export function previewMoney(value: string | null | undefined): string {
  if (value == null) return "не рассчитано";
  const match = /^([+-]?)([0-9]+)(?:\.([0-9]+))?(?:E([+-]?[0-9]+))?$/i.exec(value);
  if (!match) return "не рассчитано";
  const sign = match[1] === "-" ? "-" : "";
  const digits = match[2] + (match[3] ?? "");
  // Decimal permits zero with any exponent; it needs no exponent expansion.
  if (!/[1-9]/.test(digits)) return `${sign}0,00 ₽`;
  const decimalPosition = match[2].length + Number(match[4] ?? "0");
  if (!Number.isSafeInteger(decimalPosition)) return "не рассчитано";
  const significantPosition = decimalPosition - digits.search(/[1-9]/);
  if (significantPosition > 18 || significantPosition < -1) return "не рассчитано";
  const integer = (decimalPosition <= 0 ? "0" : decimalPosition >= digits.length
    ? digits + "0".repeat(decimalPosition - digits.length) : digits.slice(0, decimalPosition)).replace(/^0+(?=.)/, "");
  const fraction = decimalPosition <= 0 ? "0".repeat(-decimalPosition) + digits
    : decimalPosition >= digits.length ? "" : digits.slice(decimalPosition);
  // Confirmed source is exact kopecks. Never silently round an invalid string.
  if (/[1-9]/.test(fraction.slice(2))) return "не рассчитано";
  return `${sign}${integer.replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0")},${fraction.padEnd(2, "0").slice(0, 2)} ₽`;
}
