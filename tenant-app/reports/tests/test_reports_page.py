"""Tests for pages/reports_page.py's pure schedule-label helper.

Regression coverage for a bug caught live in production: the previous
implementation built schedule_label with a dict literal indexed by
schedule_type -- Python evaluates every value in a dict literal eagerly,
so the "weekly" branch's f-string (referencing schedule_weekday) ran even
for a "daily" definition, where schedule_weekday is NULL/None, crashing
with `TypeError: list indices must be integers or slices, not NoneType`
the moment the "Отчёты" page was opened with any daily/monthly report.
"""
from pages.reports_page import _schedule_label


def test_daily_schedule_label_does_not_touch_schedule_weekday():
    definition = {
        "schedule_type": "daily", "schedule_time": "09:00",
        "schedule_weekday": None, "schedule_day": None,
    }
    assert _schedule_label(definition) == "ежедневно в 09:00"


def test_monthly_schedule_label_does_not_touch_schedule_weekday():
    definition = {
        "schedule_type": "monthly", "schedule_time": "08:00",
        "schedule_weekday": None, "schedule_day": 15,
    }
    assert _schedule_label(definition) == "ежемесячно 15-го числа в 08:00"


def test_weekly_schedule_label_uses_weekday_name():
    definition = {
        "schedule_type": "weekly", "schedule_time": "10:00",
        "schedule_weekday": 0, "schedule_day": None,
    }
    assert _schedule_label(definition) == "еженедельно по понедельникам в 10:00"
