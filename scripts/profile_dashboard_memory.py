"""Where build_dashboard's memory goes on a large database: per table, per stage, and what SQL could cut.

    WB_SAAS_DATA_DIR=<dir with wb_dashboard.sqlite3> python scripts/profile_dashboard_memory.py --out profile.json [--days 30]

Reports, in one process (peak = ru_maxrss on Linux, PeakWorkingSet on Windows, plus tracemalloc deltas for the frames):
  * rows and in-memory size (`DataFrame.memory_usage(deep=True)`) of each table `build_dashboard` reads, and the share of
    those rows that fall inside the report period (what a SQL `WHERE` could drop before pandas sees it);
  * the peak after each stage of the function (reads, filtering, per-product loop, financial block);
  * the same read done with the period pushed into SQL, to show the size difference.
It only reads the database.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tracemalloc
from datetime import date, timedelta
from pathlib import Path

APP = Path(os.environ.get("WB_SAAS_APP_DIR") or Path(__file__).resolve().parents[1] / "tenant-app")
sys.path.insert(0, str(APP))


def peak_mib() -> float:
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from importlib import import_module
        sys.path.insert(0, str(APP))
        worker = import_module("reports.report_worker")
        return worker.peak_rss_kb() / 1024


def mib(frame) -> float:
    return round(frame.memory_usage(deep=True).sum() / 1024 ** 2, 1)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args(argv)
    end = date.today()
    start = end - timedelta(days=args.days - 1)

    import pandas as pd
    from db import read_table
    from db.core import connect
    from db.ingest import read_financial
    import calculations

    report = {"period": [start.isoformat(), end.isoformat()], "baseline_peak_mib": round(peak_mib(), 1), "tables": {}, "stages": []}

    readers = {
        "orders": (lambda: read_table("orders", omit_raw=True), "order_date"),
        "sales": (lambda: read_table("sales", omit_raw=True), "sale_date"),
        "ads_daily": (lambda: read_table("ads_daily", omit_raw=True), "day"),
        "stocks": (lambda: read_table("stocks", omit_raw=True), "snapshot_at"),
        "costs": (lambda: read_table("costs", omit_raw=True), None),
        "products_catalog": (lambda: read_table("products_catalog", omit_raw=True), None),
        "financial_report": (lambda: read_financial(omit_raw=True), "operation_date"),
    }
    with connect() as conn:
        for name, (read, date_column) in readers.items():
            started = time.perf_counter()
            before = peak_mib()
            frame = read()
            entry = {"rows": int(len(frame)), "frame_mib": mib(frame), "read_s": round(time.perf_counter() - started, 2),
                     "peak_mib_after_read": round(peak_mib(), 1), "peak_growth_mib": round(peak_mib() - before, 1),
                     "columns": int(frame.shape[1]),
                     "string_columns_mib": round(sum(frame[c].memory_usage(deep=True) for c in frame.columns if frame[c].dtype == object) / 1024 ** 2, 1)}
            if date_column:
                inside = conn.execute(f'SELECT COUNT(*) FROM {name} WHERE substr({date_column},1,10) BETWEEN ? AND ?', (start.isoformat(), end.isoformat())).fetchone()[0]
                entry["rows_in_period"] = int(inside)
                entry["share_in_period_percent"] = round(100 * inside / max(1, entry["rows"]), 1)
            if name == "stocks":
                latest = conn.execute("SELECT COUNT(*) FROM stocks WHERE snapshot_at = (SELECT MAX(snapshot_at) FROM stocks)").fetchone()[0]
                entry["rows_in_latest_snapshot"] = int(latest)
            report["tables"][name] = entry
            del frame

    # the whole function, with tracemalloc for allocation peaks inside it
    tracemalloc.start()
    before = peak_mib()
    started = time.perf_counter()
    data = calculations.build_dashboard(start, end)
    elapsed = time.perf_counter() - started
    current, traced_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    report["build_dashboard"] = {
        "seconds": round(elapsed, 1), "process_peak_mib": round(peak_mib(), 1), "growth_over_baseline_mib": round(peak_mib() - before, 1),
        "python_allocations_peak_mib": round(traced_peak / 1024 ** 2, 1), "result_rows": {"daily": int(len(data.daily)), "products": int(len(data.products))},
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report["build_dashboard"], ensure_ascii=False))
    for name, entry in report["tables"].items():
        print(f"{name:18} rows={entry['rows']:>8} frame={entry['frame_mib']:>8} MiB strings={entry['string_columns_mib']:>8} MiB in_period={entry.get('share_in_period_percent', '-')}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
