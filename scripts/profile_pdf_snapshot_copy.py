"""Compare eager/COW snapshot peaks in separate processes, without tenant data."""
from __future__ import annotations
import argparse
import copy
import json
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "tenant-app"
sys.path.insert(0, str(APP))


def child(mode, rows):
    import numpy as np
    import pandas as pd
    from calculations import DashboardData
    from reports.report_worker import _copy_dashboard, peak_rss_kb
    values = {}
    for field in fields(DashboardData):
        if field.name in {"kpi", "financial", "financial_fifo_summary"}:
            values[field.name] = {"nested": {"value": 1}}
        else:
            values[field.name] = pd.DataFrame({"value": np.arange(rows, dtype=float)})
    snapshot = DashboardData(**values)
    before = peak_rss_kb()
    cloned = copy.deepcopy(snapshot) if mode == "eager" else _copy_dashboard(snapshot)
    after = peak_rss_kb()
    shared = np.shares_memory(snapshot.daily["value"].to_numpy(), cloned.daily["value"].to_numpy())
    cloned.daily.loc[0, "value"] = -1
    assert snapshot.daily.loc[0, "value"] == 0
    print(json.dumps({"mode": mode, "rows_per_frame": rows, "pandas": pd.__version__,
                      "peak_before_kib": before, "peak_after_copy_kib": after,
                      "copy_peak_growth_kib": max(0, after - before), "shared_before_write": bool(shared),
                      "mutation_isolated": True}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=1000000)
    parser.add_argument("--child", choices=["eager", "cow"])
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.rows < 1:
        parser.error("rows must be positive")
    if args.child:
        child(args.child, args.rows)
        return
    report = {"scope": "synthetic dashboard snapshot copying only; not full PDF or container acceptance", "results": []}
    for mode in ("eager", "cow"):
        result = subprocess.run([sys.executable, __file__, "--child", mode, "--rows", str(args.rows)],
                                check=True, capture_output=True, text=True, timeout=120)
        report["results"].append(json.loads(result.stdout.strip().splitlines()[-1]))
    rendered = json.dumps(report, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
