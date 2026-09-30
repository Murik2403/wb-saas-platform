import csv
import io
import sqlite3
import uuid
from .base import DbTestCase
from db.core import connect
from db.ozon_pnl import COLUMNS, add_cost_lot, confirm_import, preview_import, pnl, capture_source_snapshot


def event(identifier='sale-1', **changes):
    row=dict(external_event_id=identifier,accrued_at='2026-09-04T12:00:00+03:00',movement_at='2026-09-03T12:00:00+03:00',kind='sale',sku='123',posting_ref='posting-1',return_of='',quantity='3',revenue_rub='100.00',commission_rub='-10.00',logistics_rub='-5.00',other_rub='0.00',source_total_rub='85.00',currency='RUB',document_ref='Statement-1')
    return {**row,**changes}


def csv_rows(rows):
    output=io.StringIO(); writer=csv.DictWriter(output,fieldnames=COLUMNS);writer.writeheader();writer.writerows(rows)
    return output.getvalue()


class OzonPnlTests(DbTestCase):
    def lot(self,rate=10,quantity=2,**changes):
        args=dict(namespace='main',sku='123',received_at='2026-09-01T12:00:00+03:00',quantity_units=quantity,unit_cost_rub=str(rate),document_ref='receipt-1',reason='historical document',request_key=str(uuid.uuid4()),actor='owner');args.update(changes)
        return add_cost_lot(**args)

    def load(self,rows,namespace='main'):
        source=csv_rows(rows);preview=preview_import(source,namespace)
        return confirm_import(source,namespace,preview['fingerprint'],'owner')

    def report(self,**changes):
        args=dict(start='2026-09-01',end='2026-09-30',namespace='main');args.update(changes)
        return pnl(**args)

    def test_two_lots_sale_return_resale_exact_and_reconciled(self):
        self.lot();self.lot(20,3,received_at='2026-09-02T12:00:00+03:00')
        returned=event('return-1',kind='return',return_of='sale-1',quantity='1',accrued_at='2026-09-06T12:00:00+03:00',movement_at='2026-09-05T12:00:00+03:00',revenue_rub='-30.00',commission_rub='3.00',logistics_rub='-2.00',source_total_rub='-29.00')
        self.load([event(),returned,event('sale-2',quantity='2',posting_ref='posting-2',accrued_at='2026-09-09T12:00:00+03:00',movement_at='2026-09-08T12:00:00+03:00')])
        result=self.report()
        self.assertEqual(result['known_cogs_rub'],'60.00')
        self.assertEqual(result['totals']['source_total_rub'],'141.00')
        self.assertEqual(result['result_after_fifo_rub'],'81.00')
        self.assertEqual(result['unknown_cost_units'],0)
        self.assertTrue(result['components_reconciled'])
        self.assertFalse(result['marketplace_source_complete'])

    def test_unknown_is_null_not_zero_and_late_receipt_not_eligible(self):
        self.lot(quantity=1);self.lot(quantity=5,received_at='2026-09-10T00:00:00+03:00')
        self.load([event()]);result=self.report()
        self.assertEqual(result['known_cogs_rub'],'10.00')
        self.assertEqual(result['unknown_cost_units'],2)
        self.assertIsNone(result['result_after_fifo_rub'])
        self.assertIsNone(result['historical_cogs_rub'])

    def test_financial_period_uses_moscow_accrual_not_movement(self):
        self.lot(quantity=3)
        self.load([event(accrued_at='2026-09-04T22:00:00Z')])
        self.assertFalse(self.report(end='2026-09-04')['loaded'])
        self.assertEqual(self.report(start='2026-09-05',end='2026-09-05')['known_cogs_rub'],'30.00')

    def test_reimport_duplicates_and_fifo_do_not_accumulate(self):
        self.lot(quantity=3)
        first=self.load([event(),event()]);again=self.load([event()])
        self.assertEqual(first['duplicates'],1);self.assertEqual(again['imported'],0)
        self.assertEqual(first['projection_revision'],again['projection_revision'])
        self.assertEqual(self.report()['known_cogs_rub'],'30.00')
        with connect() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM ozon_document_events').fetchone()[0],1)

    def test_conflict_rolls_back_whole_import(self):
        self.load([event()])
        changed=event(revenue_rub='101.00',source_total_rub='86.00')
        with self.assertRaises(ValueError):self.load([event('new'),changed])
        with connect() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM ozon_document_events').fetchone()[0],1)

    def test_returns_unmatched_excess_and_sku_posting_mismatch_unknown(self):
        self.lot(quantity=3);self.load([event()])
        for index,changes in enumerate([dict(return_of='missing'),dict(quantity='4'),dict(sku='456'),dict(posting_ref='other')]):
            self.load([{**event(f'return-{index}',kind='return',return_of='sale-1',quantity='1',accrued_at=f'2026-09-{6+index:02}T00:00:00Z',movement_at=f'2026-09-{6+index:02}T00:00:00Z',revenue_rub='-30.00',commission_rub='0',logistics_rub='0',source_total_rub='-30.00'),**changes}])
        result=self.report();self.assertEqual(result['unknown_cost_units'],7);self.assertEqual(result['known_cogs_rub'],'30.00')
        self.assertIsNone(result['result_after_fifo_rub'])

    def test_partial_returns_restore_original_cost_only_once(self):
        self.lot(quantity=3);self.load([event()])
        self.load([event('r1',kind='return',return_of='sale-1',quantity='2',movement_at='2026-09-05T00:00:00Z',revenue_rub='-60',commission_rub='0',logistics_rub='0',source_total_rub='-60')])
        self.load([event('r2',kind='return',return_of='sale-1',quantity='2',movement_at='2026-09-06T00:00:00Z',revenue_rub='-60',commission_rub='0',logistics_rub='0',source_total_rub='-60')])
        result=self.report();self.assertEqual(result['known_cogs_rub'],'10.00');self.assertEqual(result['unknown_cost_units'],2)

    def test_historical_backfill_has_new_immutable_projection(self):
        self.load([event()]);before=self.report()
        self.lot(quantity=3);after=self.report()
        self.assertGreater(after['projection_revision'],before['projection_revision'])
        self.assertIsNone(before['historical_cogs_rub']);self.assertEqual(after['historical_cogs_rub'],'30.00')

    def test_wb_and_namespace_isolation(self):
        with connect() as conn:before=[tuple(row) for row in conn.execute('SELECT * FROM sales')]
        self.lot(quantity=3);self.load([event()],namespace='other-seller')
        self.assertEqual(self.report(namespace='other-seller')['unknown_cost_units'],3)
        with connect() as conn:self.assertEqual([tuple(row) for row in conn.execute('SELECT * FROM sales')],before)

    def test_zero_cost_is_explicit_and_receipt_replay_is_idempotent(self):
        key=str(uuid.uuid4());first=self.lot(rate=0,quantity=3,request_key=key);again=self.lot(rate=0,quantity=3,request_key=key)
        self.assertEqual(first,again)
        with self.assertRaises(ValueError):self.lot(rate=1,quantity=3,request_key=key)
        self.load([event()]);self.assertEqual(self.report()['historical_cogs_rub'],'0.00')

    def test_validation_missing_unknown_money_sign_currency_and_date(self):
        for change in [dict(commission_rub=''),dict(source_total_rub='84'),dict(currency='CNY'),dict(quantity='1.5'),dict(accrued_at='2026-09-01T00:00:00'),dict(revenue_rub='-100',source_total_rub='-115')]:
            with self.assertRaises(ValueError):self.load([event(**change)])
        with self.assertRaises(ValueError):self.load([])
        self.assertFalse(self.report()['loaded'])

    def test_fees_no_inventory_and_no_double_subtraction(self):
        self.load([event('fee',kind='fee',sku='',posting_ref='',movement_at='',quantity='0',revenue_rub='0',commission_rub='0',logistics_rub='-12.34',other_rub='0',source_total_rub='-12.34')])
        result=self.report();self.assertEqual(result['result_after_fifo_rub'],'-12.34');self.assertEqual(result['known_cogs_rub'],'0.00')

    def test_all_histories_immutable_even_replace_and_raw_reimport(self):
        self.lot();self.load([event()])
        with connect() as conn:
            capture_source_snapshot(conn,{'accruals':[]});capture_source_snapshot(conn,{'accruals':[]});capture_source_snapshot(conn,{'accruals':[{'unknown':'preserved'}]})
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM ozon_raw_source_versions').fetchone()[0],2)
        for table in ['ozon_document_requests','ozon_document_events','ozon_document_cost_lots','ozon_cost_projections','ozon_raw_source_versions']:
            for sql in [f'DELETE FROM {table}',f"UPDATE {table} SET actor='changed'",f'INSERT OR REPLACE INTO {table} SELECT * FROM {table}']:
                with self.assertRaises(sqlite3.IntegrityError):
                    with connect() as conn:conn.execute(sql)

    def test_partial_return_cost_is_proportional_not_first_cheap_unit(self):
        self.lot(10,1);self.lot(20,1,received_at='2026-09-02T00:00:00Z')
        self.load([event(quantity='2'),event('r',kind='return',quantity='1',return_of='sale-1',movement_at='2026-09-05T00:00:00Z',accrued_at='2026-09-06T00:00:00Z',revenue_rub='-30',commission_rub='0',logistics_rub='0',source_total_rub='-30')])
        result=self.report(start='2026-09-06',end='2026-09-06')
        self.assertEqual(result['historical_cogs_rub'],'-15.00')
        self.assertEqual(result['lines'][0]['historical_cost']['cost_method'],'proportional_original_sale')

    def test_return_from_partial_unknown_sale_and_resale_remain_unknown(self):
        self.lot(10,1)
        self.load([event(quantity='2'),event('r',kind='return',quantity='1',return_of='sale-1',movement_at='2026-09-05T00:00:00Z',accrued_at='2026-09-06T00:00:00Z',revenue_rub='-30',commission_rub='0',logistics_rub='0',source_total_rub='-30')])
        result=self.report(start='2026-09-06',end='2026-09-06')
        self.assertEqual(result['unknown_cost_units'],1);self.assertIsNone(result['historical_cogs_rub'])
        self.load([event('resale',quantity='1',posting_ref='new',movement_at='2026-09-08T00:00:00Z',accrued_at='2026-09-08T00:00:00Z')])
        self.assertEqual(self.report(start='2026-09-08',end='2026-09-08')['unknown_cost_units'],1)

    def test_repeated_partial_returns_reverse_exact_total_original_cost(self):
        self.lot(10,1);self.lot(20,2,received_at='2026-09-02T00:00:00Z');self.load([event()])
        for index in range(3):
            self.load([event(f'r-{index}',kind='return',quantity='1',return_of='sale-1',movement_at=f'2026-09-{6+index:02}T00:00:00Z',revenue_rub='-30',commission_rub='0',logistics_rub='0',source_total_rub='-30')])
        self.assertEqual(self.report()['historical_cogs_rub'],'0.00')

    def test_same_time_receipts_use_append_order_not_uuid(self):
        self.lot(10,1,request_key='ffffffff-ffff-4fff-afff-ffffffffffff')
        self.lot(20,1,request_key='00000000-0000-4000-a000-000000000000')
        self.load([event(quantity='1')]);self.assertEqual(self.report()['historical_cogs_rub'],'10.00')

    def test_full_return_group_resale_closes_cost_residual(self):
        self.lot(10,2);self.lot(20,1,received_at='2026-09-02T00:00:00Z')
        self.load([event(),event('r',kind='return',quantity='3',return_of='sale-1',movement_at='2026-09-05T00:00:00Z',revenue_rub='-100',commission_rub='0',logistics_rub='0',source_total_rub='-100'),event('resale',quantity='3',posting_ref='new',movement_at='2026-09-08T00:00:00Z')])
        self.assertEqual(self.report()['historical_cogs_rub'],'40.00')
        with connect() as conn:
            import json
            payload=json.loads(conn.execute('SELECT payload_json FROM ozon_cost_projections ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertEqual([lot['remaining_cost_rub'] for lot in payload['available_lots']],['0','0','0'])

    def test_return_original_age_but_not_available_before_actual_return(self):
        self.lot(10,1);self.lot(20,2,received_at='2026-09-02T00:00:00Z')
        self.load([event(quantity='1'),event('r',kind='return',quantity='1',return_of='sale-1',movement_at='2026-09-07T00:00:00Z',revenue_rub='-100',commission_rub='0',logistics_rub='0',source_total_rub='-100'),event('before-return',quantity='1',posting_ref='new',movement_at='2026-09-06T00:00:00Z'),event('after-return',quantity='1',posting_ref='newer',movement_at='2026-09-08T00:00:00Z')])
        result=self.report();costs={row['external_event_id']:row['historical_cost']['known_cogs_rub'] for row in result['lines']}
        self.assertEqual(costs['before-return'],'20');self.assertEqual(costs['after-return'],'10')

    def test_external_receipt_identity_survives_new_request_nonce(self):
        one=self.lot(external_lot_id="document-line-1")
        again=self.lot(external_lot_id="document-line-1")
        self.assertEqual(one["id"],again["id"])
        with self.assertRaises(ValueError):self.lot(rate=20,external_lot_id="document-line-1")
        with connect() as conn:self.assertEqual(conn.execute("SELECT COUNT(*) FROM ozon_document_cost_lots").fetchone()[0],1)

    def test_request_nonce_cannot_be_reused_for_different_document(self):
        nonce=str(uuid.uuid4());self.lot(request_key=nonce,external_lot_id="first")
        with self.assertRaises(ValueError):self.lot(request_key=nonce,external_lot_id="second")
        with connect() as conn:self.assertEqual(conn.execute("SELECT COUNT(*) FROM ozon_document_cost_lots").fetchone()[0],1)
