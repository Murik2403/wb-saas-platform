"""The lean financial-row reader of the FIFO reconciliation equals the previous eager one, value for value.

The reference below is the previous code (SELECT *, one dict of every column per row, `raw_json` reduced to its SRID except for
protected ids). The new reader keeps nine columns per ordinary row and reads protected rows in full.
"""
import json
from datetime import date
from unittest.mock import patch

import pandas as pd

from db import core, financial_fifo_reconciliation as reconciliation
from db.financial_source import fingerprint
from tests.base import DbTestCase
from tests import test_financial_fifo_reconciliation as fixtures

START, END_EXCLUSIVE = date(2026, 6, 1), date(2026, 7, 1)

# every shape a legacy or damaged payload can take; each must give the same SRID as before
PAYLOADS = [
    ('valid', json.dumps({"srid": "A-1", "x": 1})),
    ('upper', json.dumps({"SRID": "A-2"})),
    ('both_keys_lower_wins', json.dumps({"srid": "A-3", "SRID": "OTHER"})),
    ('empty_lower_falls_to_upper', json.dumps({"srid": "", "SRID": "A-4"})),
    ('zero_lower_falls_to_upper', json.dumps({"srid": 0, "SRID": "A-5"})),
    ('number', json.dumps({"srid": 12345})),
    ('float', json.dumps({"srid": 1.0})),
    ('true', json.dumps({"srid": True})),
    ('padded', json.dumps({"srid": "  spaced  "})),
    ('nested_object', json.dumps({"srid": {"a": 1}})),
    ('list_value', json.dumps({"srid": [1, 2]})),
    ('null_value', json.dumps({"srid": None})),
    ('nan_literal', '{"srid": NaN}'),
    ('top_level_array', '[{"srid": "A-6"}]'),
    ('top_level_string', '"A-7"'),
    ('top_level_number', '42'),
    ('invalid', '{"srid": "A-8"'),
    ('garbage', 'not json at all'),
    ('empty_text', ''),
    ('blank_text', '   '),
    ('bom', '﻿{"srid": "A-9"}'),
    ('unicode', json.dumps({"srid": "Заказ-№1"}, ensure_ascii=False)),
    ('huge', json.dumps({"srid": "A-10", "pad": "x" * 300000})),
    ('duplicate_keys', '{"srid": "first", "srid": "second"}'),
]


def reference_rows(conn, start, end_exclusive, preserve_raw_ids):
    finance = conn.execute(
        """SELECT *
             FROM financial_report
            WHERE operation_date >= ? AND operation_date < ?
            ORDER BY operation_date,rrd_id""",
        (start.isoformat(), end_exclusive.isoformat()),
    )
    rows = []
    for source in finance:
        row = dict(source)
        if row.get("rrd_id") not in preserve_raw_ids and "raw_json" in row:
            row["raw_json"] = reconciliation._srid_payload(row["raw_json"])
        rows.append(row)
    return rows


class FifoRowsEquivalenceTests(DbTestCase):
    NM_ID = fixtures.FinancialFifoReconciliationTests.NM_ID
    _event = fixtures.FinancialFifoReconciliationTests._event

    def setUp(self):
        super().setUp()
        core.ensure_financial_schema()

    def _insert(self, rrd_id, raw, *, operation="Продажа", day="2026-06-02", quantity=1, nm_id=None):
        with core.connect() as conn:
            conn.execute(
                "INSERT INTO financial_report(rrd_id,operation_date,sale_date,nm_id,supplier_article,quantity,operation_name,doc_type_name,raw_json,"
                "retail_amount,ppvz_for_pay,delivery_rub,deduction) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rrd_id, day, "2026-06-01", nm_id or self.NM_ID, "ART", quantity, operation, "Продажа", raw, 100.5, 80.25, 7.0, 1.5))

    def _fill(self):
        for index, (_, raw) in enumerate(PAYLOADS, 1):
            self._insert(index, raw, operation="Продажа" if index % 3 else "Возврат", day=f"2026-06-{(index % 25) + 1:02d}")
        self._insert(900, json.dumps({"srid": "SERVICE"}), operation="Логистика")  # not a sale or a return
        self._insert(901, json.dumps({"srid": "OUTSIDE"}), day="2026-08-01")  # outside the window

    def _protect(self, *rrd_ids):
        with core.connect() as conn:
            for table in ("warehouse_financial_bindings", "warehouse_financial_projection", "migration_historical_documents"):
                conn.execute(f"CREATE TABLE IF NOT EXISTS {table}(rrd_id INTEGER)")
            for rrd_id in rrd_ids:
                conn.execute("INSERT INTO warehouse_financial_bindings(rrd_id) VALUES (?)", (rrd_id,))

    def test_every_payload_shape_gives_the_same_rows_as_the_eager_reader(self):
        self._fill()
        with core.connect() as conn:
            self.assertEqual(list(reconciliation._window_financial_rows(conn, START, END_EXCLUSIVE, set())),
                             [self._lean(row) for row in reference_rows(conn, START, END_EXCLUSIVE, set())])

    def _lean(self, row):
        """The reference row restricted to the columns the reconciliation reads (the rest is dropped by design)."""
        return {key: row[key] for key in reconciliation._RECONCILE_COLUMNS if key in row}

    def test_protected_rows_are_read_in_full_and_their_fingerprint_is_unchanged(self):
        self._fill()
        self._protect(2, 5, 22)
        with core.connect() as conn:
            lean = list(reconciliation._window_financial_rows(conn, START, END_EXCLUSIVE, {2, 5, 22}))
            eager = reference_rows(conn, START, END_EXCLUSIVE, {2, 5, 22})
        by_id = {row["rrd_id"]: row for row in lean}
        for expected in eager:
            if expected["rrd_id"] in {2, 5, 22}:
                self.assertEqual(by_id[expected["rrd_id"]], expected)
                self.assertEqual(fingerprint(by_id[expected["rrd_id"]]), fingerprint(expected))
            else:
                self.assertEqual(by_id[expected["rrd_id"]], self._lean(expected))
        self.assertEqual([row["rrd_id"] for row in lean], [row["rrd_id"] for row in eager], "same order")

    def test_the_whole_reconciliation_dataframe_is_identical(self):
        self._fill()
        self._protect(3, 7)
        for index in range(1, 12):
            self._event(f"S{index}", srid=["A-1", "A-2", "A-3", "A-4", "A-5", "12345", "1.0", "True", "spaced", "A-9", "Заказ-№1"][index - 1], cost=10.0 + index)
        new = reconciliation.read_financial_fifo_reconciliation("2026-06-01", "2026-06-30")
        with patch.object(reconciliation, "_window_financial_rows", reference_rows):
            old = reconciliation.read_financial_fifo_reconciliation("2026-06-01", "2026-06-30")
        pd.testing.assert_frame_equal(new, old, check_exact=True)
        self.assertGreater(len(new), 20)
        self.assertIn("exact", set(new["status"]))


class ProjectionPlumbingTests(FifoRowsEquivalenceTests):
    """Only protected rows reach the projections; the changed rows land at the right place of the frame."""

    def test_projected_rows_land_on_their_own_rows_and_the_rest_is_untouched(self):
        self._fill()
        protected = {2, 5, 22}
        self._protect(*protected)
        base = reconciliation.read_financial_fifo_reconciliation("2026-06-01", "2026-06-30")

        seen = []

        def apply(conn, sources, rows):
            seen.append([source["rrd_id"] for source in sources])
            for source, row in zip(sources, rows):
                row.update(status="manual_source", reason="plumbing", eligible_fifo_cogs_rub=float(source["rrd_id"]), layer_sources=["x"])
            return rows

        with patch("shared_warehouse_finance.apply_projection", apply), patch("db.finished_history_import.project", lambda conn, sources, rows: rows):
            projected = reconciliation.read_financial_fifo_reconciliation("2026-06-01", "2026-06-30")
        self.assertEqual(sorted(seen[0]), sorted(protected))
        self.assertEqual(len(projected), len(base))
        pd.testing.assert_series_equal(projected["rrd_id"], base["rrd_id"])
        changed = projected["rrd_id"].isin(protected)
        for column in base.columns:  # values only: a column's dtype depends on all of its rows, which the projection changed
            left, right = projected.loc[~changed, column].tolist(), base.loc[~changed, column].tolist()
            self.assertEqual([None if value != value else value for value in left] if column == "eligible_fifo_cogs_rub" else left,
                             [None if value != value else value for value in right] if column == "eligible_fifo_cogs_rub" else right, column)
        self.assertTrue((projected.loc[changed, "reason"] == "plumbing").all())
        self.assertEqual(sorted(projected.loc[changed, "eligible_fifo_cogs_rub"].tolist()), sorted(float(i) for i in protected))
        self.assertEqual(projected.loc[changed, "layer_sources"].tolist(), [["x"]] * 3)

    def test_without_any_protected_row_the_projections_are_not_called(self):
        self._fill()
        calls = []
        with patch("shared_warehouse_finance.apply_projection", lambda *a: calls.append(1)), patch("db.finished_history_import.project", lambda *a: calls.append(1)):
            reconciliation.read_financial_fifo_reconciliation("2026-06-01", "2026-06-30")
        self.assertEqual(calls, [])
