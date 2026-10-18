"""Where a report PDF spends its time: data loading, calculation, rendering, process start-up.

Run it in a tenant environment (the image the benchmark uses, seeded with the demo data) or against any data dir:

    WB_SAAS_DATA_DIR=<dir> python scripts/profile_pdf_stages.py --out profile.json [--repeat 1]

Per metric it separates, in one in-process pass, the time inside the table reads (`db.read_table`,
`read_financial`), inside `calculations.build_dashboard`, and the rest of the metric (its own pandas work
plus drawing), then the figure -> PNG time. A second pass runs every metric the way the isolated
production mode does (a fresh `reports.metric_worker` process each) so the start-up and import cost is
visible. `build_dashboard` calls are counted: calls beyond the first per report are repeated work.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

APP = Path(os.environ.get("WB_SAAS_APP_DIR") or Path(__file__).resolve().parents[1] / "tenant-app")
sys.path.insert(0, str(APP))


def timed(counter: dict, key: str, fn):
    def wrapper(*args, **kwargs):
        started = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            counter[key] = counter.get(key, 0.0) + time.perf_counter() - started
            counter[key + "_calls"] = counter.get(key + "_calls", 0) + 1
    return wrapper


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--skip-isolated", action="store_true")
    args = parser.parse_args(argv)
    end = date.today()
    start = end - timedelta(days=args.days - 1)

    import matplotlib.pyplot as plt
    import calculations
    import db
    from reports import metrics

    counter: dict = {}
    # every module imported `read_table` by name, so patch the names where they are used
    for module in (calculations, metrics, db):
        for name in ("read_table", "read_financial"):
            if hasattr(module, name):
                setattr(module, name, timed(counter, name, getattr(module, name)))
    original_build = calculations.build_dashboard
    calculations.build_dashboard = timed(counter, "build_dashboard", original_build)
    metrics.build_dashboard = calculations.build_dashboard

    rows = []
    total_started = time.perf_counter()
    for code in metrics.METRIC_BUILDERS:
        counter.clear()
        started = time.perf_counter()
        result = metrics.build_metric(code, start, end)
        build_s = time.perf_counter() - started
        started = time.perf_counter()
        buffer = io.BytesIO()
        result.figure.savefig(buffer, format="png", dpi=125, bbox_inches="tight")
        plt.close(result.figure)
        render_s = time.perf_counter() - started
        dashboard_s = counter.get("build_dashboard", 0.0)
        rows.append({
            "metric": code,
            "build_total_s": round(build_s, 3),
            "build_dashboard_s": round(dashboard_s, 3),
            "build_dashboard_calls": counter.get("build_dashboard_calls", 0),
            "table_reads_s": round(counter.get("read_table", 0.0) + counter.get("read_financial", 0.0), 3),
            "table_reads_calls": counter.get("read_table_calls", 0) + counter.get("read_financial_calls", 0),
            "own_calc_and_draw_s": round(build_s - dashboard_s, 3),
            "png_s": round(render_s, 3),
            "png_bytes": buffer.tell(),
        })
    in_process_total = time.perf_counter() - total_started

    isolated_rows = []
    if not args.skip_isolated:
        import tempfile
        isolated_started = time.perf_counter()
        for code in metrics.METRIC_BUILDERS:
            with tempfile.TemporaryDirectory(prefix="profile-metric-") as tmp:
                started = time.perf_counter()
                done = subprocess.run(
                    [sys.executable, "-m", "reports.metric_worker", code, start.isoformat(), end.isoformat(),
                     os.path.join(tmp, "m.png"), os.path.join(tmp, "m.json")],
                    cwd=str(APP), capture_output=True, text=True, timeout=300, check=False,
                )
                isolated_rows.append({"metric": code, "wall_s": round(time.perf_counter() - started, 3), "exit": done.returncode})
        isolated_total = time.perf_counter() - isolated_started
        startup_started = time.perf_counter()
        subprocess.run([sys.executable, "-c", "import reports.metrics"], cwd=str(APP), check=False, capture_output=True)
        import_only_s = time.perf_counter() - startup_started
    else:
        isolated_total = None
        import_only_s = None

    doc_started = time.perf_counter()
    from reports.pdf_builder import _render_report_pdf
    pdf = _render_report_pdf("profile", list(metrics.METRIC_BUILDERS), start, end)
    full_in_process_pdf_s = time.perf_counter() - doc_started

    summary = {
        "days": args.days,
        "metrics": len(rows),
        "in_process_sum_s": round(in_process_total, 3),
        "build_dashboard_calls": sum(r["build_dashboard_calls"] for r in rows),
        "build_dashboard_s": round(sum(r["build_dashboard_s"] for r in rows), 3),
        "own_calc_and_draw_s": round(sum(r["own_calc_and_draw_s"] for r in rows), 3),
        "png_s": round(sum(r["png_s"] for r in rows), 3),
        "isolated_total_s": None if isolated_total is None else round(isolated_total, 3),
        "python_import_reports_metrics_s": None if import_only_s is None else round(import_only_s, 3),
        "full_pdf_in_process_s": round(full_in_process_pdf_s, 3),
        "pdf_bytes": len(pdf),
        "cpu_count": os.cpu_count(),
    }
    Path(args.out).write_text(json.dumps({"summary": summary, "in_process": rows, "isolated": isolated_rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
