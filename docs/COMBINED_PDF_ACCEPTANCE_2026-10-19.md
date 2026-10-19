# Combined SQL-period / bounded-memory PDF acceptance — 2026-10-19

Runtime: **771f6100a624aced20a959e73ae568c8b9b0c7ef**, branch `codex/pdf-snapshot-memory-20261019`. Merge of d315ee4 and the earlier verified Codex memory changes, with ISO/snapshot correctness fixes.
Image: `sha256:45caab7db2860cb92ac25393523139909f51ae09aa021d59a3a71f3ca38c9445` (`bench-tenant:codex-period-guard`). Built from clean committed sources, unchanged dependencies, no network, verified inherited runtime configuration.

## Results

Synthetic database: 254,914,560 bytes (243 MiB), 150 demo copies; not a production backup. Online SQLite backup into a disposable directory; no network or published ports. Full PDF parent plus the real report worker, all 21 metrics, 11 PDF pages. **768 MiB cgroup, no swap, 2 CPUs.** No resident API/nginx/background sync in this cgroup; this is not whole-tenant or 20–30-tenant capacity acceptance.

| Days | Seconds | memory.peak | Verdict |
|---|---:|---:|---|
| 1 | 34.87 s | 210.29 MiB | PASS |
| 7 | 42.78 s | 257.39 MiB | PASS |
| 30 | 80.52 s | 446.07 MiB | PASS |
| 90 | 104.57 s | 700.16 MiB | PASS |

All runs: exit 0, OOMKilled=false, zero high/max/oom/oom_kill deltas, unchanged memory/swap settings, PDF SHA-256 verified, exact created container removed and absence confirmed. Timings include this host's concurrent local activity, are not an SLA and do not demonstrate a speed improvement. Compared with the previous ae4dc15 act, 30-day peak falls from 686.30 to 446.07 MiB; 90 days remains close to the limit (700.16 MiB).

## Correctness review and fixes

The original d315ee4 ISO guard accepted impossible calendar dates and `.123Z`, `.123+03:00`, `.1garbage` suffixes. In a reproduced case an invalid newest stock date changed dashboard stock from 9 to 0. Different fractional widths can also make SQL MAX discard rows representing equal timestamps. Reject these shapes, unsafe timestamp bounds and invalid clock/calendar values, falling back to whole-table parsing. Keep the guard and filtered read in one explicit SQLite read transaction, tested with a concurrent committed WAL insert. Preserve the deferred financial read, compact protected FIFO semantics, copy-on-write report snapshots and worker-only allocator cleanup from the previous candidate.

The integration suite before the final read-transaction refinement: **1366 passed, 1 skipped, 75 subtests**. After that refinement, the complete period-read regression file passed **12 tests / 9 subtests**, including all 21 metric titles/summaries/PNG hashes across 1/7/30/90 and empty periods. Final API suite: **687 passed**. No financial formulas or memory/slot limits changed.

Large-database DashboardData exact comparison against 43148f8: all 11 fields, values/dtypes and metadata identical at 1/7/30/90 days. The final snapshot refinement changes only read consistency; the same source database is static. Completed PDFs at 30/90 days have identical text/pages to ae4dc15 after removing only the generation timestamp from the 11 standard footers; no other differences were normalized.

## Evidence and repetition

Raw acts and SHA-256 manifest: `benchmarks/large-pdf-period-2026-10-19/`. Earlier OOM/pressure failures and Claude's original acts are preserved unchanged. PDF binaries remain outside Git under `C:/Users/mvvor/bm-tmp/codex-large-pdf-20261019/combined-<days>.pdf`.

```powershell
python scripts/build_bench_overlay.py --base bench-tenant:close-ed0748f --tag bench-tenant:codex-period-guard
python scripts/bench_large_pdf.py --image bench-tenant:codex-period-guard --database C:/Users/mvvor/bm-tmp/bigdata/wb_dashboard.sqlite3 --days 30 --out <new-act-path>.json
```

Repeat the last command for 1, 7 and 90 days. Tests run from tenant-app with independent --basetemp; API tests run from the repository root. Graphify code index updated. No production access, deployment, credentials, public text changes, or 20/25/30 runs were performed for this candidate.
