import type { ProductCostSetting, TenantSyncRun } from "./data-source";

export function onboardingEvidence(runs: TenantSyncRun[], costs: ProductCostSetting[], nmId: number | null) {
  const latest = [...runs].sort((a, b) => b.id - a.id).find((run) => !/^scope:\s*(ads|finance)/.test(run.details ?? ""));
  const syncComplete = Boolean(latest?.status === "success" && latest.finished_at &&
    ["orders", "sales", "financial_rows"].every((key) => new RegExp(`(?:^|,\\s*)${key}:\\s*\\d+`).test(latest.details ?? "")));
  const selected = costs.find((cost) => cost.nm_id === nmId);
  const costComplete = Boolean(selected?.updated_at && Number.isFinite(selected.cost_per_wb_unit) && selected.cost_per_wb_unit > 0);
  const syncMessage = syncComplete ? "История магазина успешно загружена"
    : latest?.status === "error" ? "Последняя загрузка завершилась с ошибкой. Проверьте журнал и права WB, затем повторите."
      : latest?.status === "partial" ? "Загрузка частичная. Проверьте недоступные разделы в журнале и повторите полную загрузку."
        : latest?.status === "running" ? "Загрузка ещё идёт. Дождитесь результата."
          : "Запустите полную загрузку истории магазина";
  return { latest, syncComplete, costComplete, selected, syncMessage };
}
