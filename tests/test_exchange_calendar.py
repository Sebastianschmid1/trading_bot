"""
Tests für den Exchange-Kalender (DATA-001, stockbot/core/exchange_calendar.py).

Nutzt feste, bekannte NYSE-Kalendertage statt "heute", damit die Tests unabhängig vom
tatsächlichen Ausführungsdatum deterministisch bleiben.
"""

from datetime import datetime, timedelta, timezone

from stockbot.core import exchange_calendar as cal

FRIDAY = datetime(2026, 7, 10, tzinfo=timezone.utc)          # regulärer Handelstag
SATURDAY = datetime(2026, 7, 11, tzinfo=timezone.utc)        # Wochenende
SUNDAY = datetime(2026, 7, 12, tzinfo=timezone.utc)          # Wochenende
MONDAY = datetime(2026, 7, 13, tzinfo=timezone.utc)          # regulärer Handelstag
NEW_YEARS_DAY = datetime(2026, 1, 1, tzinfo=timezone.utc)    # Feiertag (Donnerstag)
DAY_AFTER_THANKSGIVING = datetime(2026, 11, 27, tzinfo=timezone.utc)   # Frühschluss


def test_is_trading_day_true_on_weekday():
    assert cal.is_trading_day(FRIDAY) is True


def test_is_trading_day_false_on_weekend():
    assert cal.is_trading_day(SATURDAY) is False
    assert cal.is_trading_day(SUNDAY) is False


def test_is_trading_day_false_on_holiday():
    assert cal.is_trading_day(NEW_YEARS_DAY) is False


def test_market_open_close_regular_session_is_930_to_1600_et():
    open_ts = cal.market_open(FRIDAY)
    close_ts = cal.market_close(FRIDAY)
    assert open_ts is not None and close_ts is not None
    assert close_ts - open_ts == timedelta(hours=6, minutes=30)


def test_market_open_close_none_on_non_trading_day():
    assert cal.market_open(SATURDAY) is None
    assert cal.market_close(SATURDAY) is None


def test_is_market_open_true_during_session():
    mid_session = cal.market_open(FRIDAY) + timedelta(hours=1)
    assert cal.is_market_open(mid_session) is True


def test_is_market_open_false_before_open_and_after_close():
    before = cal.market_open(FRIDAY) - timedelta(minutes=1)
    after = cal.market_close(FRIDAY) + timedelta(minutes=1)
    assert cal.is_market_open(before) is False
    assert cal.is_market_open(after) is False


def test_is_market_open_false_on_weekend():
    assert cal.is_market_open(SATURDAY.replace(hour=15)) is False


def test_next_market_open_from_before_fridays_open_is_friday():
    before = cal.market_open(FRIDAY) - timedelta(hours=1)
    assert cal.next_market_open(before) == cal.market_open(FRIDAY)


def test_next_market_open_from_within_friday_session_skips_to_monday():
    mid_friday = cal.market_open(FRIDAY) + timedelta(hours=1)
    assert cal.next_market_open(mid_friday) == cal.market_open(MONDAY)


def test_next_market_open_skips_weekend():
    after_friday_close = cal.market_close(FRIDAY) + timedelta(minutes=1)
    assert cal.next_market_open(after_friday_close) == cal.market_open(MONDAY)


def test_minutes_to_close_during_session():
    close_ts = cal.market_close(FRIDAY)
    ten_min_before_close = close_ts - timedelta(minutes=10)
    minutes = cal.minutes_to_close(ten_min_before_close)
    assert minutes is not None
    assert abs(minutes - 10.0) < 1e-6


def test_minutes_to_close_none_when_market_closed():
    assert cal.minutes_to_close(SATURDAY.replace(hour=15)) is None


def test_is_early_close_true_day_after_thanksgiving():
    assert cal.is_early_close(DAY_AFTER_THANKSGIVING) is True


def test_is_early_close_false_on_regular_session():
    assert cal.is_early_close(FRIDAY) is False


def test_is_early_close_false_on_non_trading_day():
    assert cal.is_early_close(SATURDAY) is False
