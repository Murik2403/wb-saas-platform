"""Render all metrics of one report in a single isolated process.

`metric_worker` starts a fresh interpreter per metric, which re-imports pandas and matplotlib 21 times and, for the
thirteen metrics that call `calculations.build_dashboard`, re-reads and re-computes the same tables 13 times. This
worker keeps the isolation that matters (the API process never holds the working set) but starts once and computes
the dashboard once per (start, end). Each metric still receives its own deep copy, so a metric that edits a frame
cannot change what the next one sees, and the figure is closed and the interpreter's garbage collected between
metrics.

Usage: report_worker <start> <end> <out_dir> <code> [<code> ...]
Writes <out_dir>/<index>.png and <out_dir>/<index>.json ({"title", "summary"}) per code, and <out_dir>/done.json.
Exit 3 names the failing metric on stderr.
"""
from __future__ import annotations

import copy
import gc
import json
import sys
from datetime import date
from pathlib import Path

import matplotlib.pyplot as plt

from . import metrics
from calculations import build_dashboard as _build_dashboard

DPI = 125  # identical to metric_worker


def peak_rss_kb() -> int:
    try:
        import resource
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)  # kB on Linux
    except ImportError:  # Windows development hosts
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t)] + [
                (name, ctypes.c_size_t) for name in ("WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                                                     "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        kernel, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
        return int(counters.PeakWorkingSetSize // 1024)


def main() -> None:
    start_text, end_text, out_dir, *codes = sys.argv[1:]
    start, end = date.fromisoformat(start_text), date.fromisoformat(end_text)
    out = Path(out_dir)
    snapshots: dict = {}

    def shared_dashboard(first, last):
        key = (first, last)
        if key not in snapshots:
            snapshots[key] = _build_dashboard(first, last)
        return copy.deepcopy(snapshots[key])

    metrics.build_dashboard = shared_dashboard  # the metrics module looks the name up at call time
    for index, code in enumerate(codes):
        try:
            result = metrics.build_metric(code, start, end)
            try:
                result.figure.savefig(out / f"{index}.png", format="png", dpi=DPI, bbox_inches="tight")
            finally:
                plt.close(result.figure)
            (out / f"{index}.json").write_text(json.dumps({"title": result.title, "summary": result.summary}, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - name the metric, the parent turns this into the section error
            sys.stderr.write(f"{code}: {type(exc).__name__}: {exc}\n")
            raise SystemExit(3)
        del result
        gc.collect()
    (out / "done.json").write_text(json.dumps({"metrics": len(codes), "peak_rss_kb": peak_rss_kb(), "dashboard_builds": len(snapshots)}), encoding="utf-8")


if __name__ == "__main__":
    main()
