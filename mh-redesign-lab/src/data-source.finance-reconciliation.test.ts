import { describe, expect, it } from "vitest";

import { adaptFinanceReport } from "./data-source";
import type { FinanceReportState } from "./contracts";

describe("finance FIFO reconciliation adapter", () => {
  it("keeps operation-date reconciliation separate from sale-date FIFO costs", () => {
    const fallback = { rows: [] } as FinanceReportState;
    const report = adaptFinanceReport({
      fifo_cogs_period_basis: "sale_date",
      fifo_reconciliation: {
        sale_return_rows: 4,
        exact_rows: 2,
        sale_return_units: 6,
        exact_units: 3,
        exact_unit_coverage_percent: 50,
        exact_fifo_cogs_rub: 145.5,
        status_counts: { exact: 2, synthetic: 1, unmatched: 1 },
      },
    }, fallback);

    expect(report.fifoPeriodBasis).toBe("sale_date");
    expect(report.fifoReconciliation).toEqual({
      saleReturnRows: 4,
      exactRows: 2,
      saleReturnUnits: 6,
      exactUnits: 3,
      exactUnitCoveragePercent: 50,
      exactFifoCogs: 145.5,
      statusCounts: { exact: 2, synthetic: 1, unmatched: 1 },
    });
  });

  it("adapts the hybrid P&L evidence and keeps incomplete COGS explicit", () => {
    const report = adaptFinanceReport({
      financial: {
        exact_fifo_cogs_rub: 100,
        current_cost_estimate_rub: 30,
        exact_units: 1,
        estimated_units: 1,
        insufficient_cogs_units: 2,
        sale_return_units: 4,
        exact_unit_coverage_percent: 25,
        cogs_quality: "insufficient",
        cogs_complete: false,
      },
    }, { rows: [] } as FinanceReportState);

    expect(report.cogsEvidence).toEqual({
      exactFifoRub: 100,
      currentEstimateRub: 30,
      exactUnits: 1,
      estimatedUnits: 1,
      insufficientUnits: 2,
      totalUnits: 4,
      exactUnitCoveragePercent: 25,
      quality: "insufficient",
      complete: false,
    });
  });
});
