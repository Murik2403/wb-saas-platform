import unittest

from price_advice import advise_sku, advise_all, base_for_final, final_price, margin_pct

# Hand-checked fixture: p = 20000/20 = 1000, k = 1 - 16000/20000 = 0.2,
# net profit/unit = 100 (10%), fixed F = 1000*0.8 - 100 = 700,
# break-even = 700/0.8 = 875, price for 20% = 700/0.6 = 1166.67.
ROW = dict(nm_id=1, vendor_code="A", sale_units=20, units=20, revenue=20000, to_pay=16000,
           net_profit=2000, cost=8000, unsold_cost_units=0, ad_spend=1000, wb_expenses=2000)


def adv(price=1000, disc=0, target=20, row=None, **kw):
    return advise_sku({**ROW, **(row or {})}, price, disc, target, **kw)


class PriceAdviceTests(unittest.TestCase):
    def test_primitives(self):
        self.assertEqual(final_price(1250, 20), 1000)
        self.assertEqual(final_price(105, 50), 53)  # 52.5 rounds half up like the UI
        self.assertAlmostEqual(margin_pct(1000, 0.2, 700), 10.0)
        self.assertEqual(base_for_final(1076.92, 20), 1347)  # 1346.15 -> 1347 -> final 1078
        self.assertEqual(final_price(1347, 20), 1078)

    def test_model_numbers_and_cap(self):
        r = adv()
        self.assertEqual(r["breakeven_final"], 875)
        self.assertEqual(r["est_margin_now_pct"], 10.0)
        self.assertEqual(r["status"], "recommend")
        # want 1167 but capped at +15% = 1150; margin (920-700)/1150 = 19.13%
        self.assertEqual((r["recommended_price"], r["recommended_final"]), (1150, 1150))
        self.assertEqual(r["est_margin_after_pct"], 19.1)
        self.assertIn("Шаг ограничен 15%", r["reason"])
        self.assertIn("спрос на цену не прогнозируется", r["reason"])
        self.assertIn("себестоимость 400", r["reason"])
        self.assertIn("прочее 150", r["reason"])

    def test_target_inside_cap_and_rounding_up(self):
        r = adv(target=15)  # 700/0.65 = 1076.92 -> 1077; (861.6-700)/1077 = 15.0%
        self.assertEqual(r["recommended_price"], 1077)
        self.assertGreaterEqual(r["est_margin_after_pct"], 15.0)
        self.assertFalse("Шаг ограничен" in r["reason"])

    def test_discount_kept(self):
        r = adv(price=1250, disc=20, target=15)
        self.assertEqual((r["current_final"], r["recommended_price"], r["recommended_final"]), (1000, 1347, 1078))
        self.assertEqual(r["current_discount"], 20)
        self.assertGreaterEqual(r["est_margin_after_pct"], 15.0)

    def test_discount_99(self):
        r = adv(price=100000, disc=99, target=15)
        self.assertEqual((r["current_final"], r["recommended_price"], r["recommended_final"]), (1000, 107693, 1077))

    def test_ok_when_target_met_never_lowers(self):
        r = adv(price=1500, disc=0, target=20)  # margin (1200-700)/1500 = 33.3%
        self.assertEqual(r["status"], "ok")
        self.assertIsNone(r["recommended_price"])

    def test_below_breakeven_capped_still_below(self):
        r = adv(price=700, target=20)  # final 700 < 875; cap 805 still below
        self.assertEqual(r["status"], "below_breakeven")
        self.assertEqual(r["recommended_price"], 805)
        self.assertIn("остаётся ниже безубыточности", r["note"])

    def test_below_breakeven_step_reaches_floor(self):
        r = adv(price=800, target=20)  # cap 920 >= 875; margin (736-700)/920 = 3.9%
        self.assertEqual((r["status"], r["recommended_price"], r["est_margin_after_pct"]), ("below_breakeven", 920, 3.9))
        self.assertNotIn("остаётся ниже", r["note"])
        self.assertEqual(r["est_margin_now_pct"], -7.5)

    def test_negative_realised_margin(self):
        r = adv(row=dict(net_profit=-2000))  # F = 800 + 100 = 900, break-even 1125
        self.assertEqual(r["breakeven_final"], 1125)
        self.assertEqual(r["status"], "below_breakeven")
        self.assertEqual(r["realised_margin_pct"], -10.0)

    def test_insufficient_cases(self):
        for row, text in [
            (dict(sale_units=9), "Мало продаж"),
            (dict(units=0), "Нет чистых продаж"),
            (dict(revenue=0), "Нет чистых продаж"),
            (dict(cost=0), "Себестоимость"),
            (dict(unsold_cost_units=3), "Себестоимость"),
            (dict(net_profit=None), "чистой прибыли"),
            (dict(to_pay=19900), "комиссии"),   # k = 0.5% -> unreliable
            (dict(to_pay=5000), "комиссии"),    # k = 75%
        ]:
            r = adv(row=row)
            self.assertEqual(r["status"], "insufficient_data", row)
            self.assertIn(text, r["note"])
            self.assertIsNone(r["recommended_price"])

    def test_unreachable_target(self):
        r = adv(row=dict(to_pay=9000), target=50)  # k = 0.55
        self.assertEqual(r["status"], "insufficient_data")
        self.assertIn("недостижима", r["note"])

    def test_no_price_and_upload(self):
        self.assertEqual(adv(price=None)["status"], "insufficient_data")
        self.assertEqual(adv(disc=100)["status"], "insufficient_data")
        r = adv(upload_in_progress=True)
        self.assertEqual(r["status"], "upload_in_progress")
        self.assertIsNone(r["recommended_price"])

    def test_integers_only(self):
        r = adv(price=1234, disc=33, target=17)
        for key in ("recommended_price", "recommended_final", "current_final", "breakeven_final"):
            self.assertIsInstance(r[key], int)

    def test_advise_all(self):
        out = advise_all([ROW, {**ROW, "nm_id": 2}], {1: (1000, 0)}, 20, {1})
        self.assertEqual([o["status"] for o in out], ["upload_in_progress", "insufficient_data"])


if __name__ == "__main__":
    unittest.main()
