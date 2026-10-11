"""Advertising replacement must never expose an empty or partly replaced period."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from contextlib import contextmanager, closing
from unittest import mock
import sqlite3

import sync
import db.core as core
from db.ingest import upsert_ads
from tests.base import DbTestCase


class AdsPeriodAtomicTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.day = datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
        self.outside = (datetime.now(ZoneInfo("Europe/Moscow")).date() - timedelta(days=40)).isoformat()
        upsert_ads([
            dict(campaign_id=1, day=self.day, nm_id=0, spend=10),
            dict(campaign_id=1, day=self.outside, nm_id=0, spend=30),
            dict(campaign_id=2, day=self.day, nm_id=0, spend=20),
        ])

    def rows(self):
        with core.connect() as conn:
            return [tuple(r) for r in conn.execute("SELECT campaign_id,day,nm_id,spend FROM ads_daily ORDER BY campaign_id,day,nm_id")]

    def run_ads(self, stats, campaigns=(1,)):
        api = mock.Mock()
        api.get_campaign_ids.return_value = list(campaigns)
        api.get_ad_stats.return_value = stats
        result = {}
        sync._sync_ads(api, {}, result)
        return result

    def stats(self, spend=15, campaign=1, days=None):
        return [{"advertId": campaign, "sum": spend if days is None else sum(day.get("sum", 0) for day in days),
                 "days": [{"date": self.day, "sum": spend}] if days is None else days}]

    def test_insert_failure_rolls_back_entire_period(self):
        before = self.rows()
        with core.connect() as conn:
            conn.execute("CREATE TRIGGER ads_failure BEFORE INSERT ON ads_daily WHEN NEW.spend=999 BEGIN SELECT RAISE(ABORT,'injected insertion failure'); END")
        previous_day = (datetime.now(ZoneInfo("Europe/Moscow")).date() - timedelta(days=1)).isoformat()
        result = self.run_ads(self.stats(days=[{"date": previous_day, "sum": 15}, {"date": self.day, "sum": 999}]))
        self.assertIn("ads_error", result)
        self.assertEqual(self.rows(), before)

    def test_reader_observes_old_period_until_commit(self):
        before = self.rows()
        observed = []
        original_connect = core.connect

        @contextmanager
        def traced_connect():
            with original_connect() as conn:
                def trace(sql):
                    if sql.lstrip().upper().startswith("INSERT INTO ADS_DAILY"):
                        with closing(sqlite3.connect(self.db_path)) as reader:
                            observed.append(reader.execute("SELECT campaign_id,day,nm_id,spend FROM ads_daily ORDER BY campaign_id,day,nm_id").fetchall())
                conn.set_trace_callback(trace)
                yield conn

        with mock.patch.object(core, "connect", traced_connect):
            result = self.run_ads(self.stats())
        self.assertNotIn("ads_error", result)
        self.assertTrue(observed)
        self.assertTrue(all(rows == before for rows in observed))
        self.assertIn((1, self.day, 0, 15.0), self.rows())

    def test_unrequested_campaign_and_outside_dates_survive(self):
        self.run_ads(self.stats())
        self.assertIn((2, self.day, 0, 20.0), self.rows())
        self.assertIn((1, self.outside, 0, 30.0), self.rows())

    def test_no_current_campaigns_does_not_delete_historical_spend(self):
        before = self.rows()
        result = self.run_ads([], campaigns=())
        self.assertEqual(result["ads"], 0)
        self.assertEqual(self.rows(), before)

    def test_explicit_zero_day_campaign_clears_only_its_period(self):
        result = self.run_ads(self.stats(days=[]))
        self.assertNotIn("ads_skipped", result)
        self.assertEqual(result["ads"], 0)
        self.assertNotIn((1, self.day, 0, 10.0), self.rows())
        self.assertIn((2, self.day, 0, 20.0), self.rows())
        self.assertIn((1, self.outside, 0, 30.0), self.rows())

    def test_missing_campaign_is_partial_and_its_history_is_preserved(self):
        result = self.run_ads(self.stats(), campaigns=(1, 2))
        self.assertIn("ads_skipped", result)
        self.assertIn((2, self.day, 0, 20.0), self.rows())
        self.assertIn((1, self.day, 0, 15.0), self.rows())

    def test_empty_response_with_current_campaigns_preserves_everything(self):
        before = self.rows()
        result = self.run_ads([])
        self.assertIn("ads_skipped", result)
        self.assertEqual(self.rows(), before)

    def test_invalid_responses_preserve_everything(self):
        before = self.rows()
        invalid_stats = [
            {"unexpected": "shape"},
            [{"advertId": 1}],
            [{"advertId": 1, "days": []}],
            [{"advertId": 1, "days": [], "sum": 100}],
            self.stats(campaign=99),
            self.stats(days=[{"date": "garbage", "sum": 20}]),
            self.stats(days=[{"date": self.outside, "sum": 20}]),
            self.stats(days=[{"date": self.day}, {"date": self.day}]),
            self.stats() + self.stats(),
        ]
        for stats in invalid_stats:
            with self.subTest(stats=stats):
                self.assertIn("ads_error", self.run_ads(stats))
                self.assertEqual(self.rows(), before)

    def test_later_fetch_failure_does_not_apply_earlier_window(self):
        before = self.rows()
        api = mock.Mock()
        api.get_campaign_ids.return_value = [1]
        api.get_ad_stats.side_effect = [self.stats(days=[]), RuntimeError("second window unavailable")]
        result = {}
        sync._sync_ads(api, {"advert_history_days": 60}, result)
        self.assertIn("ads_error", result)
        self.assertEqual(api.get_ad_stats.call_count, 2)
        self.assertEqual(self.rows(), before)

    def test_campaign_missing_in_one_window_keeps_entire_campaign_period(self):
        before = self.rows()
        api = mock.Mock()
        api.get_campaign_ids.return_value = [1]
        api.get_ad_stats.side_effect = [[], self.stats()]
        result = {}
        sync._sync_ads(api, {"advert_history_days": 60}, result)
        self.assertIn("ads_skipped", result)
        self.assertEqual(self.rows(), before)

    def test_all_verified_windows_replace_their_combined_period(self):
        api = mock.Mock()
        api.get_campaign_ids.return_value = [1]
        api.get_ad_stats.side_effect = [self.stats(days=[{"date": self.outside, "sum": 40}]), self.stats()]
        result = {}
        sync._sync_ads(api, {"advert_history_days": 60}, result)
        self.assertNotIn("ads_error", result)
        self.assertNotIn("ads_skipped", result)
        self.assertEqual(result["ads"], 2)
        self.assertIn((1, self.outside, 0, 40.0), self.rows())
        self.assertIn((1, self.day, 0, 15.0), self.rows())
        self.assertIn((2, self.day, 0, 20.0), self.rows())

    def test_atomic_replacement_removes_obsolete_product_attribution(self):
        upsert_ads([dict(campaign_id=1, day=self.day, nm_id=777, spend=10)])
        self.run_ads(self.stats())
        self.assertNotIn((1, self.day, 777, 10.0), self.rows())

    def test_database_replacement_rejects_unverified_or_duplicate_rows(self):
        from db.ingest import replace_ads_period
        before = self.rows()
        invalid_rows = [
            [dict(campaign_id=99, day=self.day, nm_id=0, spend=15)],
            [dict(campaign_id=1, day=self.outside, nm_id=0, spend=15)],
            [dict(campaign_id=1, day=self.day, nm_id=-1, spend=15)],
            [dict(campaign_id=1, day=self.day, nm_id=0, spend=15)] * 2,
            [dict(campaign_id=1, day=self.day, nm_id=0)],
            [dict(campaign_id=1, day=self.day, nm_id=0, spend=float("nan"))],
            [dict(campaign_id=1, day=self.day, nm_id=0, spend=-1)],
        ]
        for rows in invalid_rows:
            with self.assertRaises(ValueError):
                replace_ads_period(rows, self.day, self.day, [1])
            self.assertEqual(self.rows(), before)

    def test_real_fetch_adapter_batches_fifty_ids_and_month_windows(self):
        from wb_api import WBAPI
        api = WBAPI("test-only")
        requests = []

        def response(method, url, *, params):
            requests.append(params)
            ids = [int(value) for value in params["ids"].split(",")]
            return [dict(advertId=value, sum=0, days=[]) for value in ids]

        with mock.patch.object(api, "get_campaign_ids", return_value=list(range(1, 52))), \
             mock.patch.object(api, "_request", side_effect=response), \
             mock.patch("wb_api.time.sleep"):
            result = {}
            sync._sync_ads(api, {"advert_history_days": 60}, result)
        self.assertNotIn("ads_error", result)
        self.assertNotIn("ads_skipped", result)
        self.assertEqual(len(requests), 4)
        for params in requests:
            self.assertLessEqual(len(params["ids"].split(",")), 50)
            self.assertLessEqual((date.fromisoformat(params["endDate"]) - date.fromisoformat(params["beginDate"])).days, 30)

    def test_real_fetch_adapter_empty_http_batch_preserves_that_campaign(self):
        from wb_api import WBAPI
        api = WBAPI("test-only")
        before = self.rows()
        with mock.patch.object(api, "get_campaign_ids", return_value=[1]), \
             mock.patch.object(api, "_request", return_value=[]), \
             mock.patch("wb_api.time.sleep"):
            result = {}
            sync._sync_ads(api, {}, result)
        self.assertIn("ads_skipped", result)
        self.assertEqual(self.rows(), before)

    def test_contradictory_empty_response_preserves_saved_spend(self):
        before = self.rows()
        for metric in ("views", "clicks", "orders", "sum_price", "atbs", "shks", "canceled"):
            with self.subTest(metric=metric):
                result = self.run_ads([dict(advertId=1, sum=0, days=[], **{metric: 5})])
                self.assertIn("ads_error", result)
                self.assertEqual(self.rows(), before)

    def test_missing_nonfinite_or_negative_daily_spend_preserves_history(self):
        before = self.rows()
        for spend in (None, float("nan"), float("inf"), -1):
            with self.subTest(spend=spend):
                day = dict(date=self.day, views=100, clicks=5)
                if spend is not None:
                    day["sum"] = spend
                result = self.run_ads([dict(advertId=1, sum=15, days=[day])])
                self.assertIn("ads_error", result)
                self.assertEqual(self.rows(), before)

    def test_campaign_daily_and_product_spend_disagreements_preserve_history(self):
        before = self.rows()
        invalid = [
            dict(advertId=1, sum=15, days=[dict(date=self.day, sum=0)]),
            dict(advertId=1, sum=0, days=[dict(date=self.day, sum=0, apps=[dict(appType=1, sum=15, nms=[dict(nmId=777, sum=15)])])]),
            dict(advertId=1, sum=15, days=[dict(date=self.day, sum=15, apps=[dict(appType=1, nms=[dict(nmId=777, views=100)])])]),
        ]
        for stats in invalid:
            with self.subTest(stats=stats):
                result = self.run_ads([stats])
                self.assertIn("ads_error", result)
                self.assertEqual(self.rows(), before)

    def test_valid_device_breakdown_adds_sku_components_not_parent_totals(self):
        # One SKU appears on both mobile and web. App and SKU values describe
        # the same spend: 6+9=15, not 6+6+9+9=30 or a deduplicated 6/9.
        stats = dict(advertId=1, sum=15, days=[dict(date=self.day, sum=15, apps=[
            dict(appType=1, sum=6, nms=[dict(nmId=777, sum=6)]),
            dict(appType=32, sum=9, nms=[dict(nmId=777, sum=9)]),
        ])])
        result = self.run_ads([stats])
        self.assertNotIn("ads_error", result)
        self.assertIn((1, self.day, 0, 15.0), self.rows())
        self.assertIn((1, self.day, 777, 15.0), self.rows())

    def test_known_zero_day_without_optional_counters_is_valid(self):
        result = self.run_ads([dict(advertId=1, sum=0, days=[dict(date=self.day, sum=0)])])
        self.assertNotIn("ads_error", result)
        self.assertIn((1, self.day, 0, 0.0), self.rows())

    def test_missing_campaign_total_with_known_daily_spend_is_valid(self):
        # Omitted campaign summary does not invent zero; actual day money is
        # still a usable observed value when views/clicks/attribution are absent.
        result = self.run_ads([dict(advertId=1, days=[dict(date=self.day, sum=15)])])
        self.assertNotIn("ads_error", result)
        self.assertIn((1, self.day, 0, 15.0), self.rows())

    def test_partial_attribution_and_kopeck_rounding_are_not_false_errors(self):
        stats = dict(advertId=1, sum=15, days=[dict(date=self.day, sum=15.005,
            apps=[dict(appType=1, sum=15.005, nms=[dict(nmId=777, sum=10)])])])
        result = self.run_ads([stats])
        self.assertNotIn("ads_error", result)
        self.assertIn((1, self.day, 777, 10.0), self.rows())

    def test_invalid_campaign_summary_and_product_money_do_not_destroy_history(self):
        before = self.rows()
        invalid = [
            dict(advertId=1, sum=float("nan"), days=[dict(date=self.day, sum=15)]),
            dict(advertId=1, sum=None, days=[dict(date=self.day, sum=15)]),
            dict(advertId=1, sum=15, days=[dict(date=self.day, sum=15,
                apps=[dict(appType=1, nms=[dict(nmId=777, sum=float("inf"))])])]),
            dict(advertId=1, sum=15, days=[dict(date=self.day, sum=15,
                apps=[dict(appType=1, sum=16, nms=[dict(nmId=777, sum=15)])])]),
        ]
        for stats in invalid:
            with self.subTest(stats=stats):
                self.assertIn("ads_error", self.run_ads([stats]))
                self.assertEqual(self.rows(), before)

    def test_real_fetch_adapter_contradictory_money_preserves_history(self):
        from wb_api import WBAPI
        api = WBAPI("test-only")
        before = self.rows()
        with mock.patch.object(api, "get_campaign_ids", return_value=[1]), \
             mock.patch.object(api, "_request", return_value=[dict(advertId=1, sum=15,
                days=[dict(date=self.day, views=100)])]), \
             mock.patch("wb_api.time.sleep"):
            result = {}
            sync._sync_ads(api, {}, result)
        self.assertIn("ads_error", result)
        self.assertEqual(self.rows(), before)
