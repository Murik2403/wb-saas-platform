from datetime import date

from tests.base import DbTestCase
from db import core
from calculations import build_dashboard
from ads_analytics import compute_ads_analytics, metrics


class AdsAnalyticsTests(DbTestCase):
    def seed(self):
        # campaign, day, nm, views, clicks, spend, atbs, orders, canceled, revenue
        rows = [
            (1, "2026-09-01", 0, 1000, 50, 200.0, 10, 5, 1, 1000.0),
            (1, "2026-09-01", 10, 600, 30, 120.0, 6, 3, 0, 600.0),   # product allocation: must not double count
            (1, "2026-09-01", 20, 400, 20, 80.0, 4, 2, 1, 400.0),
            (1, "2026-09-02", 0, 2000, 100, 300.0, 20, 10, 2, 3000.0),
            (2, "2026-09-02", 0, 500, 0, 50.0, 0, 0, 0, 0.0),
            (1, "2026-08-31", 0, 400, 10, 100.0, 2, 1, 0, 250.0),   # previous window (1 day long? no: 2 days -> 08-30..08-31)
            (1, "2026-09-03", 0, 9999, 99, 999.0, 9, 9, 9, 9999.0),  # outside
        ]
        with core.connect() as conn:
            conn.executemany(
                "INSERT INTO ads_daily(campaign_id,day,nm_id,views,clicks,spend,atbs,orders_count,canceled,ad_revenue,raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,'{}')",
                rows)

    def test_totals_dedupe_and_formulas_hand_checked(self):
        self.seed()
        r = compute_ads_analytics(date(2026, 9, 1), date(2026, 9, 2))
        t = r["totals"]
        # nm_id=0 rows only: views 3500, clicks 150, spend 550, carts 30, orders 15, revenue 4000, canceled 3
        self.assertEqual((t["views"], t["clicks"], t["spend"], t["carts"], t["orders"]), (3500, 150, 550.0, 30, 15))
        self.assertEqual(t["ad_revenue"], 4000.0)
        self.assertEqual(t["ctr"], round(150 / 3500 * 100, 2))  # 4.29
        self.assertEqual(t["cpc"], 3.67)       # 550/150
        self.assertEqual(t["cpm"], 157.14)     # 550/3500*1000
        self.assertEqual(t["cart_conversion"], 20.0)   # 30/150
        self.assertEqual(t["order_conversion"], 10.0)  # 15/150
        self.assertEqual(t["cpo"], 36.67)      # 550/15
        self.assertEqual(t["drr"], 13.75)      # 550/4000
        self.assertEqual(t["canceled"], 3)

    def test_totals_equal_existing_kpi_cards(self):
        self.seed()
        k = build_dashboard(date(2026, 9, 1), date(2026, 9, 2)).kpi
        t = compute_ads_analytics(date(2026, 9, 1), date(2026, 9, 2))["totals"]
        self.assertAlmostEqual(t["spend"], k["ad_spend"])
        self.assertAlmostEqual(t["ad_revenue"], k["ad_revenue"])
        self.assertAlmostEqual(t["clicks"], k["ad_clicks"])
        self.assertAlmostEqual(t["ctr"], k["ad_ctr"], places=2)

    def test_previous_window_by_day_and_campaigns(self):
        self.seed()
        r = compute_ads_analytics(date(2026, 9, 1), date(2026, 9, 2))
        self.assertEqual((r["previous_start"], r["previous_end"]), ("2026-08-30", "2026-08-31"))
        p = r["previous_totals"]
        self.assertEqual((p["spend"], p["cpo"], p["drr"]), (100.0, 100.0, 40.0))
        self.assertEqual([d["day"] for d in r["by_day"]], ["2026-09-01", "2026-09-02"])
        self.assertEqual(r["by_day"][0]["spend"], 200.0)
        self.assertEqual([c["campaign_id"] for c in r["campaigns"]], [1, 2])  # by spend desc
        c2 = r["campaigns"][1]
        self.assertIsNone(c2["cpc"]); self.assertIsNone(c2["cpo"]); self.assertIsNone(c2["drr"])
        self.assertEqual(c2["ctr"], 0.0)

    def test_no_data_and_zero_division(self):
        r = compute_ads_analytics(date(2026, 9, 1), date(2026, 9, 2))
        self.assertFalse(r["has_data"]); self.assertIsNone(r["previous_totals"])
        self.assertIsNone(r["totals"]["ctr"]); self.assertEqual(r["by_day"], [])
        self.assertIsNone(metrics({k: 0.0 for k in ("views", "clicks", "spend", "atbs", "orders_count", "canceled", "ad_revenue")})["cpm"])

    def test_bucketing_caps_points(self):
        self.seed()
        r = compute_ads_analytics(date(2026, 6, 1), date(2026, 9, 2))  # 94 days -> bucket 2
        self.assertEqual(r["bucket_days"], 2)
        self.assertLessEqual(len(r["by_day"]), 60)
        self.assertAlmostEqual(sum(d["spend"] for d in r["by_day"]), r["totals"]["spend"])

    def test_fallback_to_all_rows_without_total_rows(self):
        with core.connect() as conn:
            conn.execute("INSERT INTO ads_daily(campaign_id,day,nm_id,views,clicks,spend,raw_json) VALUES (1,'2026-09-01',10,100,5,20,'{}')")
        self.assertEqual(compute_ads_analytics(date(2026, 9, 1), date(2026, 9, 1))["totals"]["spend"], 20.0)
