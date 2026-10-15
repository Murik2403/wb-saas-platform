"""The commission chart must never materialize unused marketplace payloads.

Production's isolated metric worker was killed at this metric. Real SQLite
authorizer checks distinguish projection before fetching from dropping raw_json
after it has already occupied the worker's heap.
"""
from contextlib import contextmanager
from datetime import date
import sqlite3

import matplotlib.pyplot as plt
import pandas as pd
import pytest

from db import core
from reports import metrics


START, END = date(2026, 9, 1), date(2026, 9, 30)
NO_DATA = "Данных отчёта реализации за период нет."


def test_commission_metric_never_reads_raw_payload_columns(monkeypatch, tmp_path):
    database = tmp_path / "commission.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE financial_report(operation_date TEXT,nm_id INTEGER,commission REAL,acquiring_fee REAL,retail_amount REAL,raw_json TEXT)")
        conn.execute("CREATE TABLE products_catalog(nm_id INTEGER,subject_name TEXT,raw_json TEXT)")
        # Distinct payloads, rather than one Python object shared by every row.
        conn.executemany("INSERT INTO financial_report VALUES(?,?,?,?,?,?)", [
            ("2026-09-10", 1, 150, 10, 1000, '{"payload":"' + str(index) + "x" * 32768 + '"}')
            for index in range(64)
        ])
        conn.execute("INSERT INTO products_catalog VALUES(1,'Одежда',?)", ('{"payload":"' + "y" * 32768 + '"}',))
    original_connect = core.connect
    denied = []

    @contextmanager
    def guarded_connect():
        with original_connect() as conn:
            def authorize(action, table, column, _database, _trigger):
                if action == sqlite3.SQLITE_READ and column == "raw_json":
                    denied.append(table)
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            conn.set_authorizer(authorize)
            try:
                yield conn
            finally:
                conn.set_authorizer(None)

    monkeypatch.setattr(core, "DB_PATH", database)
    monkeypatch.setattr(core, "connect", guarded_connect)
    monkeypatch.setattr(metrics, "read_table", core.read_table)
    result = metrics.build_metric("wb_commission_rate", START, END)
    assert denied == []
    assert result.summary == "Категория с максимальной эффективной комиссией: Одежда (16.0%)."
    assert [patch.get_width() for patch in result.figure.axes[0].patches] == [16.0]


def _financial(**changes):
    return {"operation_date": "2026-09-10", "nm_id": 1, "commission": 150.0,
            "acquiring_fee": 10.0, "retail_amount": 1000.0, **changes}


@pytest.mark.parametrize("financial,catalog,categories,rates,summary", [
    ([_financial()], [{"nm_id": 1, "subject_name": "Одежда"}], ["Одежда"], [16.0], "Категория с максимальной эффективной комиссией: Одежда (16.0%)."),
    ([_financial(operation_date="2026-09-01"), _financial(operation_date="2026-09-30"),
      _financial(operation_date="2026-08-31", commission=999), _financial(operation_date="bad", commission=999),
      _financial(operation_date="2026-10-01", commission=999)], [], ["Без категории"], [16.0], "Категория с максимальной эффективной комиссией: Без категории (16.0%)."),
    ([_financial(commission=100, acquiring_fee=10), _financial(commission=-10, acquiring_fee=-1, retail_amount=-100)],
     [{"nm_id": 1, "subject_name": "Возвраты"}], ["Возвраты"], [11.0], "Категория с максимальной эффективной комиссией: Возвраты (11.0%)."),
    ([_financial(commission="bad", acquiring_fee="10", retail_amount="1000")], [], ["Без категории"], [1.0], "Категория с максимальной эффективной комиссией: Без категории (1.0%)."),
    ([_financial(retail_amount=0), _financial(nm_id=2, retail_amount=-100)], [], [], [], NO_DATA),
    ([_financial()], [{"nm_id": 1, "subject_name": None}], ["Без категории"], [16.0], "Категория с максимальной эффективной комиссией: Без категории (16.0%)."),
    ([_financial()], [{"nm_id": 1, "subject_name": "A"}, {"nm_id": 1, "subject_name": "B"}],
     ["A", "B"], [16.0, 16.0], "Категория с максимальной эффективной комиссией: A (16.0%)."),
    ([], [], [], [], NO_DATA),
    ([{"nm_id": 1, "commission": 1}], [], [], [], NO_DATA),
    ([{"operation_date": "2026-09-01", "nm_id": 1}], [], [], [], NO_DATA),
    ([_financial(subject_name="Original")], [{"nm_id": 1, "subject_name": "Catalog"}], ["Без категории"], [16.0], "Категория с максимальной эффективной комиссией: Без категории (16.0%)."),
])
def test_commission_chart_preserves_dates_categories_returns_and_missing_values(
    monkeypatch, financial, catalog, categories, rates, summary,
):
    original_financial = pd.DataFrame(financial)
    original_catalog = pd.DataFrame(catalog)

    def reader(table, *, omit_raw=False):
        frame = original_financial if table == "financial_report" else original_catalog
        return frame.drop(columns=["raw_json"], errors="ignore") if omit_raw else frame

    monkeypatch.setattr(metrics, "read_table", reader)
    result = metrics.build_metric("wb_commission_rate", START, END)
    assert result.title == "Эффективная ставка комиссии WB по категориям"
    assert result.summary == summary
    ax = result.figure.axes[0]
    assert [patch.get_width() for patch in ax.patches] == rates
    if categories:
        assert [tick.get_text() for tick in ax.get_yticklabels()] == categories
        assert [text.get_text() for text in ax.texts] == [f"{rate:.1f}%" for rate in rates]
        assert ax.get_xlabel() == "Эффективная ставка комиссии, %"
        assert ax.yaxis_inverted()
    # The reader's source frames remain intact, including the old merge-name
    # collision behavior: this repair does not reinterpret financial categories.
    pd.testing.assert_frame_equal(original_financial, pd.DataFrame(financial))
    pd.testing.assert_frame_equal(original_catalog, pd.DataFrame(catalog))
    plt.close(result.figure)
