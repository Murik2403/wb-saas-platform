import pytest

from db.ingest import upsert_ads, upsert_financial
from monthly_pnl import monthly_pnl
from tax_estimate import add_tax_block, estimate_month, read_tax_settings, save_tax_settings
from tests.base import DbTestCase
from datetime import date


def est(regime, income, usn, vat, expenses=0.0, cost=0.0, cost_official=False, result=0.0):
    return estimate_month(regime, cost_official, income, expenses, cost, usn, vat, result)


@pytest.mark.parametrize("income,vat,vat_out", [(1000, 0, 0), (1050, 5, 50), (1070, 7, 70), (1200, 20, 200), (1220, 22, 220)])
def test_usn_income_vat_variants(income, vat, vat_out):
    r = est("usn_income", income, 6, vat, result=500)
    assert r["vat_out"] == vat_out
    assert r["usn_base"] == 1000
    assert r["usn"] == 60  # 6% of 1000
    assert r["total"] == vat_out + 60
    assert r["profit_after_tax_estimate"] == 500 - vat_out - 60
    assert r["status"] == "ok" and r["loss"] is False


def test_usn_income_expense_with_and_without_official_cost():
    base = dict(income=1200, usn=15, vat=20, expenses=400, cost=300)
    r = est("usn_income_expense", **base)  # 1200-200-400 = 600; 15% = 90
    assert (r["usn_base"], r["usn"], r["total"]) == (600, 90, 290)
    r = est("usn_income_expense", cost_official=True, **base)  # 600-300 = 300; 15% = 45
    assert (r["usn_base"], r["usn"], r["total"]) == (300, 45, 245)


def test_usn_income_ignores_expenses_and_cost():
    assert est("usn_income", 1200, 6, 20, expenses=999, cost=999, cost_official=True)["usn_base"] == 1000


def test_loss_month_has_zero_usn_and_flag_but_vat_stays():
    r = est("usn_income_expense", 1200, 15, 20, expenses=1500)  # base 1000-1500 = -500
    assert (r["usn_base"], r["usn"], r["vat_out"], r["total"], r["loss"]) == (-500, 0, 200, 200, True)


def test_rounding_only_at_the_end():
    r = est("usn_income", 100, 6, 22)  # vat_out 18.032787, base 81.967213, usn 4.918033
    assert (r["vat_out"], r["usn_base"], r["usn"], r["total"]) == (18.03, 81.97, 4.92, 22.95)


def test_missing_rate_is_not_zero_and_unknown_regime_rejected():
    r = est("usn_income", 1000, None, 20)
    assert r["status"] == "rates_missing" and r["total"] is None
    assert est("usn_income", 1000, 6, None)["status"] == "rates_missing"
    with pytest.raises(ValueError):
        est("osno", 1000, 6, 20)


class TaxBlockTests(DbTestCase):
    def pnl(self):
        upsert_financial([
            dict(rrd_id=1, rr_dt="2026-08-10", nm_id=100, doc_type_name="Продажа", quantity=2,
                 retail_price_withdisc_rub=1000, retail_amount=1200, ppvz_for_pay=900, ppvz_sales_commission=200,
                 acquiring_fee=40, delivery_rub=60),
        ])
        upsert_ads([dict(campaign_id=1, day="2026-08-09", nm_id=0, spend=50)])
        return monthly_pnl(date(2026, 8, 1), date(2026, 9, 30))

    def test_no_regime_gives_null_tax(self):
        p = self.pnl()
        add_tax_block(p)
        assert p["months"][0]["tax"] is None and p["total"]["tax"] is None

    def test_regime_with_rates_and_missing_month(self):
        save_tax_settings(2026, "usn_income_expense", False, [{"month": 8, "usn_pct": 15, "vat_pct": 20}])
        self.assertEqual(read_tax_settings(2026)["rates"], [{"month": 8, "usn_pct": 15.0, "vat_pct": 20.0}])
        p = self.pnl()
        add_tax_block(p)
        aug, sep = p["months"]
        # income 1200, vat_out 200; expenses: commission 200 + acquiring 40 + logistics 60 + ad 50 + other (1200-900-200-40=60) = 410
        self.assertEqual(aug["tax"]["vat_out"], 200)
        self.assertEqual(aug["tax"]["usn_base"], 590)  # 1200-200-410
        self.assertEqual(aug["tax"]["usn"], 88.5)
        self.assertEqual(aug["tax"]["total"], 288.5)
        self.assertEqual(aug["tax"]["profit_after_tax_estimate"], round(aug["result"] - 288.5, 2))
        self.assertEqual(sep["tax"]["status"], "rates_missing")
        self.assertEqual(p["total"]["tax"]["status"], "partial")
        self.assertEqual(p["total"]["tax"]["total"], 288.5)
        # saving again replaces rates
        save_tax_settings(2026, "usn_income", False, [])
        self.assertEqual(read_tax_settings(2026)["rates"], [])
