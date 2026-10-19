"""Reading only the report period in SQL gives the dashboard exactly what reading whole tables gave."""
import json
from datetime import date, timedelta
from unittest.mock import patch

import pandas as pd

import calculations
from db import core
from db.ingest import read_financial, read_financial_period
from tests.base import DbTestCase

TODAY = date(2026, 6, 20)


def stamp(day, fmt="T"):
    return f"{day.isoformat()}{fmt}10:30:00" if fmt else day.isoformat()


class PeriodReadTests(DbTestCase):
    def setUp(self):
        super().setUp()
        core.ensure_financial_schema()

    def _clear(self):
        with core.connect() as conn:
            for table in ("orders", "sales", "ads_daily", "financial_report", "stocks", "products_catalog"):
                conn.execute(f"DELETE FROM {table}")

    def _fill(self, fmt="T", extra_order_dates=()):
        with core.connect() as conn:
            for i in range(40):
                day = TODAY - timedelta(days=i)
                nm = 100 + i % 5
                conn.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,brand,raw_json) VALUES (?,?,?,?,'{}') ON CONFLICT DO NOTHING",
                             (nm, f"A{nm}", f"P{nm}", "B"))
                conn.execute("INSERT INTO orders(id,order_date,last_change,nm_id,supplier_article,total_price,finished_price,price_with_disc,is_cancel,raw_json)"
                             " VALUES (?,?,?,?,?,?,?,?,?,'{}')",
                             (f"o{i}", stamp(day, fmt), stamp(day, fmt), nm, f"A{nm}", 1000 + i, 900 + i, 800 + i, i % 7 == 0))
                conn.execute("INSERT INTO sales(id,sale_date,last_change,nm_id,supplier_article,sale_id,total_price,finished_price,price_with_disc,is_return,raw_json)"
                             " VALUES (?,?,?,?,?,?,?,?,?,?,'{}')",
                             (f"s{i}", stamp(day, fmt), stamp(day, fmt), nm, f"A{nm}", f"s{i}", 1000 + i, 900 + i, 800 + i, i % 9 == 0))
                conn.execute("INSERT INTO ads_daily(campaign_id,day,nm_id,spend,views,clicks,ad_revenue,raw_json) VALUES (?,?,?,?,?,?,?,'{}')",
                             (1, stamp(day, fmt), 0, 50.5 + i, 100 + i, 5 + i, 300 + i))
                conn.execute("INSERT INTO financial_report(rrd_id,operation_date,sale_date,nm_id,supplier_article,quantity,retail_amount,"
                             "retail_price_withdisc_rub,ppvz_for_pay,delivery_rub,commission,operation_name,doc_type_name,raw_json)"
                             " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (1000 + i, stamp(day, fmt), stamp(day, fmt), nm, f"A{nm}", 1, 700.5 + i, 800 + i, 600.25 + i, 10.5, 20.75,
                              "Продажа" if i % 6 else "Возврат", "Продажа", json.dumps({"srid": f"R{i}"})))
            for day in extra_order_dates:
                conn.execute("INSERT INTO orders(id,order_date,nm_id,raw_json) VALUES (?,?,?,'{}')", (f"x{day}", day, 100))
            for when, quantity in ((TODAY - timedelta(days=3), 5), (TODAY, 9)):
                conn.execute("INSERT INTO stocks(snapshot_at,nm_id,chrt_id,warehouse_id,warehouse_name,region_name,quantity,raw_json)"
                             " VALUES (?,?,?,?,?,?,?,'{}')", (stamp(when, fmt), 100, 1, 1, "W", "R", quantity))

    def _dashboard(self, days, force_whole=False):
        end, start = TODAY, TODAY - timedelta(days=days - 1)
        if not force_whole:
            return calculations.build_dashboard(start, end)
        with patch.object(calculations, "read_table_period", lambda *a, **k: None), \
                patch.object(calculations, "read_latest_stocks", lambda **k: None), \
                patch.object(calculations, "read_financial_period", lambda *a, **k: None):
            return calculations.build_dashboard(start, end)

    def _same(self, days):
        pushed, whole = self._dashboard(days), self._dashboard(days, force_whole=True)
        for field in pushed.__dataclass_fields__:
            a, b = getattr(pushed, field), getattr(whole, field)
            if isinstance(a, pd.DataFrame):
                pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True), check_exact=True, obj=f"{field} ({days} days)")
            else:
                self.assertEqual(json.dumps(a, sort_keys=True, default=str), json.dumps(b, sort_keys=True, default=str), field)

    def test_dashboard_is_identical_for_1_7_30_90_days_with_datetime_and_date_only_columns(self):
        for fmt in ("T", " ", ""):
            with self.subTest(format=fmt or "date only"):
                self._clear()
                self._fill(fmt)
                for days in (1, 7, 30, 90):
                    self._same(days)

    def test_mixed_formats_fall_back_to_the_whole_table_and_stay_identical(self):
        self._fill("T", extra_order_dates=(TODAY.isoformat(), (TODAY - timedelta(days=2)).isoformat() + " 08:00:00"))
        self.assertIsNone(core.read_table_period("orders", "order_date", TODAY - timedelta(days=6), TODAY))
        self._same(7)

    def test_period_read_refuses_null_zone_and_second_format_and_empty_table(self):
        start, end = TODAY - timedelta(days=6), TODAY
        self.assertIsNone(core.read_table_period("orders", "order_date", start, end), "empty table")
        self._fill("T")
        self.assertIsNotNone(core.read_table_period("orders", "order_date", start, end))
        for bad in (None, stamp(TODAY, "T") + "+03:00", stamp(TODAY, "T") + "Z", TODAY.isoformat(), "2026/06/01", "not a date"):
            with core.connect() as conn:
                conn.execute("DELETE FROM orders WHERE id='bad'")
                conn.execute("INSERT INTO orders(id,order_date,nm_id,raw_json) VALUES ('bad',?,100,'{}')", (bad,))
            self.assertIsNone(core.read_table_period("orders", "order_date", start, end), repr(bad))

    def test_period_read_returns_rowid_order_not_date_order(self):
        with core.connect() as conn:
            for i, day in enumerate((5, 1, 9, 3, 7)):
                conn.execute("INSERT INTO orders(id,order_date,nm_id,raw_json) VALUES (?,?,100,'{}')", (f"o{i}", stamp(TODAY - timedelta(days=day))))
        got = core.read_table_period("orders", "order_date", TODAY - timedelta(days=9), TODAY)
        self.assertEqual(got["id"].tolist(), ["o0", "o1", "o2", "o3", "o4"])
        self.assertEqual(got["id"].tolist(), core.read_table("orders", omit_raw=True)["id"].tolist())

    def test_period_bounds_are_inclusive_and_exact(self):
        self._fill("T")
        got = core.read_table_period("orders", "order_date", TODAY - timedelta(days=9), TODAY - timedelta(days=2))
        self.assertEqual(sorted(got["id"]), sorted(f"o{i}" for i in range(2, 10)))

    def test_financial_period_read_adds_the_row_with_the_latest_date_once(self):
        self._fill("T")
        with core.connect() as conn:
            conn.execute("INSERT INTO financial_report(rrd_id,operation_date,nm_id,raw_json) VALUES (5000,?,100,'{}')", (stamp(TODAY + timedelta(days=5)),))
        got = read_financial_period(TODAY - timedelta(days=6), TODAY, omit_raw=True)
        whole = read_financial(omit_raw=True)
        self.assertEqual(got["rrd_id"].tolist().count(5000), 1)
        self.assertEqual(pd.to_datetime(got["operation_date"]).max(), pd.to_datetime(whole["operation_date"]).max())
        order = whole["rrd_id"].tolist()
        self.assertEqual(got["rrd_id"].tolist(), [r for r in order if r in set(got["rrd_id"])], "rowid order")

    def test_latest_stocks_returns_only_the_newest_snapshot(self):
        self._fill("T")
        self.assertEqual(core.read_latest_stocks(omit_raw=True)["quantity"].tolist(), [9])
        with core.connect() as conn:
            conn.execute("UPDATE stocks SET snapshot_at='2026-06-17 10:30:00' WHERE quantity=5")
        self.assertIsNone(core.read_latest_stocks(omit_raw=True), "two formats in one column")
