from datetime import date

from self_buyouts import bucket_start, delta, group_rows, totals, with_deltas


def test_bucket_boundaries():
    # 2026-09-07 is a Monday; 2026-09-06 Sunday belongs to the previous ISO week
    assert bucket_start(date(2026, 9, 7), "week") == date(2026, 9, 7)
    assert bucket_start(date(2026, 9, 13), "week") == date(2026, 9, 7)
    assert bucket_start(date(2026, 9, 6), "week") == date(2026, 8, 31)
    assert bucket_start(date(2026, 1, 1), "week") == date(2025, 12, 29)  # across the year
    assert bucket_start(date(2026, 2, 28), "month") == date(2026, 2, 1)
    assert bucket_start(date(2026, 9, 15), "day") == date(2026, 9, 15)


def test_totals_and_net():
    assert totals([(2, 150000, 30000), (1, 50000, 0)]) == {
        "records": 2, "units": 3, "paid_kop": 200000, "cashback_kop": 30000, "net_cost_kop": 170000}
    assert totals([])["net_cost_kop"] == 0


def test_group_rows_week_and_month():
    rows = [(date(2026, 8, 31), 1, 100, 10), (date(2026, 9, 6), 2, 200, 20), (date(2026, 9, 7), 3, 300, 30)]
    weeks = group_rows(rows, "week")
    assert [w["start"] for w in weeks] == ["2026-09-07", "2026-08-31"]
    assert weeks[1]["units"] == 3 and weeks[1]["paid_kop"] == 300 and weeks[1]["net_cost_kop"] == 270
    months = group_rows(rows, "month")
    assert [m["start"] for m in months] == ["2026-09-01", "2026-08-01"]
    assert months[0]["units"] == 5 and months[0]["net_cost_kop"] == 450


def test_delta_zero_previous_and_negative():
    assert delta(500, 0) == {"value": 500, "previous": 0, "delta": 500, "delta_pct": None}
    assert delta(150, 200) == {"value": 150, "previous": 200, "delta": -50, "delta_pct": -25.0}
    assert delta(0, 0)["delta_pct"] is None
    cur = totals([(1, 100, 0)])
    assert with_deltas(cur, totals([]))["units"]["delta"] == 1
