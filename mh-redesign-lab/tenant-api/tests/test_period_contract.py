from datetime import date
from pathlib import Path
import sys


TENANT_APP = Path(__file__).resolve().parents[3] / "tenant-app"
sys.path.insert(0, str(TENANT_APP))

from overview_service import resolve_window


def test_standard_periods_use_the_expected_inclusive_window() -> None:
    today = date(2026, 8, 31)

    assert resolve_window("today", today) == (date(2026, 8, 31), date(2026, 8, 31))
    assert resolve_window("7d", today) == (date(2026, 8, 25), date(2026, 8, 31))
    assert resolve_window("30d", today) == (date(2026, 8, 2), date(2026, 8, 31))


def test_custom_period_preserves_user_dates_and_normalizes_order() -> None:
    today = date(2026, 8, 31)

    assert resolve_window("custom", today, date(2026, 8, 20), date(2026, 8, 5)) == (
        date(2026, 8, 5),
        date(2026, 8, 20),
    )
