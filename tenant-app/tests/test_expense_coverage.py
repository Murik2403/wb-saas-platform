import sqlite3
import uuid
from .base import DbTestCase
from db.core import connect
from db.manual_expenses import save_manual_expense, read_manual_expenses, set_manual_expense_inclusion
from db.expense_coverage import read_coverage, save_attestation, fingerprint


class ExpenseCoverageTests(DbTestCase):
    scope = dict(nm_id=123, start='2026-09-01', end='2026-09-30')

    def rows(self):
        return [{**row, 'allocated_rub': row['amount_rub'], 'excluded_from_result': not bool(row['include_in_management_result'])} for row in read_manual_expenses(**self.scope)]

    def add(self, amount=0, included=False):
        return save_manual_expense(nm_id=123, article='taxes', amount_rub=amount, allocation_basis='total', source='manual', period_start=self.scope['start'], period_end=self.scope['end'], include_in_management_result=included)

    def save(self, state='confirmed_zero', **extra):
        args = dict(**self.scope, article='taxes', state=state, source_ref='ledger #12', reason='Reviewed period', actor='owner@test', request_key=str(uuid.uuid4()), rows=self.rows(), baseline={'units': 2})
        args.update(extra)
        return save_attestation(**args)

    def coverage(self, **extra):
        args = dict(**self.scope, rows=self.rows(), baseline={'units': 2}); args.update(extra)
        return read_coverage(**args)

    def test_legacy_and_excluded_zero_are_unknown(self):
        self.add(); self.add(50)
        self.assertEqual(self.coverage()['count'], 0)
        self.assertTrue(self.coverage()['preliminary'])

    def test_zero_and_not_applicable_never_create_expense(self):
        self.save(); self.save('not_applicable')
        self.assertEqual(self.coverage()['count'], 1)
        self.assertEqual(self.rows(), [])
        self.assertEqual(len(self.coverage()['history']), 2)
        self.assertIn('confirmed_zero', self.coverage()['history'][0]['before_json'])

    def test_estimate_and_unknown_not_covered(self):
        self.save('estimated'); self.assertEqual(self.coverage()['estimated'], ['taxes'])
        self.assertEqual(self.coverage()['count'], 0)
        self.save('unknown'); self.assertIn('taxes', self.coverage()['missing'])

    def test_nonzero_cannot_be_zero_or_not_applicable(self):
        self.add(50, True)
        for state in ['confirmed_zero', 'not_applicable']:
            with self.assertRaises(ValueError): self.save(state)
        self.save('confirmed'); self.assertEqual(self.coverage()['count'], 1)
        self.assertEqual(self.rows()[0]['amount_rub'], 50)

    def test_period_sku_and_source_changes_invalidate(self):
        self.save()
        self.assertEqual(self.coverage(nm_id=456)['count'], 0)
        self.assertEqual(self.coverage(end='2026-09-29')['count'], 0)
        changed = self.coverage(baseline={'units': 3})
        self.assertEqual(changed['count'], 0)
        self.assertTrue(next(r for r in changed['items'] if r['article']=='taxes')['stale'])

    def test_inclusion_amount_and_effective_allocation_invalidate(self):
        identifier = self.add(50)
        self.save(); set_manual_expense_inclusion(identifier, True)
        self.assertEqual(self.coverage()['count'], 0)
        self.save('confirmed')
        changed = self.rows(); changed[0]['allocated_rub'] = 25
        self.assertEqual(self.coverage(rows=changed)['count'], 0)
        with connect() as conn: conn.execute('UPDATE manual_expenses SET amount_rub=51 WHERE id=?', (identifier,))
        self.assertEqual(self.coverage()['count'], 0)

    def test_replay_and_collision_rollback(self):
        key = str(uuid.uuid4())
        identifier = self.save(request_key=key)
        self.assertEqual(self.save(request_key=key), identifier)
        with self.assertRaises(ValueError): self.save('estimated', request_key=key)
        self.assertEqual(len(self.coverage()['history']), 1)

    def test_snapshot_and_concurrent_rows_guard(self):
        old = self.rows(); expected = fingerprint('taxes', old, {'units': 2})
        self.add(30, True)
        with self.assertRaises(ValueError): self.save('confirmed', rows=old)
        with self.assertRaises(ValueError): self.save('confirmed', expected_fingerprint=expected)
        self.assertEqual(len(self.coverage()['history']), 0)

    def test_unknown_effective_amount_and_invalid_input(self):
        self.add(1, True); rows=self.rows(); rows[0]['allocated_rub']=None
        with self.assertRaises(ValueError): self.save('confirmed', rows=rows)
        for extra in [dict(article='other'), dict(state='auto'), dict(reason=''), dict(source_ref='\n'), dict(request_key='wrong'), dict(nm_id=True)]:
            with self.assertRaises(ValueError): self.save(**extra)

    def test_history_is_immutable_and_utc(self):
        self.save(); self.assertTrue(self.coverage()['history'][0]['created_at'].endswith('+00:00'))
        for sql in ['DELETE FROM expense_article_attestations', "UPDATE expense_article_attestations SET state='unknown'"]:
            with self.assertRaises(sqlite3.IntegrityError):
                with connect() as conn: conn.execute(sql)

    def test_replace_cannot_destroy_journal(self):
        self.save()
        with self.assertRaises(sqlite3.IntegrityError):
            with connect() as conn:
                conn.execute("INSERT OR REPLACE INTO expense_article_attestations SELECT * FROM expense_article_attestations")
        self.assertEqual(len(self.coverage()['history']), 1)

    def test_nonzero_source_cannot_be_marked_zero(self):
        for state in ['confirmed_zero','not_applicable']:
            with self.assertRaises(ValueError): self.save(state, baseline={'units':2,'observed_nonzero':{'taxes':{'absolute_observed_rub':100}}})
        self.assertEqual(self.coverage()['count'],0)
