# PDF metric snapshot copying

Base: ec13bc1. Scope: report_worker only; financial formulas, period reads, memory/slot limits and production are untouched.

The worker cached a DashboardData snapshot but eagerly deep-copied all nine DataFrames for every metric, including frames that metric never used. With pandas 3 (the locked runtime), shallow DataFrame copies provide copy-on-write isolation. The new helper supplies these independent frame wrappers to deepcopy through its memo; nested dictionaries are still copied recursively. pandas 2 keeps the original eager-copy behavior; no global pandas options change.

Regression tests alter numeric cells, string cells, columns and nested metadata in every dashboard field, then verify the original and a second metric remain unchanged. They also assert numeric buffers really are shared before writing and the compatibility path eagerly copies.

A fresh-process synthetic measurement (`python scripts/profile_pdf_snapshot_copy.py --out docs/benchmarks/pdf-snapshot-copy-2026-10-19.json`) uses nine distinct frames with 1,000,000 float64 rows each. Eager copy grows process peak by 54,768 KiB; copy-on-write grows it by 0 KiB at the copy boundary. Both isolate a subsequent write. This is a snapshot-copy measurement, not a complete PDF benchmark, a container peak, or proof that the 243 MiB database fits 768 MiB.

The dashboard-build peak occurs before copying, so this change cannot alone solve the largest-database OOM. Full container acceptance and 20/25/30 on the final combined candidate remain separate.

Verification: 127 report tests PASS, then 3 fresh-process worker equivalence tests PASS (105.94 s) with a dedicated pytest basetemp: all 21 PNG hashes/title/summary match the per-metric baseline, full PDF text/page count match the in-process renderer, failing metric is reported. An earlier parallel run overlapped pytest temporary directories and failed after its first test; isolated rerun above passes. New isolation tests were first run red before adding the helper. `git diff --check` and compileall pass. `graphify update .` completed (code AST only). No deployment.

## Codex follow-up: full large-PDF acceptance

The later code through ae4dc15 completes the full 21-metric PDF on this 243 MiB synthetic database at 30 and 90 days under 768 MiB with no swap, no pressure/OOM events. Details, limits of the claim and preserved failures: [LARGE_PDF_OOM_ACCEPTANCE_2026-10-19.md](LARGE_PDF_OOM_ACCEPTANCE_2026-10-19.md). This supersedes the open full-PDF OOM status above for this specific scenario; the original measurements remain historical.
