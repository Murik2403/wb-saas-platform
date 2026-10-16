from datetime import date
from unittest.mock import patch
import pandas as pd
from tests.base import DbTestCase
from db import core, ingest
from calculations import build_dashboard

class DashboardProjectionTests(DbTestCase):
    def seed(self, count=20, raw_size=1024):
        core.ensure_financial_schema()
        raw='{"opaque":"'+'x'*raw_size+'"}'
        with core.connect() as conn:
            conn.executemany("INSERT INTO orders(id,order_date,nm_id,price_with_disc,raw_json) VALUES (?, '2026-09-01', 123, 1000, ?)",[(str(i),raw) for i in range(count)])
            conn.executemany("INSERT INTO sales(id,sale_date,nm_id,price_with_disc,raw_json) VALUES (?, '2026-09-01', 123, 1000, ?)",[(str(i),raw) for i in range(count)])
            conn.executemany("INSERT INTO financial_report(rrd_id,operation_date,nm_id,quantity,retail_amount,retail_price_withdisc_rub,ppvz_for_pay,operation_name,doc_type_name,raw_json) VALUES (?, '2026-09-01',123,1,1000,1000,900,'Продажа','Продажа',?)",[(i,raw) for i in range(count)])

    def test_projection_excludes_large_payloads_without_deleting_source(self):
        self.seed(count=400,raw_size=65536)
        for full, slim in [(core.read_table('orders'),core.read_table('orders',omit_raw=True)),(ingest.read_financial(),ingest.read_financial(omit_raw=True))]:
            self.assertIn('raw_json',full.columns)
            self.assertNotIn('raw_json',slim.columns)
            pd.testing.assert_frame_equal(full.drop(columns='raw_json'),slim)
            self.assertGreater(full.memory_usage(deep=True).sum()/slim.memory_usage(deep=True).sum(),100)
        with core.connect() as conn:
            self.assertGreater(conn.execute('SELECT length(raw_json) FROM orders LIMIT 1').fetchone()[0],65536)

    def test_dashboard_projection_preserves_every_money_result_and_frame(self):
        self.seed()
        projected=build_dashboard(date(2026,9,1),date(2026,9,19))
        with patch('calculations.read_table',side_effect=lambda name, **kwargs: core.read_table(name)),patch('calculations.read_financial',side_effect=lambda **kwargs: ingest.read_financial()):
            ordinary=build_dashboard(date(2026,9,1),date(2026,9,19))
        self.assertEqual(projected.kpi,ordinary.kpi)
        self.assertEqual(projected.financial,ordinary.financial)
        for field in ['daily','products','ads','stocks','alerts','financial_products','financial_unallocated','hourly']:
            pd.testing.assert_frame_equal(getattr(projected,field),getattr(ordinary,field))

    def test_quality_omits_large_financial_payload_without_changing_diagnostics(self):
        import ui_helpers
        self.seed(count=80, raw_size=65536)
        context = ({}, True, {"status": "ok", "finished_at": "2026-09-01T12:00:00+03:00"}, None)
        memory = []
        def measured_reader(**kwargs):
            frame = ingest.read_financial(**kwargs)
            memory.append(int(frame.memory_usage(deep=True).sum()))
            return frame
        with patch('ui_helpers.read_financial', side_effect=measured_reader):
            projected = ui_helpers.build_data_quality_overview(*context)
        self.assertLess(memory[0], 80 * 4096, "Diagnostic reader materialized the raw report payload")
        with patch('ui_helpers.read_financial', side_effect=lambda **kwargs: ingest.read_financial()):
            ordinary = ui_helpers.build_data_quality_overview(*context)
        self.assertEqual(projected[1], ordinary[1])
        for index in (0, 2, 3):
            pd.testing.assert_frame_equal(projected[index], ordinary[index])
        with core.connect() as conn:
            self.assertGreater(conn.execute('SELECT length(raw_json) FROM financial_report LIMIT 1').fetchone()[0], 65536)
