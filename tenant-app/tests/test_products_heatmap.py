import json
from datetime import date

from tests.base import DbTestCase
from db import core
from calculations import build_dashboard
from products_heatmap import compute_products_heatmap


class ProductsHeatmapTests(DbTestCase):
    def seed(self):
        with core.connect() as conn:
            conn.executemany(
                "INSERT INTO sales(id,sale_date,nm_id,supplier_article,price_with_disc,is_return,raw_json) VALUES (?,?,?,?,?,?,'{}')",
                [("s1", "2026-09-01T10:00:00", 10, "A", 100, 0), ("s2", "2026-09-01T11:00:00", 10, "A", 50, 0),
                 ("s3", "2026-09-03T10:00:00", 10, "A", 70, 1), ("s4", "2026-09-03T12:00:00", 20, "B", 200, 0),
                 ("s5", "2026-08-31T12:00:00", 10, "A", 999, 0)])
            conn.executemany(
                "INSERT INTO orders(id,order_date,nm_id,supplier_article,finished_price,is_cancel,raw_json) VALUES (?,?,?,?,?,?,'{}')",
                [("o1", "2026-09-01T10:00:00", 10, "A", 120, 0), ("o2", "2026-09-01T10:05:00", 10, "A", 80, 0),
                 ("o3", "2026-09-02T10:00:00", 20, "B", 300, 0), ("o4", "2026-09-02T11:00:00", 20, "B", 300, 1)])
            conn.executemany(
                "INSERT INTO ads_daily(campaign_id,day,nm_id,spend,orders_count,raw_json) VALUES (?,?,?,?,?,'{}')",
                [(1, "2026-09-01", 0, 500, 9), (1, "2026-09-01", 10, 40.5, 2), (1, "2026-09-02", 10, 10, 1),
                 (2, "2026-09-02", 20, 7, 0)])
            conn.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES (10,'ART-10','Футболка','{}')")

    def rows(self, r):
        return {x["nm_id"]: x for x in r["rows"]}

    def test_sales_and_revenue_with_returns(self):
        self.seed()
        r = compute_products_heatmap("revenue", date(2026, 9, 1), date(2026, 9, 4))
        self.assertEqual(r["dates"], ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"])
        by = self.rows(r)
        self.assertEqual(by[10]["values"], [150.0, 0.0, -70.0, 0.0])  # missing day = 0
        self.assertEqual(by[10]["total"], 80.0)
        self.assertEqual(by[20]["values"], [0.0, 0.0, 200.0, 0.0])
        self.assertEqual([x["nm_id"] for x in r["rows"]], [20, 10])  # by total desc
        self.assertEqual(r["column_totals"], [150.0, 0.0, 130.0, 0.0])
        self.assertEqual((r["min"], r["max"]), (-70.0, 200.0))
        self.assertEqual((by[10]["name"], by[10]["vendor_code"]), ("Футболка", "ART-10"))
        self.assertEqual(by[20]["vendor_code"], "B")  # fallback to supplier_article of the rows
        s = compute_products_heatmap("sales", date(2026, 9, 1), date(2026, 9, 4))
        self.assertEqual(self.rows(s)[10]["values"], [2.0, 0.0, -1.0, 0.0])

    def test_matches_existing_per_sku_totals(self):
        self.seed()
        start, end = date(2026, 9, 1), date(2026, 9, 12)
        products = build_dashboard(start, end).products.set_index("Артикул WB")
        by = self.rows(compute_products_heatmap("revenue", start, end))
        for nm, row in by.items():
            self.assertAlmostEqual(row["total"], products.loc[nm, "Выручка"])

    def test_orders_skip_cancelled(self):
        self.seed()
        r = compute_products_heatmap("orders", date(2026, 9, 1), date(2026, 9, 2))
        by = self.rows(r)
        self.assertEqual(by[10]["values"], [2.0, 0.0])
        self.assertEqual(by[20]["values"], [0.0, 1.0])
        rub = self.rows(compute_products_heatmap("orders_rub", date(2026, 9, 1), date(2026, 9, 2)))
        self.assertEqual(rub[10]["values"], [200.0, 0.0])
        self.assertEqual(rub[20]["values"], [0.0, 300.0])

    def test_ads_use_only_sku_rows_and_report_campaign_level(self):
        self.seed()
        r = compute_products_heatmap("ad_spend", date(2026, 9, 1), date(2026, 9, 2))
        by = self.rows(r)
        self.assertEqual(by[10]["values"], [40.5, 10.0])
        self.assertEqual(by[20]["values"], [0.0, 7.0])
        self.assertEqual(r["column_totals"], [40.5, 17.0])
        self.assertEqual(r["campaign_level_total"], 500.0)
        o = compute_products_heatmap("ad_orders", date(2026, 9, 1), date(2026, 9, 2))
        self.assertEqual(self.rows(o)[10]["values"], [2.0, 1.0])
        self.assertNotIn(20, self.rows(o))  # all-zero SKU is dropped

    def test_empty_window_and_empty_db(self):
        r = compute_products_heatmap("orders", date(2026, 1, 1), date(2026, 1, 3))
        self.assertEqual((r["rows"], r["other"], r["min"], r["max"], r["grand_total"]), ([], None, 0.0, 0.0, 0.0))
        self.assertEqual(r["column_totals"], [0.0, 0.0, 0.0])
        self.seed()
        r = compute_products_heatmap("orders", date(2026, 1, 1), date(2026, 1, 3))
        self.assertEqual(r["rows"], [])
        json.dumps(r, allow_nan=False)

    def test_long_window_buckets_and_other_row_keep_totals_exact(self):
        self.seed()
        start, end = date(2026, 6, 1), date(2026, 9, 12)  # 104 days -> 2-day buckets
        r = compute_products_heatmap("revenue", start, end, max_rows=1)
        self.assertEqual((r["bucket_days"], len(r["dates"])), (2, 52))
        self.assertEqual(len(r["rows"]), 1)
        self.assertEqual(r["rows"][0]["nm_id"], 10)  # 999+100+50-70 = 1079 > 200
        self.assertEqual(r["rows"][0]["total"], 1079.0)
        self.assertEqual(r["other"]["count"], 1)
        self.assertEqual(r["other"]["total"], 200.0)
        self.assertEqual(r["other"]["values"][47], 200.0)
        for i in range(52):
            self.assertAlmostEqual(r["column_totals"][i], r["rows"][0]["values"][i] + r["other"]["values"][i])
        self.assertEqual(r["grand_total"], 1279.0)
        self.assertEqual(sum(r["rows"][0]["values"]), 1079.0)

    def test_unknown_metric(self):
        with self.assertRaises(ValueError):
            compute_products_heatmap("profit", date(2026, 9, 1), date(2026, 9, 2))
