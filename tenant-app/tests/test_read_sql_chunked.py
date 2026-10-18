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


# ---- mixed SQLite storage classes: nothing may be converted before the column's final type is known -------------------------------

BIG = [2 ** 53 + 1, 2 ** 53 + 3, 2 ** 62 + 7, 2 ** 63 - 1, -(2 ** 63), -(2 ** 53) - 1, 12345678901234567]


def one_column(values):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t(id INTEGER, v)")
    conn.executemany("INSERT INTO t VALUES (?,?)", [(i + 1, value) for i, value in enumerate(values)])
    return conn


def test_counterexample_integer_above_2_pow_53_next_to_a_float_and_a_text_keeps_the_exact_integer(monkeypatch):
    """A first chunk of [int, float, int] used to become float64 (-2**53-1 turned into -2**53) while the whole read keeps the
    column as object with the exact integer because a later row is text."""
    values = [-(2 ** 53) - 1, -9007199254740992.0, -1, "s"]
    conn = one_column(values)
    monkeypatch.setattr(core, "READ_CHUNK_ROWS", 3)
    got = core.read_sql_chunked("SELECT * FROM t ORDER BY id", conn)
    want = pd.read_sql_query("SELECT * FROM t ORDER BY id", conn)
    pd.testing.assert_frame_equal(got, want, check_exact=True)
    assert got["v"].dtype == object and got["v"].tolist() == values
    assert got["v"].iloc[0] == -(2 ** 53) - 1 and isinstance(got["v"].iloc[0], int)


@pytest.mark.parametrize("chunk", [1, 2, 3, 4, 5])
def test_big_integers_nulls_floats_text_and_blobs_in_every_order_match_a_whole_read(monkeypatch, chunk):
    monkeypatch.setattr(core, "READ_CHUNK_ROWS", chunk)
    rng = random.Random(chunk)
    for _ in range(300):
        values = [rng.choice([None, 1, 2.5, "s", rng.choice(BIG), float(rng.choice(BIG)), b"b", 0, -1]) for _ in range(rng.randint(2, 12))]
        conn = one_column(values)
        got = core.read_sql_chunked("SELECT * FROM t ORDER BY id", conn)
        want = pd.read_sql_query("SELECT * FROM t ORDER BY id", conn)
        pd.testing.assert_frame_equal(got, want, check_exact=True)
        for left, right in zip(got["v"].tolist(), want["v"].tolist()):
            assert type(left) is type(right) and (left == right or (left != left and right != right)), (values, got["v"].tolist(), want["v"].tolist())


def test_a_type_that_changes_between_chunks_gives_the_whole_reads_dtype(monkeypatch):
    monkeypatch.setattr(core, "READ_CHUNK_ROWS", 2)
    cases = {
        "int_then_null": [1, 2, 3, None],
        "int_then_float": [1, 2, 3.5, 4.5],
        "null_then_float": [None, None, 1.5, 2.5],
        "null_then_text": [None, None, "a", "b"],
        "text_then_null": ["a", "b", None, None],
        "all_null": [None, None, None],
        "int_then_text": [1, 2, "a", "b"],
        "text_with_ints_inside": ["a", 7, "b", 2 ** 60 + 1],
    }
    for name, values in cases.items():
        conn = one_column(values)
        got = core.read_sql_chunked("SELECT * FROM t ORDER BY id", conn)
        want = pd.read_sql_query("SELECT * FROM t ORDER BY id", conn)
        pd.testing.assert_frame_equal(got, want, check_exact=True, obj=name)
        assert got["v"].dtype == want["v"].dtype, name


def test_empty_result_and_parameters(monkeypatch):
    monkeypatch.setattr(core, "READ_CHUNK_ROWS", 2)
    conn = one_column([1, 2, 3, 4, 5])
    pd.testing.assert_frame_equal(core.read_sql_chunked("SELECT * FROM t WHERE id > 99", conn), pd.read_sql_query("SELECT * FROM t WHERE id > 99", conn), check_exact=True)
    got = core.read_sql_chunked("SELECT * FROM t WHERE id BETWEEN ? AND ? ORDER BY id", conn, params=(2, 4))
    pd.testing.assert_frame_equal(got, pd.read_sql_query("SELECT * FROM t WHERE id BETWEEN ? AND ? ORDER BY id", conn, params=(2, 4)), check_exact=True)
