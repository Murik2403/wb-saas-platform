from datetime import date

import pandas as pd

from calculations import build_dashboard
from db.fifo_materials import save_costs
from db.ingest import upsert_ads, upsert_financial
from monthly_pnl import month_ranges, monthly_pnl
from tests.base import DbTestCase

AUG, SEP = date(2026, 8, 1), date(2026, 9, 30)


def fin(rrd_id, day, kind, qty, **kw):
    return dict(rrd_id=rrd_id, rr_dt=day, nm_id=100, doc_type_name=kind, quantity=qty, **kw)


class MonthlyPnlTests(DbTestCase):
    def test_unsigned_returns_compensation_and_campaign_settlements_match_dashboard(self):
        upsert_financial([
            fin(101, '2026-09-10', 'Продажа', 2, retail_price_withdisc_rub=1000, retail_amount=2000, ppvz_for_pay=1500, ppvz_sales_commission=400, acquiring_fee=100),
            fin(102, '2026-09-11', 'Возврат', 1, retail_price_withdisc_rub=1000, retail_amount=1000, ppvz_for_pay=750, ppvz_sales_commission=200, acquiring_fee=50),
            fin(103, '2026-09-12', 'Продажа', 1, supplier_oper_name='Компенсация ущерба', retail_price_withdisc_rub=1000, ppvz_for_pay=90),
            fin(104, '2026-09-13', '', 0, deduction=50, deduction_reason='WB Продвижение'),
        ])
        upsert_ads([dict(campaign_id=1, day='2026-09-10', nm_id=0, spend=50)])
        save_costs(pd.DataFrame([{'nm_id':100,'cost_per_wb_unit':100}]))
        actual = monthly_pnl(date(2026,9,1), SEP)['total']
        expected = build_dashboard(date(2026,9,1), SEP).financial
        for monthly_key, dashboard_key in [('result','profit'),('sales','sales'),('realization','gross_sales'),('units','net_units'),('commission','commission'),('acquiring','acquiring')]:
            self.assertAlmostEqual(actual[monthly_key], expected[dashboard_key], places=2, msg=monthly_key)

    def seed(self, with_cost=True):
        upsert_financial([
            fin(1, "2026-08-10", "Продажа", 2, retail_price_withdisc_rub=1000, retail_amount=1800, ppvz_for_pay=1500,
                ppvz_sales_commission=200, acquiring_fee=40, delivery_rub=100, storage_fee=20, penalty=10,
                deduction=5, acceptance=3, additional_payment=7),
            fin(2, "2026-08-20", "Возврат", 1, retail_price_withdisc_rub=1000, retail_amount=-900, ppvz_for_pay=-750,
                ppvz_sales_commission=-100, acquiring_fee=-20),
            fin(3, "2026-09-05", "Продажа", 3, retail_price_withdisc_rub=500, retail_amount=1400, ppvz_for_pay=1100,
                ppvz_sales_commission=250, acquiring_fee=30, delivery_rub=60, rebill_logistic_cost=15),
        ])
        upsert_ads([
            dict(campaign_id=1, day="2026-08-12", nm_id=0, spend=50),
            dict(campaign_id=1, day="2026-09-04", nm_id=0, spend=70),
            dict(campaign_id=1, day="2026-09-04", nm_id=100, spend=999),  # allocation row, must be ignored
            dict(campaign_id=1, day="2026-09-20", nm_id=0, spend=500),  # after last report row, not in profit
        ])
        if with_cost:
            save_costs(pd.DataFrame([{"nm_id": 100, "cost_per_wb_unit": 100}]))

    def test_month_ranges_clip_partial_months(self):
        self.assertEqual(month_ranges(date(2026, 8, 15), date(2026, 10, 3)), [
            (date(2026, 8, 15), date(2026, 8, 31)), (date(2026, 9, 1), date(2026, 9, 30)), (date(2026, 10, 1), date(2026, 10, 3))])
        self.assertEqual(month_ranges(date(2026, 9, 3), date(2026, 9, 9)), [(date(2026, 9, 3), date(2026, 9, 9))])

    def test_hand_checked_months_without_cost(self):
        self.seed(with_cost=False)
        result = monthly_pnl(AUG, SEP)
        aug, sep = result["months"]
        self.assertEqual(result["data_status"], "ok")
        self.assertEqual(result["data_through"], "2026-09-05")
        self.assertEqual((aug["realization"], aug["spp_discount"], aug["sales"]), (1000, -100, 900))
        self.assertEqual((aug["commission"], aug["acquiring"], aug["logistics"], aug["storage"], aug["penalties"]), (100, 20, 100, 20, 10))
        self.assertEqual(aug["advertising"], 50)
        self.assertEqual(aug["other"], 31)  # 900-750-100-20 + 5 + 3 - 7
        self.assertEqual(aug["result"], 569)  # to_pay 750 + add 7 - (100+10+20+5+3) - ad 50
        self.assertEqual((sep["realization"], sep["sales"], sep["logistics"], sep["advertising"]), (1500, 1400, 75, 70))
        self.assertEqual(sep["other"], 20)  # 1400-1100-250-30
        self.assertEqual(sep["result"], 1100 - 75 - 70)
        self.assertTrue(sep["data_incomplete"])
        self.assertFalse(aug["data_incomplete"])
        self.assertFalse(aug["partial"])
        self.assertEqual(result["total"]["result"], aug["result"] + sep["result"])

    def test_totals_equal_whole_period_dashboard(self):
        self.seed()
        result = monthly_pnl(AUG, SEP)
        fin_total = build_dashboard(AUG, SEP).financial
        total = result["total"]
        self.assertGreater(fin_total["cost"], 0)
        self.assertAlmostEqual(total["cost"], fin_total["cost"], places=2)
        self.assertAlmostEqual(total["result"], fin_total["profit"], places=2)
        self.assertAlmostEqual(total["sales"], fin_total["sales"], places=2)
        self.assertAlmostEqual(total["realization"], fin_total["gross_sales"], places=2)
        self.assertAlmostEqual(total["advertising"], fin_total["financial_ad_spend"], places=2)
        self.assertAlmostEqual(total["commission"], fin_total["commission"], places=2)
        self.assertAlmostEqual(total["logistics"], fin_total["logistics"] + fin_total["rebill_logistics"], places=2)

    def test_period_inside_one_month_and_empty_store(self):
        self.seed(with_cost=False)
        one = monthly_pnl(date(2026, 8, 11), date(2026, 8, 31))
        self.assertEqual([m["month"] for m in one["months"]], ["2026-08"])
        self.assertTrue(one["months"][0]["partial"])
        self.assertEqual(one["months"][0]["sales"], -900)  # only the 08-20 return falls in the window
        empty = monthly_pnl(date(2025, 1, 1), date(2025, 2, 10))
        self.assertEqual(empty["data_status"], "no_financial_data")
        self.assertEqual(empty["total"]["result"], 0)
        self.assertEqual(len(empty["months"]), 2)
