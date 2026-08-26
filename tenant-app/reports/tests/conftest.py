"""Closes matplotlib figures left open by metric-drawing tests.

metrics.py's real caller (pdf_builder.py) already does plt.close(result.figure)
after rendering each PNG -- this is purely test hygiene: unit tests call the
metric functions directly without going through that step, so across ~230
tests matplotlib accumulates open figures and warns past its default limit.
"""
from __future__ import annotations

import matplotlib.pyplot as plt
import pytest


@pytest.fixture(autouse=True)
def _close_matplotlib_figures():
    yield
    plt.close("all")
