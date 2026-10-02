from tests.base import DbTestCase
from tax_reconciliation import BASE_FIELDS, calculate, read_month, save_month, validate


def values(**changes):
    return dict(month="2026-09", regime="ausn_income", source="Bank and offset statement", confirmed=True,
                received=10000, offset=1000, compensation=200, other_income=0, refunds=500,
                profit_before_tax=4000, fns_assessed=860, paid=800, **changes)


class TaxReconciliationTests(DbTestCase):
    def test_decimal_and_distinct_actuals(self):
        result = calculate(values())
        self.assertEqual(result["base"], 10700)
        self.assertEqual(result["estimate"], 856)
        self.assertEqual(result["after_estimate"], 3144)
        self.assertEqual(result["after_fns"], 3140)
        self.assertEqual(result["fns_difference"], 4)
        self.assertEqual(result["assessed_minus_paid"], 60)

    def test_missing_is_not_zero_and_actuals_still_work(self):
        data = values(); data["offset"] = None
        result = calculate(data)
        self.assertEqual(result["missing"], ["offset"])
        self.assertIsNone(result["estimate"])
        self.assertEqual(result["after_fns"], 3140)
        self.assertIsNone(read_month("2026-09")["calculation"]["estimate"])
        for key in BASE_FIELDS: data[key] = 0
        self.assertEqual(calculate(data)["estimate"], 0)

    def test_round_half_up_negative_base(self):
        data = values(); data.update(received=.25, offset=0, compensation=0, refunds=0)
        self.assertEqual(calculate(data)["estimate"], .02)
        data["refunds"] = 1
        self.assertTrue(calculate(data)["negative_base"])
        self.assertEqual(calculate(data)["estimate"], 0)

    def test_history_conflict_retry_and_month_isolation(self):
        first = save_month(values(), 0, "owner")
        self.assertEqual(first["revision"], 1)
        self.assertEqual(save_month(values(), 0, "owner")["revision"], 1)
        data = values(); data["paid"] = 850
        with self.assertRaisesRegex(ValueError, "revision_conflict"): save_month(data, 0, "other")
        second = save_month(data, 1, "owner")
        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["history"][1]["values"]["paid"], 800)
        self.assertEqual(read_month("2026-10")["history"], [])
        self.assertEqual(first["period"]["end"], "2026-09-30")
        self.assertEqual(read_month("2024-02")["period"]["end"], "2024-02-29")

    def test_validation(self):
        for key, value in [("received", -1), ("received", float("inf")), ("received", .001), ("regime", "usn_income"), ("confirmed", False), ("source", " "), ("month", "2026-13")]:
            data = values(); data[key] = value
            with self.assertRaises(ValueError): validate(data)
        data = values(); data["profit_before_tax"] = -100
        self.assertEqual(validate(data)["profit_before_tax"], -100)
