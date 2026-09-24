import { afterEach, describe, expect, it, vi } from "vitest";

import { getFinanceFifoReconciliationPage } from "./data-source";

describe("finance FIFO detail page adapter", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("requests the selected period and maps only the paged diagnostic response", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        period: { start: "2026-09-01", end: "2026-09-19" },
        total: 31,
        offset: 25,
        limit: 25,
        rows: [{
          rrd_id: 91, operation_date: "2026-09-10", sale_date: "2026-09-09", nm_id: 123,
          supplier_article: "SKU-A", srid: "srid-1", event_type: "return", units: 2,
          status: "exact", reason: "", eligible_fifo_cogs_rub: -84,
          observed_fifo_cost_rub: 84, layer_sources: ["production"],
        }],
      }),
    });
    vi.stubGlobal("fetch", fetch);
    const controller = new AbortController();

    const page = await getFinanceFifoReconciliationPage(
      { id: "custom", start: "2026-09-01", end: "2026-09-19" }, 25, 25, controller.signal,
    );

    const [url] = fetch.mock.calls[0] as [string];
    expect(url).toContain("/api/v1/dashboard/finance/fifo-reconciliation?");
    expect(url).toContain("period=custom");
    expect(url).toContain("start=2026-09-01");
    expect(url).toContain("end=2026-09-19");
    expect(url).toContain("offset=25");
    expect(page).toEqual({
      period: { start: "2026-09-01", end: "2026-09-19" },
      total: 31, offset: 25, limit: 25,
      rows: [{
        rrdId: 91, operationDate: "2026-09-10", saleDate: "2026-09-09", nmId: 123,
        supplierArticle: "SKU-A", srid: "srid-1", eventType: "return", units: 2,
        status: "exact", reason: "", eligibleCostRub: -84, observedCostRub: 84,
        layerSources: ["production"],
      }],
    });
  });
});
