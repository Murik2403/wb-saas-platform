from datetime import datetime

from report_scheduler import _is_due


def base_definition(**overrides) -> dict:
    definition = {
        "schedule_type": "daily", "schedule_time": "09:00",
        "schedule_weekday": None, "schedule_day": None, "last_run_at": None,
    }
    definition.update(overrides)
    return definition


def test_daily_fires_at_exact_minute():
    now = datetime(2026, 8, 23, 9, 0)
    assert _is_due(base_definition(), now) is True


def test_daily_does_not_fire_other_minute():
    now = datetime(2026, 8, 23, 9, 1)
    assert _is_due(base_definition(), now) is False


def test_daily_does_not_fire_twice_same_day():
    now = datetime(2026, 8, 23, 9, 0)
    definition = base_definition(last_run_at="2026-08-23T09:00:00")
    assert _is_due(definition, now) is False


def test_daily_fires_again_next_day():
    now = datetime(2026, 8, 24, 9, 0)
    definition = base_definition(last_run_at="2026-08-23T09:00:00")
    assert _is_due(definition, now) is True


def test_weekly_only_fires_on_matching_weekday():
    now = datetime(2026, 8, 23, 10, 0)  # a Sunday (weekday() == 6)
    matching = base_definition(schedule_type="weekly", schedule_time="10:00", schedule_weekday=6)
    other_day = base_definition(schedule_type="weekly", schedule_time="10:00", schedule_weekday=0)
    assert _is_due(matching, now) is True
    assert _is_due(other_day, now) is False


def test_monthly_only_fires_on_matching_day():
    now = datetime(2026, 8, 1, 8, 0)
    matching = base_definition(schedule_type="monthly", schedule_time="08:00", schedule_day=1)
    other_day = base_definition(schedule_type="monthly", schedule_time="08:00", schedule_day=15)
    assert _is_due(matching, now) is True
    assert _is_due(other_day, now) is False
