# Release acceptance: PDF fairness candidate 024a6fe and the one-process report worker

Branch `claude/pdf-speed-20261029` (from `claude/release-acceptance-20261028`, base Codex 024a6fe). Everything ran in a separate Docker-in-Docker daemon on the owner's machine (own volume, network `none`, 20 GiB, 10 CPUs, no host socket); production was not touched. Candidate under test: 024a6fe unchanged for the first runs, then 024a6fe + `6715307` (the only application change: `tenant-app/reports/pdf_builder.py`, new `report_worker.py`, one test file).

## Results

| Step | Candidate | Result | Act |
|---|---|---|---|
| Smoke 2/3/4 | 024a6fe | PASS (a first attempt FAILED on a harness timezone bug, kept) | `benchmarks/release-2026-10-17/smoke-pass/`, `smoke-tzbug-FAILED/` |
| Faults 3 and 4 | 024a6fe | PASS | `.../faults-pass/` |
| 20 tenants, 2 slots | 024a6fe | **FAIL** (12 of 20 PDFs in 660 s; a PDF took ~110 s) | `.../load-20-FAILED/` |
| 20 tenants | + `6715307` | PASS, last PDF 185 s of 660 s | `.../load-pass-b048b41/` |
| 25 tenants | + `6715307` | PASS, 324 s of 840 s | same |
| 30 tenants | + `6715307` | PASS, 531 s of 960 s | same |

All PASS acts: commit b048b41, clean tree, at most 2 PDFs at once, no API 5xx, no 504, no OOM (`memory.events` zero), container peak 426-435 MiB, every created object removed by full ID, raw sidecars hash-verified. Deadlines, two slots, the memory floor (2 GiB Windows, 1500 MiB VM) and every PASS criterion were not changed.

## Why the 20-tenant run failed and what changed

Queue, slots and 503/Retry-After behaved (718 of 720 request PDFs got 503 with Retry-After, no 504). The failure was capacity: a PDF took ~110 s against 50-55 s on 2 tenants. Profile of the old code at 0.5 CPU per tenant (the 20-on-10 ratio; `scripts/profile_pdf_stages.py`) reproduced it: 98 s per PDF, of which about 75 s was 21 fresh interpreters re-importing pandas and matplotlib (one per metric in isolated mode), 12 s thirteen repeats of `build_dashboard` (7 full table reads each), 6 s PNG rendering and 3 s the metrics' own calculations.

`reports/report_worker.py` renders all metrics of a report in one isolated process with one dashboard snapshot (deep-copied per metric). The API process still never holds the working set. Measured in the same image at 0.5 CPU: 98 s to 16 s; at 2 CPU: 39.5 s to 6.2 s; container peak memory 221 vs 222 MiB (demo data).

## Report content is unchanged

`reports/tests/test_report_worker_equivalence.py` (fresh interpreters, seeded demo database): every one of the 21 metrics has the same title, summary and PNG bytes as the per-metric worker (the baseline is itself checked to be deterministic); the finished PDF has the same text and page count as the in-process render; the dashboard is built once; a failing metric is named and fails the report.

## Deviations and limits, stated plainly

- The stand's pre-run memory gate (`required_windows_free`, harness only) was lowered from a 1 GiB to a 0.5 GiB margin (`d78114d`) for the 30-tenant run at the owner's explicit instruction after three attempts were refused 0.3-0.5 GiB short. The 2 GiB floor, the watchdog and 0.33 GiB per tenant are unchanged; measured per-tenant use was 0.26 (20) and 0.29 (25) GiB. Windows free memory in the 30 run never fell below 5.5 GiB. The refused attempts are `NOT_RUN` records (no workload), not committed as acts.
- Memory and speed were profiled on the demo seed (13 products, 61 days), which is what the benchmark tenants use. A tenant with a large real database may have a larger working set in the single worker than in one metric; the batch worker has no per-metric memory release. This has not been measured and is the first thing to watch after rollout.
- `WB_SAAS_REPORT_ISOLATED_METRICS=1` is the staging image default; with it off the in-process path (unchanged) is used.
- The first `20` act and the smoke tz-bug act are kept as recorded FAILs.
- Reviewer defects in 024a6fe that were not fixed in the candidate (reported to Codex; parked patch `claude-extra-pdf-fixes.patch`): the Streamlit "generate now" path bypasses `interactive_pdf`; a shop-child manual PDF ignores the beacon; the beacon can be held while a worker already owns a slot.

## Commands

```
python scripts/build_bench_overlay.py --base sha256:9f02e4b4... --tag bench-tenant:speed-<head>
python scripts/run_local_daemon_acceptance.py --phase smoke|load|faults --head <40-char sha> --image-id sha256:... \
    --image-tar ... --helper-tar ... --bundle source.bundle --evidence <dir> [--only 20]
python scripts/profile_pdf_stages.py --out profile.json
python -m pytest tenant-app/reports scripts/tests
```

## Peak memory on large databases (synthetic, not a restored backup)

No restored copy of a large production database exists on this machine and production was not touched, so the databases are the demo seed multiplied by `scripts/make_large_tenant_db.py` (own products, order, sale and finance rows per copy). Data: `benchmarks/release-2026-10-17/big-db-memory.json`; one full 21-metric PDF in a container with 2 CPUs and no network.

| Database | Old (one process per metric) | New (one process per report) | Limit |
|---|---|---|---|
| 41 MiB, 325 products | 60.5 s, 387 MiB | 11.0 s, 395 MiB | 768m |
| 81 MiB, 650 products | 165.8 s, 548 MiB | 19.3 s, 550 MiB | 768m |
| 122 MiB, 975 products (75 copies) | 221.7 s, 715 MiB | 25.8 s, 714 MiB | 768m |
| 243 MiB, 1950 products, 386k orders | 257.1 s, 1468 MiB | 28.5 s, 1233 MiB | 3g |
| 243 MiB | OOM on the first section | OOM on the first section | 768m |

The single worker does not raise the peak: it equals the old one within 5 MiB, because the peak is the dashboard computation (`calculations.build_dashboard`), not the number of processes. The finding that matters for rollout is older than this change: the production hard ceiling is 768 MiB (`TENANT_MEM_LIMIT`), and a PDF on a database of about 120 MiB (about 190k orders) already peaks at 715 MiB; above that it is killed by OOM in the old code and in the new. Reducing the memory of `build_dashboard` (reading only the period instead of whole tables) is the next step for large tenants and is not part of this change.
