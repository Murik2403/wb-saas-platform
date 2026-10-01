from datetime import date

from tests.base import DbTestCase
from db import core
from calculations import build_dashboard
from products_service import compute_products
from products_trend import compute_products_trend, products_totals


class ProductsTrendTests(DbTestCase):
    def seed(self):
        core.ensure_financial_schema()
        rows = [  # id, day, nm, price_with_disc, is_return
            ("1", "2026-09-01", 10, 100, 0), ("2", "2026-09-01", 10, 50, 0),
            ("3", "2026-09-03", 10, 70, 1), ("4", "2026-09-03", 20, 200, 0),
            ("5", "2026-09-10", 10, 30, 0), ("6", "2026-08-31", 10, 999, 0),
        ]
        with core.connect() as conn:
            conn.executemany("INSERT INTO sales(id,sale_date,nm_id,price_with_disc,is_return,raw_json) VALUES (?,?,?,?,?,'{}')", rows)

    def test_daily_series_hand_checked(self):
        self.seed()
        r = compute_products_trend(date(2026, 9, 1), date(2026, 9, 4))
        self.assertEqual(r["bucket_days"], 1)
        self.assertEqual(r["dates"], ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"])
        by = {i["nm_id"]: i for i in r["items"]}
        self.assertEqual(by[10]["revenue"], [150.0, 0.0, -70.0, 0.0])
        self.assertEqual(by[10]["units"], [2, 0, -1, 0])
        self.assertEqual(by[20]["revenue"], [0.0, 0.0, 200.0, 0.0])

    def test_series_sum_equals_existing_per_sku_totals(self):
        self.seed()
        start, end = date(2026, 9, 1), date(2026, 9, 12)
        products = build_dashboard(start, end).products.set_index("Артикул WB")
        for item in compute_products_trend(start, end)["items"]:
            self.assertAlmostEqual(sum(item["revenue"]), products.loc[item["nm_id"], "Выручка"])
            self.assertEqual(sum(item["units"]), products.loc[item["nm_id"], "Продажи"])

    def test_long_window_is_bucketed_and_sums_preserved(self):
        self.seed()
        start, end = date(2026, 6, 1), date(2026, 9, 12)  # 104 days -> 2-day buckets, 52 points
        r = compute_products_trend(start, end)
        self.assertEqual(r["bucket_days"], 2)
        self.assertEqual(len(r["dates"]), 52)
        by = {i["nm_id"]: i for i in r["items"]}
        self.assertEqual(len(by[10]["revenue"]), 52)
        self.assertEqual(sum(by[10]["revenue"]), 100 + 50 - 70 + 30 + 999)
        # 2026-09-03 is day 94 -> bucket 47; 09-01 is day 92 -> bucket 46
        self.assertEqual(by[20]["revenue"][47], 200.0)

    def test_empty_window(self):
        self.seed()
        r = compute_products_trend(date(2025, 1, 1), date(2025, 1, 3))
        self.assertEqual(r["items"], [])
        self.assertEqual(len(r["dates"]), 3)


def test_products_totals_hand_checked():
    rows = [
        {"revenue": 1000, "margin": 100, "days_of_stock": 3},
        {"revenue": 500, "margin": None, "days_of_stock": 7},   # 7 is not < 7
        {"revenue": 500, "margin": -50, "days_of_stock": None},
        {"revenue": 0, "margin": 0, "days_of_stock": 0},        # 0 counts as risk
    ]
    t = products_totals(rows)
    assert t == {"revenue": 2000.0, "margin_pct": 2.5, "at_risk": 2, "sku_count": 4}
    assert products_totals([]) == {"revenue": 0, "margin_pct": 0.0, "at_risk": 0, "sku_count": 0}


def test_totals_match_compute_products_shape():
    import pandas as pd
    class Data:
        products = pd.DataFrame([{"Артикул WB": 1, "Выручка": 200.0, "Запас, дней": 2.0}])
        financial_products = pd.DataFrame([{"Артикул WB": 1, "Чистая прибыль": 20.0, "Чистая маржинальность, %": 10.0}])
    payload = compute_products({"data": Data, "period": "7d", "start": date(2026, 9, 1), "end": date(2026, 9, 7)})
    assert products_totals(payload["products"]) == {"revenue": 200.0, "margin_pct": 10.0, "at_risk": 1, "sku_count": 1}
