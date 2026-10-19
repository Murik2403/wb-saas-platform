import copy
from dataclasses import fields

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from calculations import DashboardData
from reports import report_worker


def dashboard():
    values = {}
    for field in fields(DashboardData):
        if field.name in {"kpi", "financial", "financial_fifo_summary"}:
            values[field.name] = {"nested": {"values": [1, 2]}}
        else:
            values[field.name] = pd.DataFrame({"value": np.arange(1000, dtype=float), "label": ["item"] * 1000})
    return DashboardData(**values)


def test_metric_snapshots_share_numeric_buffers_until_written():
    if int(pd.__version__.split(".")[0]) < 3:
        pytest.skip("mandatory copy-on-write requires pandas 3")
    original = dashboard()
    first = report_worker._copy_dashboard(original)
    second = report_worker._copy_dashboard(original)
    baseline = copy.deepcopy(original)
    for field in fields(original):
        source = getattr(original, field.name)
        one, two = getattr(first, field.name), getattr(second, field.name)
        if isinstance(source, pd.DataFrame):
            assert one is not source and two is not one
            assert np.shares_memory(source["value"].to_numpy(), one["value"].to_numpy())
            one.loc[0, "value"] = -99
            one.loc[0, "label"] = "changed"
            one["extra"] = 42
            one.rename(columns={"value": "renamed"}, inplace=True)
            assert_frame_equal(source, getattr(baseline, field.name), check_exact=True)
            assert_frame_equal(two, source, check_exact=True)
        else:
            one["nested"]["values"].append(3)
            assert source == getattr(baseline, field.name) == two


def test_older_pandas_falls_back_to_independent_eager_copy(monkeypatch):
    monkeypatch.setattr(pd, "__version__", "2.3.0")
    original = dashboard()
    cloned = report_worker._copy_dashboard(original)
    assert not np.shares_memory(original.daily["value"].to_numpy(), cloned.daily["value"].to_numpy())
    cloned.daily.loc[0, "value"] = -10
    assert original.daily.loc[0, "value"] == 0
