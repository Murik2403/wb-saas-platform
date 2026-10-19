from datetime import date, timedelta
import weakref
from unittest.mock import patch

import calculations
import pandas as pd
from tests.base import DbTestCase
from tests.test_dashboard_projection import DashboardProjectionTests


class DashboardReadLifetimeTests(DbTestCase):
    seed = DashboardProjectionTests.seed

    def test_financial_read_starts_after_operational_frames_are_released(self):
        self.seed()
        refs = []
        original_prepare = calculations._prepare_dates
        original_financial = calculations.read_financial

        def prepare(frame, column):
            result = original_prepare(frame, column)
            if column in ("order_date", "sale_date"):
                refs.append(weakref.ref(result))
            return result

        def financial(**kwargs):
            self.assertEqual(len(refs), 2)
            self.assertTrue(all(ref() is None for ref in refs), "financial SQL read overlaps live operational frames")
            return original_financial(**kwargs)

        with patch.object(calculations, "_prepare_dates", side_effect=prepare), patch.object(calculations, "read_financial", side_effect=financial):
            calculations.build_dashboard(date(2026, 9, 1), date(2026, 9, 30))

    def test_delayed_read_matches_prefetched_financial_frame_on_all_periods(self):
        self.seed()
        # Mirror the old eager lifetime without changing the source rows,
        # parser, formulas or FIFO reader. Include a one-day hourly report.
        end = date(2026, 9, 1)
        for days in (1, 7, 30, 90):
            with self.subTest(days=days):
                start = end - timedelta(days=days - 1)
                prefetched = calculations.read_financial(omit_raw=True)
                with patch.object(calculations, "read_financial", return_value=prefetched):
                    reference = calculations.build_dashboard(start, end)
                actual = calculations.build_dashboard(start, end)
                for field in reference.__dataclass_fields__:
                    expected, got = getattr(reference, field), getattr(actual, field)
                    if isinstance(expected, pd.DataFrame):
                        pd.testing.assert_frame_equal(expected, got, check_exact=True)
                    else:
                        self.assertEqual(expected, got, field)
