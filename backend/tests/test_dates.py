"""Date normalization tests: relative expressions, passthrough, unknown handling."""

from datetime import date, datetime

from backend.utils.dates import dates_differ, parse_daypart, resolve_relative_date


def test_resolve_today_yesterday():
    now = date(2026, 8, 15)
    assert resolve_relative_date("today", now) == date(2026, 8, 15)
    assert resolve_relative_date("yesterday", now) == date(2026, 8, 14)


def test_resolve_this_morning_keeps_date_and_daypart():
    now = datetime(2026, 8, 15, 9, 30)
    assert resolve_relative_date("this morning", now) == date(2026, 8, 15)
    assert parse_daypart("this morning") == "morning"


def test_resolve_accepts_datetime_and_case_whitespace():
    now = datetime(2026, 8, 15, 23, 59)
    assert resolve_relative_date("  TODAY ", now) == date(2026, 8, 15)


def test_resolve_passes_explicit_iso_date_through():
    now = date(2026, 8, 15)
    assert resolve_relative_date("2026-08-10", now) == date(2026, 8, 10)


def test_resolve_unknown_and_none_return_none():
    now = date(2026, 8, 15)
    assert resolve_relative_date(None, now) is None
    assert resolve_relative_date("tomorrow", now) is None
    assert resolve_relative_date("not a date", now) is None
    assert parse_daypart("this morning") is not None
    assert parse_daypart("yesterday") is None


def test_dates_differ():
    assert dates_differ(date(2026, 8, 15), date(2026, 8, 15)) is False
    assert dates_differ(date(2026, 8, 15), date(2026, 8, 14)) is True
    assert dates_differ(None, date(2026, 8, 15)) is None
    assert dates_differ(date(2026, 8, 15), None) is None
    assert dates_differ(None, None) is None
