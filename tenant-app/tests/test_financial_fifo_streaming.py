"""Bound raw-payload retention without weakening financial-source fingerprints."""
from contextlib import contextmanager
import json
from unittest.mock import patch

import pandas as pd

from db import core, financial_fifo_reconciliation as reconciliation
from db.financial_source import fingerprint
from tests.base import DbTestCase
from tests import test_financial_fifo_reconciliation as fixtures


class FinancialFifoStreamingTests(DbTestCase):
    NM_ID = fixtures.FinancialFifoReconciliationTests.NM_ID
    _finance = fixtures.FinancialFifoReconciliationTests._finance
    _event = fixtures.FinancialFifoReconciliationTests._event

    def setUp(self):
        super().setUp()
        core.ensure_financial_schema()

    def _run(self):
        return reconciliation.read_financial_fifo_reconciliation('2026-06-01', '2026-06-30')

    def test_large_payloads_stream_and_shrink_before_reconcile(self):
        self._finance(1, operation_date='2026-06-02', sale_date='2026-06-01', quantity=1,
                      raw_payload={'SRID': 'SR-1', 'nested': {'unused': 'x' * 65536}, 'chrt_id': 10})
        self._event('S1', source_raw_payload={'srid': 'SR-1', 'unused': 'y' * 65536})
        original_connect, original_reconcile = reconciliation.connect, reconciliation._reconcile
        observed = []

        class Cursor:
            def __init__(self, cursor): self.cursor = cursor
            def __iter__(self): return iter(self.cursor)
            def fetchall(self):
                self.cursor.close()
                raise AssertionError('Marketplace payload cursor must stream')

        class Connection:
            def __init__(self, connection): self.connection = connection
            def execute(self, sql, *args):
                cursor = self.connection.execute(sql, *args)
                if ('FROM financial_report' in sql and 'SELECT *' in sql) or 's.raw_json AS sales_raw_json' in sql:
                    return Cursor(cursor)
                return cursor

        @contextmanager
        def guarded_connect():
            with original_connect() as connection:
                yield Connection(connection)

        def inspect(financial_rows, events, allocations, rates):
            observed.append(True)
            first = next(iter(financial_rows))
            self.assertLess(len(first['raw_json']), 100)
            self.assertLess(len(events[0]['sales_raw_json']), 100)
            self.assertEqual(reconciliation._json_srid(first['raw_json']), 'SR-1')
            return original_reconcile(financial_rows, events, allocations, rates)

        with patch.object(reconciliation, 'connect', guarded_connect), patch.object(reconciliation, '_reconcile', inspect):
            result = self._run()
        self.assertTrue(observed)
        self.assertEqual(result.iloc[0]['status'], 'exact')
        self.assertEqual(result.iloc[0]['eligible_fifo_cogs_rub'], 80)
        with core.connect() as connection:
            self.assertGreater(len(connection.execute('SELECT raw_json FROM financial_report').fetchone()[0]), 65536)
            self.assertGreater(len(connection.execute('SELECT raw_json FROM sales').fetchone()[0]), 65536)

    def test_every_binding_and_archive_preserves_original_fingerprint(self):
        tables = ('warehouse_financial_bindings', 'warehouse_financial_projection', 'migration_historical_documents')
        for index, table in enumerate(tables, 1):
            self._finance(index, operation_date='2026-06-02', sale_date='2026-06-01', quantity=1,
                          raw_payload={'srid': 'SR-' + str(index), 'chrt_id': 10 + index, 'nested': {'original': ['a', index, 'x' * 32768]}})
        self._finance(4, operation_date='2026-06-02', sale_date='2026-06-01', quantity=1,
                      raw_payload={'srid': 'ordinary', 'unused': 'x' * 32768})
        with core.connect() as connection:
            before = {row['rrd_id']: dict(row) for row in connection.execute('SELECT * FROM financial_report')}
            for index, table in enumerate(tables, 1):
                # Even other-shop/inactive records protect full source payloads.
                connection.execute('CREATE TABLE ' + table + '(rrd_id INTEGER,shop_id INTEGER,active INTEGER)')
                connection.execute('INSERT INTO ' + table + ' VALUES(?,999,0)', (index,))
        captured = []

        def inspect(_connection, financial_rows, rows):
            captured.append(True)
            facts = {row['rrd_id']: row for row in financial_rows}
            for source_id in (1, 2, 3):
                self.assertEqual(facts[source_id], before[source_id])
                self.assertEqual(fingerprint(facts[source_id]), fingerprint(before[source_id]))
            # An ordinary row is never looked up by the projections (no binding can reference it), so it is not handed to them;
            # its payload is reduced to the SRID before the reconciliation (checked in the first test of this class).
            self.assertNotIn(4, facts)
            return rows

        with patch('shared_warehouse_finance.apply_projection', inspect), patch('db.finished_history_import.project', inspect):
            self._run()
        self.assertEqual(len(captured), 2)

    def test_whole_output_matches_original_payloads_for_edge_cases(self):
        payloads = [{'srid': ' SR-1 ', 'SRID': 'ignored', 'chrt_id': 11}, {'srid': '', 'SRID': 'SR-1'},
                    {'srid': 17}, {'srid': 0, 'SRID': 'upper'}, {'srid': {'nested': 'value'}},
                    {}, ['not', 'object'], {'srid': '\u043a\u0438\u0440\u0438\u043b\u043b\u0438\u0446\u0430'}]
        for index, payload in enumerate(payloads, 1):
            self._finance(index, operation_date='2026-06-02', sale_date='2026-06-01', quantity=1, raw_payload=payload)
        self._event('S1', source_raw_payload={'SRID': 'SR-1', 'nested': {'unused': 'x' * 32768}})
        self._finance(99, operation_date='2026-06-02', sale_date='2026-06-01', quantity=1)
        with core.connect() as connection:
            connection.execute('UPDATE financial_report SET raw_json=? WHERE rrd_id=99', ('{malformed',))
        projected = self._run()
        with patch.object(reconciliation, '_srid_payload', lambda value: value):
            original = self._run()
        pd.testing.assert_frame_equal(projected, original, check_exact=True)
        self.assertTrue((projected[projected['rrd_id'].isin((1, 2, 99))]['reason'] == 'duplicate_financial_rows_for_srid').any())
        self.assertEqual(projected.loc[projected['rrd_id'] == 99, 'srid'].iloc[0], '')

    def test_window_repeats_inside_the_same_read_snapshot(self):
        from datetime import date
        self._finance(1, operation_date='2026-06-02', sale_date='2026-06-01', quantity=1)
        with core.connect() as connection:
            connection.execute('BEGIN')
            window = reconciliation._window_financial_rows(connection, date(2026, 6, 1), date(2026, 7, 1), set())
            self.assertNotIsInstance(window, list, 'window must not retain every source row')
            first = list(window)
            with core.connect() as writer:
                writer.execute("UPDATE financial_report SET quantity=2 WHERE rrd_id=1")
            self.assertEqual(list(window), first, 'all reconciliation passes must see one snapshot')
        with core.connect() as connection:
            self.assertEqual(connection.execute('SELECT quantity FROM financial_report WHERE rrd_id=1').fetchone()[0], 2)
