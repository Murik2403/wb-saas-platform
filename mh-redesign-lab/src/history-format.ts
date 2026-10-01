export function historyDate(value: string | null | undefined): string {
  if (!value) return "—";
  // Existing sync logs use Moscow wall-clock time without a timezone suffix.
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  return match ? `${match[3]}.${match[2]}.${match[1]} · ${match[4]}:${match[5]}` : "Дата неизвестна";
}

export function syncSummary(status: string, details: string | null): { title: string; counts: string; notice: string } {
  const raw = details ?? "";
  const labels: Record<string, string> = { products: "товары", orders: "заказы", sales: "продажи", financial_rows: "строки финансового отчёта", stocks: "строки остатков", ads: "строки рекламы" };
  const counts = Array.from(raw.matchAll(/(?:^|,\s*)(products|orders|sales|financial_rows|stocks|ads):\s*(\d+)(?=,|$)/g))
    .map((match) => `${labels[match[1]]}: ${Number(match[2]).toLocaleString("ru-RU")}`).join(" · ");
  const problems: string[] = [];
  if (/ads_error:|ads_skipped:|camp-api-public-cache/.test(raw)) {
    problems.push(/WB API 500/.test(raw) ? "Реклама: сервер WB вернул ошибку 500. Предыдущие данные сохранены." : "Реклама не обновилась. Предыдущие данные сохранены.");
  }
  if (/financial_error:/.test(raw)) problems.push("Финансовый отчёт не обновился.");
  if (/products_error:/.test(raw)) problems.push("Каталог товаров не обновился.");
  if (/fifo_error:|fifo_errors:\s*[1-9]/.test(raw)) problems.push("При учёте движения товаров возникли ошибки. Проверьте раздел «Контроль».");
  return {
    title: status === "running" && /^progress:\s*\d+\/\d+/.test(raw) ? `Загружаем данные WB: ${raw.match(/^progress:\s*(\d+)\/(\d+)/)!.slice(1, 3).join(" из ")} этапов` : status === "success" ? "Обновление завершено" : status === "partial" ? "Обновлено частично" : status === "running" ? "Загружаем данные WB" : "Не удалось завершить обновление",
    counts,
    notice: problems.join(" ") || (status === "error" ? "Подробности сбоя доступны ниже." : status === "partial" ? "Некоторые этапы не завершены. Подробности ниже." : ""),
  };
}
