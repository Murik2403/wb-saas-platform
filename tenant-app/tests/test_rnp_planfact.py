from datetime import date

import pytest

import rnp_planfact as r

TODAY = date(2026, 9, 10)
D = lambda n: date(2026, 9, n)
SALES = {D(3): (7, 700.0), D(4): (14, 1400.0), D(7): (7, 700.0), D(9): (14, 1400.0)}


def article(**kw):
    base = dict(plan={D(8): 10, D(9): 8, D(10): 5, D(11): 5}, orders={D(8): (12, 1200.0), D(9): (4, 400.0), D(10): (3, 300.0)},
                sales=SALES, first_sale=D(3), history_start=D(1), price_by_day={D(9): (1250.0, 1000.0)}, ads={D(9): 100.0},
                stock_by_day={D(9): 40}, plan_margin_pct=25.0, margin_pct=22.5)
    base.update(kw)
    return r.build_article([D(8), D(9), D(10), D(11), D(12)], TODAY, **base)


def test_forecast_partial_first_week_today_and_future():
    rows = {x["day"]: x for x in article()["days"]}
    # first sale 09-03: 09-08 averages 09-03..09-07 = (7+14+0+0+7)/5
    assert rows["2026-09-08"]["forecast_sales"] == 5.6
    assert rows["2026-09-09"]["forecast_sales"] == 4.67          # 28/6
    assert rows["2026-09-10"]["forecast_sales"] == 6.0           # 42/7, today excluded from its own history
    assert rows["2026-09-11"]["forecast_sales"] == rows["2026-09-12"]["forecast_sales"] == 6.0
    assert rows["2026-09-12"]["is_future"] and rows["2026-09-12"]["fact_orders"] is None and rows["2026-09-12"]["fact_sales"] is None


def test_deviation_and_totals():
    a = article()
    d = {x["day"]: x for x in a["days"]}
    assert (d["2026-09-08"]["deviation_abs"], d["2026-09-08"]["deviation_pct"]) == (2, 20.0)
    assert (d["2026-09-09"]["deviation_abs"], d["2026-09-09"]["deviation_pct"]) == (-4, -50.0)
    assert d["2026-09-10"]["deviation_abs"] is None and d["2026-09-11"]["deviation_pct"] is None   # day not complete
    t = a["totals"]
    assert (t["plan_orders"], t["fact_orders"], t["fact_orders_sum"]) == (28, 19, 1900.0)
    assert (t["deviation_abs"], t["deviation_pct"]) == (-2, -11.1)    # (12+4) - (10+8), over completed days only
    assert t["forecast_sales"] == 28.27                                 # 5.6+4.6667+6+6+6
    assert (t["fact_sales"], t["fact_sales_sum"]) == (14 + 0 + 0, 1400.0)


def test_plan_missing_zero_and_no_orders_division():
    a = article(plan={D(8): 0})
    d = a["days"][0]
    assert (d["deviation_abs"], d["deviation_pct"]) == (12, None)     # plan 0: percent undefined
    assert a["days"][1]["deviation_abs"] is None                       # no plan: no deviation
    none = article(plan={})
    assert none["totals"]["plan_orders"] is None and none["totals"]["deviation_abs"] is None


def test_no_sales_forecast_is_zero_and_no_history_none():
    a = article(sales={}, first_sale=None)
    assert all(x["forecast_sales"] == 0.0 for x in a["days"])
    b = article(sales={D(9): (5, 500.0)}, first_sale=D(9))              # first sale yesterday: nothing before 09-09
    assert b["days"][0]["forecast_sales"] is None and b["days"][1]["forecast_sales"] is None
    assert b["days"][2]["forecast_sales"] == 5.0                        # today: only 09-09 exists


def test_window_shorter_than_seven_days():
    a = r.build_article([D(9)], TODAY, plan={}, orders={}, sales={D(8): (7, 1.0)}, first_sale=D(6), history_start=D(2),
                        price_by_day={}, ads=None, stock_by_day={}, plan_margin_pct=None, margin_pct=None)
    assert a["days"][0]["forecast_sales"] == 2.33                       # 1-day window; days 06,07,08 exist: 7/3


def test_ads_price_stock_and_margin_gap():
    a = article()
    d = {x["day"]: x for x in a["days"]}
    assert d["2026-09-09"]["ad_spend"] == 100.0 and d["2026-09-09"]["drr_pct"] == 7.1   # 100 / 1400
    assert d["2026-09-08"]["drr_pct"] is None and d["2026-09-08"]["ad_spend"] == 0.0     # no revenue
    assert (d["2026-09-09"]["price_before"], d["2026-09-09"]["price_after"]) == (1250.0, 1000.0)
    assert d["2026-09-08"]["price_before"] is None and d["2026-09-09"]["stock"] == 40 and d["2026-09-08"]["stock"] is None
    assert a["margin_gap_pp"] == -2.5
    noads = article(ads=None)
    assert noads["days"][1]["ad_spend"] is None and noads["totals"]["ad_spend"] is None and noads["totals"]["drr_pct"] is None
    assert article(margin_pct=None)["margin_gap_pp"] is None


ROW = {"nm_id": 1, "vendor_code": "A", "sale_units": 20, "units": 20, "revenue": 20000.0, "to_pay": 16000.0,
       "net_profit": 2000.0, "cost": 8000.0, "unsold_cost_units": 0, "ad_spend": 1000.0, "wb_expenses": 2000.0}


def test_forecast_margin_reuses_price_advice_model():
    # k = 1-16000/20000 = 0.2; p = 1000; F = 1000*0.8 - 2000/20 = 700; margin at 1000 = (800-700)/1000 = 10 %
    m, info = r.forecast_margin(ROW, 1250, 20)
    assert m == 10.0 and info["breakeven_final"] == 875 and info["status"] == "ok"
    m, info = r.forecast_margin({**ROW, "sale_units": 3}, 1250, 20)
    assert m is None and "Мало продаж" in info["note"]
    assert r.forecast_margin(ROW, None, None)[0] is None and r.forecast_margin(None, 1000, 0)[0] is None


def test_window_days_clamped():
    assert len(r.window_days(D(1), D(10))) == 10
    days = r.window_days(date(2026, 1, 1), D(10))
    assert len(days) == 31 and days[-1] == D(10)
    assert r.window_days(D(10), D(8)) == [D(8), D(9), D(10)]
