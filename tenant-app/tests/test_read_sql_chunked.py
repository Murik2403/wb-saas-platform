"""read_sql_chunked returns exactly what one pd.read_sql_query returns, whatever the NULL pattern across chunk boundaries."""
import random
import sqlite3

import pandas as pd
import pytest

from db import core


def make(rows, schema="id INTEGER, a INTEGER, b REAL, c TEXT, d"):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(f"CREATE TABLE t({schema})")
    conn.executemany("INSERT INTO t VALUES (?,?,?,?,?)", rows)
    return conn


def same(conn, monkeypatch, chunk):
    monkeypatch.setattr(core, "READ_CHUNK_ROWS", chunk)
    got = core.read_sql_chunked("SELECT * FROM t ORDER BY id", conn)
    want = pd.read_sql_query("SELECT * FROM t ORDER BY id", conn)
    pd.testing.assert_frame_equal(got, want, check_exact=True, check_dtype=True, check_column_type=True)
    assert list(got.index) == list(want.index)


@pytest.mark.parametrize("chunk", [1, 2, 3, 5, 7, 100])
def test_null_patterns_across_chunk_boundaries(monkeypatch, chunk):
    rows = []
    for i in range(1, 21):
        rows.append((i,
                     None if i <= 6 else i,                         # all-NULL first chunks, ints later
                     None if i % 4 == 0 else i / 3,                 # scattered NULLs in a float column
                     None if i > 12 else f"s{i}",                   # strings then all-NULL tail
                     None if i % 2 else (i if i < 10 else f"x{i}")))  # dynamic typing: NULL, ints and text in one column
    same(make(rows), monkeypatch, chunk)


@pytest.mark.parametrize("chunk", [1, 4, 5, 20, 21])
def test_exact_multiples_single_row_and_empty_table(monkeypatch, chunk):
    same(make([(i, i, i * 1.5, "a", i) for i in range(1, 21)]), monkeypatch, chunk)
    same(make([(1, None, None, None, None)]), monkeypatch, chunk)
    same(make([]), monkeypatch, chunk)


def test_random_tables_match_a_whole_read(monkeypatch):
    rng = random.Random(7)
    for _ in range(40):
        n = rng.randint(1, 60)
        rows = []
        for i in range(1, n + 1):
            pick = lambda *options: rng.choice(options)
            rows.append((i, pick(None, i, 2 ** 40 + i), pick(None, 0.5 * i, float(i), 1e300), pick(None, "", "Заказ-№%d" % i, "x" * 50),
                         pick(None, 1, 2.5, "t", b"\x00\x01")))
        same(make(rows), monkeypatch, rng.choice([1, 2, 3, 5, 8, 13, 50]))


def test_the_dashboard_readers_use_it_and_stay_equal_on_a_real_schema(monkeypatch):
    from tests.base import DbTestCase  # noqa: F401  (schema comes from core.init_db below)
    monkeypatch.setattr(core, "READ_CHUNK_ROWS", 2)
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(core, "DB_PATH", Path(tmp) / "db.sqlite")
        core.init_db()
        core.ensure_financial_schema()
        with core.connect() as conn:
            for i in range(1, 8):
                conn.execute("INSERT INTO orders(id,order_date,nm_id,raw_json) VALUES (?,?,?,?)", (f"o{i}", "2026-06-0%d" % i, i if i % 3 else None, "{}"))
        got = core.read_table("orders", omit_raw=True)
        with core.connect() as conn:
            want = pd.read_sql_query(f"SELECT {core._table_projection(conn, 'orders', omit_raw=True)} FROM orders", conn)
        pd.testing.assert_frame_equal(got, want, check_exact=True)
