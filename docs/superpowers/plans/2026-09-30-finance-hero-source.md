# Finance hero source consistency

Actual synthetic browser reproduction: ALPHA financial report profit80 (report_rows1, current-cost estimated COGS10), management accounting_profit80; operational /api/overview kpis=[] because no operational sales facts are seeded. Hero currently displays fallback0 from overview metrics, contradicting the selected-period financial report.

Bounded fix: preserve actual selected-period /dashboard/finance financial.profit and report_rows in FinanceReportState. Render the finance hero from that realization report only, with explicit WB financial-result label and existing COGS preliminary quality. Missing/no-report/nonfinite/null profit remains unknown, not zero; a genuine report profit0 remains0. Distinguish additional manually confirmed expense-adjusted management result shown below. Do not mutate financial formulas or synthesize operational sales.

Add adapter and component regressions for ALPHA80/BETA60, report-row missing, legitimate0, null/nonfinite, estimated/exact quality. Run focused/full frontend and build. Independent reviewer approval before commit/integration. ROOT source/active browser fixture unchanged during implementation.
