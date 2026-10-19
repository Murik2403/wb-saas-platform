# Large PDF OOM implementation plan

**Goal:** Full 21-metric PDF on the existing 243 MiB synthetic database with cgroup memory.max=768 MiB and swap disabled.
**Evidence:** 43148f8 full PDF fails at 768 MiB, memory.events oom_kill=1. Diagnostic build_dashboard trace identifies concurrent orders/sales frames and financial read as first peak. Diagnostic 2 GiB run is for locating the peak, never acceptance.
**Architecture:** Defer the financial read until the operational frames have been consumed and released. Preserve all reads, row order, period/date parsing, financial formulas and FIFO provenance. Reduce other overlapping working sets only if the unchanged acceptance still fails.

- [ ] Add regression for read lifetime and exact DashboardData equivalence on 1/7/30/90 days against the preceding committed function.
- [ ] Defer the financial read; run regression and financial/FIFO suites.
- [ ] Build immutable committed overlay, run full PDF under 768 MiB with no swap, record cgroup peak/events, all 21 sections and parseable PDF.
- [ ] Test a 90-day full PDF as well; retain failures, verify cleanup and untouched foreign containers, commit evidence.
