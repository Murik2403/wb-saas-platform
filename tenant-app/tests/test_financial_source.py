import base64
import json
import sqlite3
from types import SimpleNamespace
from unittest import mock
from tests.base import DbTestCase
from db.core import connect
from db.ingest import upsert_financial
from db import financial_source as source


class FinancialSourceTests(DbTestCase):
    def row(self, rrd_id=1, amount=100):
        return dict(rrd_id=rrd_id,nm_id=100,quantity=1,rr_dt='2026-09-30',
                    doc_type_name='Продажа',retail_amount=amount,chrt_id=10)

    def current(self):
        with connect() as conn:
            row=dict(conn.execute('SELECT * FROM financial_report WHERE rrd_id=1').fetchone())
            return source.current(conn,row)

    def test_verified_noop_dedup_changed_fact_and_legacy_downgrade(self):
        upsert_financial([self.row()],source_identity='a'*64)
        self.assertEqual(self.current()['identity'],'a'*64)
        upsert_financial([self.row()],source_identity='a'*64)
        with connect() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM financial_source_ingestions').fetchone()[0],1)
        upsert_financial([self.row(amount=200)],source_identity='a'*64)
        self.assertEqual(self.current()['identity'],'a'*64)
        upsert_financial([self.row(amount=200)])
        self.assertIsNone(self.current()['identity'])
        self.assertEqual(self.current()['source_kind'],'legacy_unverified')
        upsert_financial([self.row(amount=200)],source_identity='a'*64)
        self.assertEqual(self.current()['identity'],'a'*64)
        with connect() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM financial_source_ingestions').fetchone()[0],4)

    def test_seller_collision_rolls_back_entire_batch_and_journal(self):
        upsert_financial([self.row()],source_identity='a'*64)
        with self.assertRaises(ValueError):upsert_financial([self.row(2),self.row(amount=900)],source_identity='b'*64)
        with connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM financial_report').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM financial_source_ingestions').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT retail_amount FROM financial_report').fetchone()[0],100)

    def test_source_guards_and_direct_financial_change_invalidates_proof(self):
        upsert_financial([self.row()],source_identity='a'*64)
        for sql in ('UPDATE financial_source_ingestions SET identity=NULL','DELETE FROM financial_source_ingestions',
                    'INSERT OR REPLACE INTO financial_source_ingestions SELECT * FROM financial_source_ingestions'):
            with self.assertRaises(sqlite3.IntegrityError):
                with connect() as conn:conn.execute(sql)
        with connect() as conn:conn.execute('UPDATE financial_report SET penalty=1')
        self.assertIsNone(self.current())
        upsert_financial([self.row()],source_identity='a'*64)
        with connect() as conn:conn.execute("UPDATE financial_report SET raw_json=json_set(raw_json,'$.chrt_id',20)")
        self.assertIsNone(self.current())

    def test_primary_frozen_seller_rejects_rotated_token_ingestion(self):
        with connect() as conn:
            conn.execute('CREATE TABLE warehouse_shops(shop_id INTEGER,identity TEXT)')
            conn.execute('INSERT INTO warehouse_shops VALUES(0,?)',('a'*64,))
        with self.assertRaises(ValueError):upsert_financial([self.row()],source_identity='b'*64)
        with connect() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM financial_report').fetchone()[0],0)

    def test_sync_captures_actual_client_identity_before_remote_rotation(self):
        import sync
        token=lambda sid:'x.'+base64.urlsafe_b64encode(json.dumps({'sid':sid}).encode()).decode().rstrip('=')+'.x'
        client=SimpleNamespace(token=token('before'))
        expected=source.captured_identity(client)
        def read(*args,**kwargs):
            client.token=token('after')
            return [self.row()]
        client.get_financial_report=read
        with mock.patch.object(sync,'WBAPI',return_value=client),mock.patch.object(sync,'get_token',return_value=client.token):
            sync._sync_finances_locked(1)
        self.assertEqual(self.current()['identity'],expected)
        self.assertNotEqual(expected,source.captured_identity(client))
