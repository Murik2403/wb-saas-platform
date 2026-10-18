# Memory of `calculations.build_dashboard` on a large database

Measured on the synthetic large tenant of `RELEASE_ACCEPTANCE_2026-10-17.md` (243 MiB: 386,250 orders, 363,900 sales, 363,900 financial rows, 118,950 ad rows, 1,950 products; **61 days of history, report period 30 days**). Nothing here changes a limit or product code: `scripts/profile_dashboard_memory.py`, `profile_dashboard_stages.py` and `prototype_dashboard_pushdown.py` are read-only measurement tools. Raw numbers: `benchmarks/release-2026-10-17/dashboard-memory-profile.json`.

## What the function holds

| Table | Rows | DataFrame size | Rows inside the period |
|---|---|---|---|
| orders | 386,250 | 91 MiB | 46% |
| sales | 363,900 | 91 MiB | 49% |
| financial_report | 363,900 | 107 MiB | 49% |
| ads_daily | 118,950 | 18 MiB | 48% |
| stocks / costs / catalog | 1,950 each | under 1 MiB | latest snapshot only used |

The resting size of all frames (about 308 MiB) is not the problem; the **peaks** are. Python allocation peak per stage (tracemalloc, MiB; the whole function peaks at 779 MiB of Python allocations and 1.0-1.1 GiB of process memory):

| Stage | Peak | Held afterwards |
|---|---|---|
| `read_table("orders")` | 365 | 24 |
| `read_table("sales")` | 419 | 64 |
| `read_table("ads_daily")` | 190 | 90 |
| `read_financial()` | **749** | 160 |
| `read_financial_fifo_reconciliation()` (period rows, `SELECT *` with raw payloads, one dict per row) | **779** | 298 |
| `project_historical_cogs()` | 424 | 304 |
| the rest (per-product loop, financial block, `f.copy()` twice, `to_dict("records")`) | up to 381 | |

## Findings

1. **`pd.read_sql_query` builds every row as a Python tuple before the frame exists.** Reading orders, sales and financial rows peaks at 3-7 times the finished frame (financial: 749 MiB for a 107 MiB frame). `chunksize` with a concatenation, or typed numpy reads, removes this transient.
2. **The two biggest peaks are both on financial rows**: the whole-table `read_financial` (everything, all history, then filtered in pandas) and `read_financial_fifo_reconciliation` (the period only, but `SELECT *`, one dict per row, `raw_json` kept as text and parsed per row to find the `srid`). The latter only needs the `srid`; `json_extract(raw_json, '$.srid')` in SQL, and streaming rows instead of keeping `financial_rows`, would cut it without changing a value. Rows whose payload must be preserved (bindings, projection, migration documents) keep their payload as today.
3. **History is read whole and filtered in pandas**: orders, sales, ads and financial rows outside the period are loaded and then dropped; stocks loads every snapshot and uses only the latest (this synthetic base has one snapshot; a real tenant stores one per sync, so this table can dominate there and was not measurable here). Indexes on the date columns already exist (`idx_orders_date`, `idx_sales_date`, `idx_ads_day`, `idx_fin_operation_date`, `idx_stocks_snapshot`).
4. **Time, not memory**: the per-product loop filters the whole orders/sales/ads frames once per product (`valid_orders[valid_orders["nm_id"] == nm_id]`, 1,950 times); one `groupby` would do it.

## Pushdown prototype (what SQL filtering alone buys, and the proof that it changes nothing)

`prototype_dashboard_pushdown.py` reads orders, sales and ads for the period only, stocks for the latest snapshot only, and financial rows for the period plus the one row that carries the latest operation date (the function needs that date for its accounting horizon). Result on this base: **every field of `DashboardData` is identical** (daily, products, ads, stocks, alerts, financial dict, FIFO summary: exact frame equality), time 30.9 s to 27.3 s, process peak 1076 MiB to 1009 MiB. The small gain is expected here: only about half of this base lies outside the period, and the largest peak (finding 2) is untouched by it. On a tenant with a year of history the filter drops about 90% of the rows, so findings 1 and 2 are where the memory is.

One guard is essential: the pushdown is only valid while every date of the table is ISO-shaped (`YYYY-MM-DD...`). pandas infers the format of mixed legacy dates from the first row (see the comment in `db.ingest.read_financial`), so a filtered read could change how such rows parse; the prototype checks this per table with `NOT GLOB` and falls back to the whole-table read.

## Recommended order (not implemented here)

1. Stream `read_financial_fifo_reconciliation` (SQL `srid`, no `raw_json` text kept): largest peak, no change of values.
2. Chunked reads in `read_table` / `read_financial` for the dashboard projection: removes the tuple blow-up for orders, sales, ads, financial.
3. Period and latest-snapshot filters in SQL behind the ISO guard.
4. Group once instead of per product (time).
Each step is checked by exact `DashboardData` equality (the prototype's `compare`), plus the existing finance tests; limits of 768 MiB per tenant stay as they are.

## Branch `claude/dashboard-memory-20261030` (from ed0748f): what was done and measured

Two changes, no limit raised, no financial formula touched.

1. **`211ca31` FIFO reconciliation** (`db/financial_fifo_reconciliation.py`): reads the nine columns the reconciliation uses instead of `SELECT *` (28), streams the cursor, reduces `raw_json` to its SRID exactly as before, re-reads in full only the rows protected by a binding, projection or migration document (their fingerprint hashes the whole row), and keeps small tuples instead of per-row copies in `_reconcile`. Isolated on a 177k-row window: Python peak **513 to 279 MiB**, 21 to 14 s.
2. **`824f42c` chunked table reads** (`db.core.read_sql_chunked`, used by `read_table` and `read_financial`): 50,000 rows per fetch instead of one `fetchall`. Columns whose dtype differs between chunks (a NULL-only chunk is `object`, the next is `float64`) are rebuilt with the same `DataFrame.from_records` inference a whole read uses, so the frame equals `pd.read_sql_query`, dtypes and index included.

| `build_dashboard`, 243 MiB synthetic base | before | after step 1 | after step 2 |
|---|---|---|---|
| process peak (RSS) | 1075.6 MiB | 1074.2 MiB | **854.3 MiB** |
| time | 31.5 s | 29.8 s | 29.5 s |
| `DashboardData` vs before | | identical | identical |

Stage peaks (tracemalloc): `orders` 365 to 85 MiB, `read_financial` 749 to 247 MiB, FIFO reconciliation inside the function 779 to 545 MiB (it starts from about 266 MiB already held). The RSS did not move after step 1 alone because the maximum was the financial read, not the reconciliation; step 2 moved it.

Equality checks: `DashboardData` (daily, products, ads, stocks, alerts, FIFO summary, the financial dict) compared field by field with exact frame equality (`prototype_dashboard_pushdown.py compare`); 24 legacy/damaged `raw_json` shapes (invalid JSON, BOM, array/number/string top level, numeric/boolean/nested/NaN `srid`, `SRID` upper case, both keys, duplicate keys, 300 KB payloads) give the same rows as the previous reader; protected rows keep the same fingerprint; the whole reconciliation frame equals the previous one; 13 chunked-read cases including a random fuzz (without the dtype rebuild 6 of them fail). Full suites: 1328 tenant-app tests and 687 tenant-api tests pass.

**What this did not solve** (measured, not hoped): the full 21-metric PDF in a 768 MiB container with these changes fits at 75 copies (122 MiB base, 697 MiB peak, was 714) and is still killed by OOM at 150 copies (243 MiB). `build_dashboard` is the heaviest step, but about 300 MiB stays held while the reconciliation and the COGS projection run, and the PDF worker keeps a dashboard snapshot besides. The next levers, in order: free `financial_df` and the filtered frames before the FIFO block, build the reconciliation output column-wise instead of 176k dicts of 18 keys (the projections mutate those dicts, so this needs care), and the period filter in SQL behind the ISO guard.
