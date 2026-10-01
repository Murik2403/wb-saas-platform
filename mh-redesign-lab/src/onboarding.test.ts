import { describe, expect, it } from "vitest";
import { onboardingEvidence, syncProgress } from "./onboarding";

const run = { id: 1, started_at: "2026-09-18", finished_at: "2026-09-18", status: "success", details: "orders: 2, sales: 1, financial_rows: 1" };
const cost = { nm_id: 123, supplier_article: "sku", product_name: "Товар", cost_per_wb_unit: 420, note: "", updated_at: "2026-09-18" };

describe("onboarding evidence", () => {
  it.each(["error", "partial", "running"])("does not finish import for %s", (status) => {
    expect(onboardingEvidence([{ ...run, status }], [cost], 123).syncComplete).toBe(false);
  });
  it("requires a full import, not an ads refresh", () => {
    expect(onboardingEvidence([{ ...run, details: "scope: ads, ads: 3" }], [cost], 123).syncComplete).toBe(false);
  });
  it("does not equate a catalogue row, another SKU or an unsaved cost with completed cost", () => {
    expect(onboardingEvidence([run], [{ ...cost, cost_per_wb_unit: 0 }], 123).costComplete).toBe(false);
    expect(onboardingEvidence([run], [cost], 456).costComplete).toBe(false);
    expect(onboardingEvidence([run], [{ ...cost, updated_at: "" }], 123).costComplete).toBe(false);
  });
  it("requires a finite positive saved cost for the selected SKU", () => {
    expect(onboardingEvidence([run], [{ ...cost, cost_per_wb_unit: Infinity }], 123).costComplete).toBe(false);
    expect(onboardingEvidence([run], [cost], 123)).toMatchObject({ syncComplete: true, costComplete: true });
  });
  it("does not hide a recent failed full import behind an earlier success", () => {
    expect(onboardingEvidence([{ ...run, id: 2, status: "error" }, run], [cost], 123).syncComplete).toBe(false);
  });
});

describe("sync progress", () => {
  it("reads the backend's live progress and names the stage in progress", () => {
    expect(syncProgress("progress: 3/7 sales")).toEqual({ done: 3, total: 7, percent: 43, text: "Выполнено 3 из 7 этапов. Сейчас: финансовый отчёт." });
    expect(syncProgress("progress: 7/7 ads")?.text).toBe("Выполнено 7 из 7 этапов. Завершаем загрузку…");
  });
  it("ignores finished-run details and anything that is not progress", () => {
    for (const details of ["orders: 2, sales: 1, financial_rows: 1", "scope: ads, ads: 3", "", null, undefined, "orders: 1, progress: 2/7 orders"]) {
      expect(syncProgress(details)).toBeNull();
    }
  });
  it("never reports more than the total", () => {
    expect(syncProgress("progress: 9/7 ads")).toMatchObject({ done: 7, total: 7, percent: 100 });
  });
});

describe("sync history during a load", () => {
  it("shows stage progress in the run title instead of a bare 'loading'", async () => {
    const { syncSummary } = await import("./history-format");
    expect(syncSummary("running", "progress: 3/7 sales").title).toBe("Загружаем данные WB: 3 из 7 этапов");
    expect(syncSummary("running", null).title).toBe("Загружаем данные WB");
    expect(syncSummary("success", "orders: 2, sales: 1, financial_rows: 1").title).toBe("Обновление завершено");
  });
});
