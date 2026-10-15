"""SQLite must exclude unused raw payloads before any report chart fetches rows."""
from contextlib import closing, contextmanager
from datetime import date
from io import BytesIO
import hashlib
import sqlite3

import matplotlib.pyplot as plt
import pandas as pd
import pytest

from db import core
from reports import metrics


CASES = [
    ("stocks", {"stocks", "products_catalog"}),
    ("returns_cancellations", {"orders", "sales"}),
    ("ad_funnel", {"ads_daily"}),
    ("cost_structure", {"costs"}),
    ("wb_commission_rate", {"financial_report", "products_catalog"}),
    ("stock_in_transit", {"stocks"}),
    ("weekday_pattern", {"orders"}),
]


def _fixture_tables():
    return {
        "stocks": [
            {"snapshot_at": "2026-08-31", "nm_id": 1, "warehouse_name": "A", "quantity": 999, "in_way_to_client": 1, "in_way_from_client": 1},
            {"snapshot_at": "2026-09-10", "nm_id": 1, "warehouse_name": "A", "quantity": 10, "in_way_to_client": 3, "in_way_from_client": 2},
            {"snapshot_at": "2026-09-10", "nm_id": 2, "warehouse_name": "B", "quantity": 7, "in_way_to_client": 0, "in_way_from_client": 1},
        ],
        "products_catalog": [{"nm_id": 1, "supplier_article": "A", "subject_name": "Clothes"}, {"nm_id": 2, "supplier_article": "B", "subject_name": "Shoes"}],
        "orders": [{"order_date": "2026-09-10", "is_cancel": 1}, {"order_date": "2026-09-11", "is_cancel": 0}, {"order_date": "2026-08-01", "is_cancel": 1}],
        "sales": [{"sale_date": "2026-09-10", "is_return": 1}, {"sale_date": "2026-09-11", "is_return": 0}, {"sale_date": "2026-08-01", "is_return": 1}],
        "ads_daily": [{"day": "2026-09-10", "views": 1000, "clicks": 100, "atbs": 20, "orders_count": 5}, {"day": "2026-08-01", "views": 9999, "clicks": 999, "atbs": 99, "orders_count": 9}],
        "costs": [{"nm_id": 1, "supplier_article": "A", "material_cost_rub": 50, "packaging_cost_rub": 10, "labor_cost_rub": 20, "other_cost_rub": 5, "cost_per_wb_unit": 85}, {"nm_id": 2, "supplier_article": "B", "material_cost_rub": 0, "packaging_cost_rub": 0, "labor_cost_rub": 0, "other_cost_rub": 0, "cost_per_wb_unit": 30}],
        "financial_report": [{"operation_date": "2026-09-10", "nm_id": 1, "commission": 150, "acquiring_fee": 10, "retail_amount": 1000}, {"operation_date": "2026-09-11", "nm_id": 2, "commission": 20, "acquiring_fee": 2, "retail_amount": 200}],
    }


def _result_signature(result):
    output = BytesIO()
    result.figure.savefig(output, format="png", dpi=90)
    signature = (result.title, result.summary, hashlib.sha256(output.getvalue()).hexdigest())
    plt.close(result.figure)
    return signature


@pytest.mark.parametrize("code,expected_tables", CASES)
def test_every_direct_metric_projects_before_fetch_and_preserves_chart(monkeypatch, tmp_path, code, expected_tables):
    database = tmp_path / "metrics.sqlite3"
    with closing(sqlite3.connect(database)) as conn:
        for name, rows in _fixture_tables().items():
            frame = pd.DataFrame(rows)
            # Separate large payloads in every real table; do not fake projection.
            frame["raw_json"] = ["{\"payload\":\"" + str(index) + "x" * 32768 + "\"}" for index in range(len(frame))]
            frame.to_sql(name, conn, index=False)
    monkeypatch.setattr(core, "DB_PATH", database)
    # The old full-read behavior provides a populated numeric + PNG reference.
    monkeypatch.setattr(metrics, "read_table", lambda name, **kwargs: core.read_table(name))
    reference = _result_signature(metrics.build_metric(code, date(2026, 9, 1), date(2026, 9, 30)))
    original_connect = core.connect
    denied, read_tables = [], set()

    @contextmanager
    def guarded_connect():
        with original_connect() as conn:
            def authorize(action, table, column, _database, _trigger):
                if action == sqlite3.SQLITE_READ:
                    if column == "raw_json":
                        denied.append(table)
                        return sqlite3.SQLITE_DENY
                    if table in expected_tables:
                        read_tables.add(table)
                return sqlite3.SQLITE_OK
            conn.set_authorizer(authorize)
            try:
                yield conn
            finally:
                conn.set_authorizer(None)

    monkeypatch.setattr(core, "connect", guarded_connect)
    monkeypatch.setattr(metrics, "read_table", core.read_table)
    projected = _result_signature(metrics.build_metric(code, date(2026, 9, 1), date(2026, 9, 30)))
    assert denied == []
    assert read_tables == expected_tables
    assert projected == reference
