"""Render one report metric in an isolated process.

Some marketplace calculations temporarily use hundreds of megabytes. Keeping
all 21 calculations in the API process made its allocator retain that memory
between metrics until Docker killed the service. This worker gives every
metric a fresh process, so its working set is returned to the OS immediately.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import matplotlib.pyplot as plt

from .metrics import build_metric


def main() -> None:
    code, start_text, end_text, image_name, result_name = sys.argv[1:]
    result = build_metric(code, date.fromisoformat(start_text), date.fromisoformat(end_text))
    try:
        result.figure.savefig(image_name, format="png", dpi=125, bbox_inches="tight")
    finally:
        plt.close(result.figure)
    Path(result_name).write_text(
        json.dumps({"title": result.title, "summary": result.summary}, ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
