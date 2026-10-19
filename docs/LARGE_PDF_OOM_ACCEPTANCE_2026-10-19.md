# Large PDF OOM acceptance — 2026-10-19

Code: **ae4dc154519257841af30ef3336a355378ad2b13** on `codex/pdf-snapshot-memory-20261019`, based on Claude ec13bc1. This is the whole branch, not a claim that the final one-line commit alone contains all optimisations.

Immutable tested image: `sha256:99692ceae02ab8b1bec8d350fc9737513db1fb02a45c4c8755ba7eecfd3dd5d6` (`bench-tenant:codex-largepdf-final`). Built with `build_bench_overlay.py` from clean committed sources over the existing ed0748f image, with no dependency changes, no network during the build, and verified inherited configuration/layers. Docker Desktop Linux; 2 CPUs per test.

## Result and exact scope

The same **254,914,560-byte (243 MiB) synthetic database** was used throughout: 150 demo copies, 386,250 orders, 363,900 sales, 363,900 financial rows, 1,950 products. It is not a production backup. Source quick_check passed; the harness used online SQLite backup into a disposable directory, never modified the source, and ran with no network or published ports.

**memory.max = 805306368 bytes (768 MiB), memory.swap.max = 0.** The measured cgroup contains the real PDF parent and its real isolated report worker, including all 21 metrics. It does **not** contain a resident tenant API/nginx/background scheduler, concurrent reports, WB synchronisation or 20–30 tenants. This is full-PDF acceptance, not a new complete-tenant capacity acceptance or a promise for every real database/protected-history shape.

| Period | Full PDF time | Cgroup memory.peak | PDF pages | max / oom / oom_kill delta | Verdict |
|---|---:|---:|---:|---|---|
| 30 | 79.02 s | 686.30 MiB | 11 | 0 / 0 / 0 | PASS |
| 90 | 73.64 s | 703.86 MiB | 11 | 0 / 0 / 0 | PASS |

Both resulting files parse with pypdf, contain extracted text, and match their SHA-256 records. Each requests and completes all 21 metric codes; a worker/metric failure aborts the report. The stricter gate requires zero high/max/oom/oom_kill events, exit 0, no OOMKilled, the unchanged memory/swap limits and confirmed removal of the exact created container. Timings are observations on this host, not a capacity SLA.

## Changes

- Defer financial SQL reading until operational order/sale frames have been consumed. Aggregate financial operations by SKU before constructing independent FIFO/COGS working sets.
- Repeat bounded financial cursors within the same SQLite BEGIN snapshot, rather than retaining all source dictionaries and a duplicate prepared list. Retain duplicate-key checks and original source fingerprints.
- Consume owned reconciliation output records in exact-type chunks using the same reader that preserves large integer/mixed SQLite types. Reduce intermediate COGS dictionaries and filter only columns used by totals.
- Dashboard reconciliation requests the eight fields used by COGS/summary, rather than retaining every event/layer list in all 18 detailed fields. The default detailed reader still returns the full frame. Protected rows still receive the full original source and all 18 result fields during projections, then only the final dashboard result is compacted. The secondary-shop policy still forces allow_local_fifo=False and explicitly forwards for_dashboard.
- In the isolated report process only, choose Arrow's system pool and release unreachable/libc-free memory at calculation boundaries and between metrics. No Arrow release_unused diagnostic call is shipped. API processes keep their allocator settings. Platforms without glibc malloc_trim safely skip that native operation.
- The earlier copy-on-write snapshot change remains, with eager-copy fallback before pandas 3.

No memory/slot limits, financial formula, consent/landing text, service deployment or production data changed.

## Verification

On the final application tree: **1355 passed, 1 skipped, 66 subtests passed**, using `python -m pytest tests reports/tests -q --tb=short` from tenant-app with an independent basetemp. A root-directory test invocation earlier had three child-import failures (db not on the child path); correct-cwd runs pass without changing those tests. The full suite includes the all-21-metric title/summary/PNG equivalence and completed-PDF text/page-count equivalence. A separate final worker/memory run also passed 5 tests.

Exact DashboardData comparison against 43148f8 on the large database passed for 1/7/30/90 days: all 11 fields, exact frame values/dtypes and metadata. The calculation/db files at the comparison commit 7ed19aa are byte-identical to those at ae4dc15; the latter changes only allocator cleanup between metric renders. Full source/projection fingerprint tests, protected-row field tests, duplicate/malformed-payload cases and mixed-type chunk-reader tests remain. Cursor snapshot isolation is tested with a concurrent committed update. Ordinary API/FIFO readers keep their defaults.

`graphify update .`, compile checks and git diff --check completed. No API endpoint, live payment or production access was involved.

## Retained negative results and cleanup

- 43148f8: 30-day full PDF OOM (oom_kill=1).
- 2e04a4b/b089d07/5baf860/8fb1980/4603b2f: intermediate 90-day failures retained in the JSON files, not relabelled.
- 7ed19aa: complete readable PDF and no OOM, but max=5 and peak reached the hard limit. It remains **FAIL** under the strict gate; cleanup between metrics produced the final PASS.
- An early harness verifier attempted to import pypdf inside the runtime image, where it is not installed. File validation now happens on the host, after rendering. Another early harness cleanup recorded the Windows SQLite-handle/case-matching error; its old FAIL act remains unchanged and later authoritative cleanup is recorded separately.
- Diagnostic 2 GiB runs located allocation peaks only; they are not acceptance runs. No accepted limit was raised.
- Final test containers and all eight explicitly identified diagnostic containers are confirmed removed. All nine previously listed unrelated local container names remain listed. No pre-existing service was stopped or changed by this work.

Raw acts: `docs/benchmarks/large-pdf-2026-10-19/`; SHA-256 manifest: `sha256.json`. The PDF files remain outside Git at `C:/Users/mvvor/bm-tmp/codex-large-pdf-20261019/final-{30,90}.pdf`.

Reproduce (run one at a time):

```powershell
python scripts/bench_large_pdf.py --image bench-tenant:codex-largepdf-final --database C:/Users/mvvor/bm-tmp/bigdata/wb_dashboard.sqlite3 --days 90 --out C:/Users/mvvor/bm-tmp/repeat-large-pdf-90.json
python scripts/bench_large_pdf.py --image bench-tenant:codex-largepdf-final --database C:/Users/mvvor/bm-tmp/bigdata/wb_dashboard.sqlite3 --days 30 --out C:/Users/mvvor/bm-tmp/repeat-large-pdf-30.json
```
