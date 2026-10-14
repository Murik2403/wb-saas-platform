"""Cost coverage keeps first-row identity semantics without per-history-row Series."""
import ast
from datetime import datetime
import inspect
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import ui_helpers as quality


# Frozen previous identity algorithm. The differential reference is scoped to
# this optimization and works in shallow CI checkouts without historical Git.
OLD_IDENTITY = """
for frame in (catalog, orders, sales):
    if frame.empty or "nm_id" not in frame.columns:
        continue
    for _, row in frame.iterrows():
        try:
            nm_id = int(float(row.get("nm_id", 0) or 0))
        except (TypeError, ValueError):
            continue
        if nm_id <= 0:
            continue
        item = identity.setdefault(nm_id, {"supplier_article": "", "product_name": ""})
        article = str(row.get("supplier_article", "") or "").strip()
        product = str(row.get("product_name", row.get("subject", "")) or "").strip()
        if article and not item["supplier_article"]:
            item["supplier_article"] = article
        if product and not item["product_name"]:
            item["product_name"] = product
"""


@pytest.fixture(scope="module")
def original_functions():
    coverage = ast.parse(inspect.getsource(quality._cost_coverage_diagnostics)).body[0]
    identity_loop = next(i for i, node in enumerate(coverage.body) if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and node.target.id == "frame")
    coverage.body[identity_loop] = ast.parse(OLD_IDENTITY).body[0]
    nodes = [coverage, ast.parse(inspect.getsource(quality.build_data_quality_overview)).body[0]]
    namespace = dict(vars(quality))
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), "original-quality-coverage", "exec"), namespace)
    return namespace


@pytest.fixture(scope="module")
def original_coverage(original_functions):
    return original_functions["_cost_coverage_diagnostics"]


NOW = datetime(2026, 10, 14, 4, 0, tzinfo=ZoneInfo("Europe/Moscow"))


@pytest.mark.parametrize("case", ["catalog_priority", "empty_fields", "missing_columns", "numeric_ids", "numeric_row_coercion", "recent_activity", "no_catalog"])
def test_identity_output_matches_original_first_nonempty_semantics(original_coverage, case):
    catalog = pd.DataFrame([
        {"nm_id": 1, "supplier_article": " Catalog-A ", "product_name": "Catalog name"},
        {"nm_id": 2, "supplier_article": "", "product_name": "Second catalog name"},
        {"nm_id": 3, "supplier_article": " Third ", "product_name": ""},
    ])
    orders = pd.DataFrame([
        {"nm_id": 2, "supplier_article": " First-Order ", "subject": "Order subject", "order_date": "2026-10-14"},
        {"nm_id": 2, "supplier_article": "Later-order", "subject": "Later subject", "order_date": "2026-10-14"},
        {"nm_id": 1, "supplier_article": "Conflicting order", "subject": "Conflicting name", "order_date": "2026-10-14"},
        {"nm_id": 77, "supplier_article": "Retired", "subject": "Outside catalog", "order_date": "2026-10-14"},
    ])
    sales = pd.DataFrame([
        {"nm_id": 3, "supplier_article": "Conflicting sale", "product_name": "First sale name", "sale_date": "2026-10-14"},
        {"nm_id": 3, "supplier_article": "Later sale", "product_name": "Later sale name", "sale_date": "2026-10-14"},
    ])
    stocks = pd.DataFrame([{"nm_id": 1, "snapshot_at": "2026-10-14", "quantity": 2}, {"nm_id": 3, "snapshot_at": "2026-10-14", "quantity": 3}])
    costs = pd.DataFrame([
        {"nm_id": 1, "supplier_article": "Catalog-A", "cost_per_wb_unit": 9},
        {"nm_id": 0, "supplier_article": "first\u00a0order", "cost_per_wb_unit": 5},
        {"nm_id": 3, "supplier_article": "Third", "cost_per_wb_unit": 0},
    ])
    if case == "empty_fields":
        catalog = catalog.astype(object)
        catalog.loc[0, ["supplier_article", "product_name"]] = [None, float("nan")]
        catalog.loc[1, "supplier_article"] = 0
        orders.loc[0, "supplier_article"] = "   "
    elif case == "missing_columns":
        catalog = catalog.drop(columns=["supplier_article", "product_name"])
        sales = sales.drop(columns=["product_name"])
    elif case == "numeric_ids":
        orders = orders.astype(object)
        sales = sales.astype(object)
        orders["nm_id"] = ["2.9", None, "bad-id", -1]
        sales["nm_id"] = ["3.0", 0]
    elif case == "numeric_row_coercion":
        # An unused float column makes iterrows coerce integer identity fields
        # to float. Preserve existing '101.0' strings rather than changing keys.
        catalog = pd.DataFrame({"nm_id": [1, 2, 3], "supplier_article": [101, 102, 103], "product_name": [201, 202, 203], "unused_rate": [.5, .5, .5]})
    elif case == "recent_activity":
        orders.loc[0:1, "order_date"] = ["2024-01-01", "invalid-date"]
        stocks.loc[1, "snapshot_at"] = "2024-01-01"
    elif case == "no_catalog":
        catalog = pd.DataFrame()
    expected_summary, expected_rows = original_coverage(catalog, orders, sales, stocks, costs, NOW)
    actual_summary, actual_rows = quality._cost_coverage_diagnostics(catalog, orders, sales, stocks, costs, NOW)
    assert actual_summary == expected_summary
    pd.testing.assert_frame_equal(actual_rows, expected_rows)


def test_large_identity_history_does_not_construct_a_series_per_operation(monkeypatch):
    catalog = pd.DataFrame([{"nm_id": 1, "supplier_article": "Current", "product_name": "Current name"}])
    orders = pd.DataFrame([{"nm_id": 1, "supplier_article": "Older", "subject": "Older name", "order_date": "2026-10-14"}] * 10000)
    sales = pd.DataFrame([{"nm_id": 1, "supplier_article": "Old sale", "product_name": "Old sale name", "sale_date": "2026-10-14"}] * 10000)
    # Empty costs: all iterrows calls would belong to the expensive identity scan.
    def forbidden(*args, **kwargs):
        raise AssertionError("Identity scan created per-operation pandas Series")
    monkeypatch.setattr(pd.DataFrame, "iterrows", forbidden)
    summary, rows = quality._cost_coverage_diagnostics(catalog, orders, sales, pd.DataFrame(), pd.DataFrame(), NOW)
    assert summary["active_count"] == 1 and summary["missing_count"] == 1
    assert rows.iloc[0]["Артикул продавца"] == "Current"
    assert rows.iloc[0]["Товар"] == "Current name"


def test_full_365_day_quality_checks_score_reliability_and_costs_match_original(tmp_path, monkeypatch, original_functions):
    from db import core
    import seed_demo_data

    monkeypatch.setattr(core, "DB_PATH", tmp_path / "synthetic-quality.sqlite3")
    monkeypatch.setenv("WB_SAAS_IS_DEMO", "1")
    monkeypatch.setenv("WB_SAAS_TENANT_SLUG", "demo")
    monkeypatch.setattr(seed_demo_data, "DAYS_OF_HISTORY", 365)
    seed_demo_data.main()

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is not None else NOW.replace(tzinfo=None)

    monkeypatch.setattr(quality, "datetime", FixedDateTime)
    monkeypatch.setitem(original_functions, "datetime", FixedDateTime)
    context = ({}, True, {"status": "ok", "finished_at": "2026-10-14T03:00:00+03:00"}, None)
    expected = original_functions["build_data_quality_overview"](*context)
    actual = quality.build_data_quality_overview(*context)
    assert actual[1] == expected[1]
    for index in (0, 2, 3):
        pd.testing.assert_frame_equal(actual[index], expected[index])
