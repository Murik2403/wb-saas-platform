/** Presentation only: never changes source values or accounting. */
export function plural(n: number, forms: readonly [string, string, string]): string {
  const value = Math.abs(n);
  if (!Number.isInteger(value)) return forms[1];
  const hundred = value % 100;
  const ten = value % 10;
  return hundred >= 11 && hundred <= 14 ? forms[2] : ten === 1 ? forms[0] : ten >= 2 && ten <= 4 ? forms[1] : forms[2];
}

export function cabinetDate(value: string | null | undefined): string {
  if (!value) return "ещё не было";
  const match = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(value);
  // Retain the source clock: do not guess the timezone of a naive timestamp.
  return match ? `${match[3]}.${match[2]}.${match[1]} ${match[4]}:${match[5]}` : value;
}

export function cabinetText(value: string): string {
  return value.replaceAll("wb_incident_review", "Проверка инцидентов WB")
    .replace(/настройки tenant/gi, "настройки кабинета")
    .replace(/\btenant\b/gi, "кабинет");
}

export function wbAvailabilityMessage(errors: readonly string[]): string {
  return errors.some(error => /401|403|409|токен|token|не подключ/i.test(error))
    ? "Часть данных WB недоступна. Проверьте подключение и права токена в настройках."
    : "Часть данных WB временно недоступна. Попробуйте обновить страницу или проверьте журнал синхронизации.";
}

export function checkPriority(status: unknown): number {
  const text = String(status ?? "");
  return /крит/i.test(text) ? 0 : /вним/i.test(text) ? 1 : /^(готово|готов|в порядке|ok)$/i.test(text.trim()) ? 3 : 2;
}
