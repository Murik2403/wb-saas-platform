"""Prototype: push the report period into SQL for build_dashboard and prove the result is identical.

Not product code. It patches `calculations.read_table` / `read_financial` in the running process so that orders, sales and ads
are read for the period only, stocks for the latest snapshot only, and the financial table for the period plus the one row that
carries the latest operation date (the function needs that date for its accounting horizon). Each mode runs in its own process:

    WB_SAAS_DATA_DIR=<dir> python scripts/prototype_dashboard_pushdown.py run --mode baseline --out base.pkl
    WB_SAAS_DATA_DIR=<dir> python scripts/prototype_dashboard_pushdown.py run --mode pushdown --out push.pkl
    python scripts/prototype_dashboard_pushdown.py compare base.pkl push.pkl

The pushdown is only applied when every date of a table is ISO-shaped (`YYYY-MM-DD...`): pandas infers the format of mixed legacy
dates from the first row (see the comment in `db.ingest.read_financial`), so a filtered read could change how they parse. With any
other shape the table is read whole, exactly as before.
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
import time
from datetime import date, timedelta
from pathlib import Path

APP = Path(os.environ.get("WB_SAAS_APP_DIR") or Path(__file__).resolve().parents[1] / "tenant-app")
sys.path.insert(0, str(APP))

ISO = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*"
DATE_COLUMN = {"orders": "order_date", "sales": "sale_date", "ads_daily": "day", "financial_report": "operation_date", "stocks": "snapshot_at"}


def install(start: date, end: date):
    import pandas as pd
    import calculations
    from db.core import _table_projection, connect
    from db.ingest import read_financial as original_financial

    full_read_table = calculations.read_table

    def iso_only(conn, table):
        column = DATE_COLUMN[table]
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} IS NOT NULL AND {column} NOT GLOB '{ISO}'").fetchone()[0] == 0

    def read_table(name, *, omit_raw=False):
        if name not in ("orders", "sales", "ads_daily", "stocks"):
            return full_read_table(name, omit_raw=omit_raw)
        column = DATE_COLUMN[name]
        with connect() as conn:
            if not iso_only(conn, name):
                return full_read_table(name, omit_raw=omit_raw)
            projection = _table_projection(conn, name, omit_raw=omit_raw)
            if name == "stocks":
                return pd.read_sql_query(f"SELECT {projection} FROM stocks WHERE snapshot_at = (SELECT MAX(snapshot_at) FROM stocks)", conn)
            # rows whose date is NULL are dropped by the caller anyway (to_datetime coerce + dropna)
            return pd.read_sql_query(f"SELECT {projection} FROM {name} WHERE substr({column},1,10) BETWEEN ? AND ?", conn, params=(start.isoformat(), end.isoformat()))

    def read_financial(*, omit_raw=False, dates_only=False):
        if dates_only:
            return original_financial(omit_raw=omit_raw, dates_only=dates_only)
        from db.core import ensure_financial_schema
        ensure_financial_schema()
        with connect() as conn:
            if not iso_only(conn, "financial_report"):
                return original_financial(omit_raw=omit_raw)
            reason = """CASE WHEN json_valid(raw_json) THEN
                COALESCE(json_extract(raw_json, '$.bonusTypeName'), json_extract(raw_json, '$.bonus_type_name'), '') ELSE '' END AS deduction_reason"""
            projection = _table_projection(conn, "financial_report", omit_raw=omit_raw)
            return pd.read_sql_query(
                f"SELECT {projection}, {reason} FROM financial_report WHERE substr(operation_date,1,10) BETWEEN ? AND ? "
                "OR rowid = (SELECT rowid FROM financial_report WHERE operation_date = (SELECT MAX(operation_date) FROM financial_report) LIMIT 1)",
                conn, params=(start.isoformat(), end.isoformat()))

    calculations.read_table = read_table
    calculations.read_financial = read_financial


def peak_mib() -> float:
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except ImportError:
        from reports.report_worker import peak_rss_kb
        return peak_rss_kb() / 1024


def run(args) -> int:
    end = date.today()
    start = end - timedelta(days=args.days - 1)
    import calculations
    if args.mode == "pushdown":
        install(start, end)
    baseline = peak_mib()
    started = time.perf_counter()
    data = calculations.build_dashboard(start, end)
    seconds = time.perf_counter() - started
    Path(args.out).write_bytes(pickle.dumps({"data": data, "seconds": seconds, "peak_mib": peak_mib(), "baseline_mib": baseline, "mode": args.mode}))
    print(json.dumps({"mode": args.mode, "seconds": round(seconds, 1), "peak_mib": round(peak_mib(), 1), "baseline_mib": round(baseline, 1)}))
    return 0


def compare(args) -> int:
    import pandas as pd
    first, second = (pickle.loads(Path(p).read_bytes()) for p in (args.base, args.push))
    problems = []
    for field in first["data"].__dataclass_fields__:
        a, b = getattr(first["data"], field), getattr(second["data"], field)
        if isinstance(a, pd.DataFrame):
            try:
                pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True), check_exact=True)
            except AssertionError as error:
                problems.append((field, str(error).splitlines()[0]))
        else:
            if json.dumps(a, sort_keys=True, default=str) != json.dumps(b, sort_keys=True, default=str):
                problems.append((field, "values differ"))
    print(json.dumps({"identical": not problems, "differences": problems,
                      "baseline": {k: round(first[k], 1) for k in ("seconds", "peak_mib")}, "pushdown": {k: round(second[k], 1) for k in ("seconds", "peak_mib")}}, ensure_ascii=False))
    return 1 if problems else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    runner = sub.add_parser("run")
    runner.add_argument("--mode", choices=["baseline", "pushdown"], required=True)
    runner.add_argument("--out", required=True)
    runner.add_argument("--days", type=int, default=30)
    runner.set_defaults(func=run)
    comparer = sub.add_parser("compare")
    comparer.add_argument("base")
    comparer.add_argument("push")
    comparer.set_defaults(func=compare)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
