"""Current stocks use the Moscow observation time, not SKU lastChangeDate."""
from datetime import datetime, timezone
from unittest import mock
from zoneinfo import ZoneInfo

import db.ingest as ingest
from db.core import connect
from tests.base import DbTestCase


class StockObservationTimeTests(DbTestCase):
    def test_default_uses_explicit_moscow_time_on_a_utc_host(self):
        utc_now = datetime(2026, 10, 11, 21, 15, 42, tzinfo=timezone.utc)

        def host_clock(tz=None):
            return utc_now.astimezone(tz) if tz else utc_now.replace(tzinfo=None)

        with mock.patch.object(ingest, "datetime") as clock:
            clock.now.side_effect = host_clock
            ingest.insert_stocks([dict(nmId=1, quantity=3, lastChangeDate="2024-01-01T00:00:00")])
            clock.now.assert_called_once_with(ZoneInfo("Europe/Moscow"))
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT snapshot_at FROM stocks").fetchone()[0], "2026-10-12T00:15:00")

    def test_explicit_historical_observation_is_preserved(self):
        at = "2026-09-01T10:30:00"
        with mock.patch.object(ingest, "datetime") as clock:
            ingest.insert_stocks([dict(nmId=1, quantity=3)], snapshot_at=at)
            clock.now.assert_not_called()
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT snapshot_at FROM stocks").fetchone()[0], at)
