"""Exchange-Kalender NYSE/Nasdaq (DATA-001, siehe docs/Plan.md, docs/PLAN_CHECKLIST.md Phase 2).

Kapselt Handelstage/-zeiten (inkl. Wochenenden, Feiertagen, Frühschluss-Tagen) über
`pandas_market_calendars`, damit Handelszeit-Prüfungen nicht mehr auf ein festes,
session-blindes Zeitfenster angewiesen sind wie `stockbot/tgbot/bot.py::_us_market_open`
(siehe TSAFE-007-Notiz in docs/PLAN_CHECKLIST.md: "feste Berlin-Zeit war session-blind").

Noch von KEINEM Live-Codepfad genutzt — die Umstellung des Schedulers/`_us_market_open`
auf diese Funktionen ist der nächste, eigene Checklisten-Punkt (DATA-002). NYSE und Nasdaq
teilen sich denselben US-Feiertagskalender; `DEFAULT_EXCHANGE` deckt daher beide ab.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache

import pandas as pd
import pandas_market_calendars as mcal

DEFAULT_EXCHANGE = "NYSE"
_FULL_SESSION = timedelta(hours=6, minutes=30)   # regulär 9:30–16:00 ET, DST-unabhängig


@lru_cache(maxsize=4)
def _calendar(exchange: str = DEFAULT_EXCHANGE):
    return mcal.get_calendar(exchange)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _schedule(date, exchange: str = DEFAULT_EXCHANGE) -> pd.DataFrame:
    """Handelskalender-Zeile für den Tag von `date` (leer, wenn kein Handelstag)."""
    day = pd.Timestamp(date).normalize()
    return _calendar(exchange).schedule(start_date=day, end_date=day)


def is_trading_day(date, exchange: str = DEFAULT_EXCHANGE) -> bool:
    """True, wenn `date` ein regulärer Handelstag ist (kein Wochenende/Feiertag)."""
    return not _schedule(date, exchange).empty


def market_open(date, exchange: str = DEFAULT_EXCHANGE) -> datetime | None:
    """Öffnungszeit (UTC, tz-aware) des Handelstags von `date`, `None` wenn kein Handelstag."""
    sched = _schedule(date, exchange)
    if sched.empty:
        return None
    return sched.iloc[0]["market_open"].to_pydatetime()


def market_close(date, exchange: str = DEFAULT_EXCHANGE) -> datetime | None:
    """Schlusszeit (UTC, tz-aware) des Handelstags von `date`, `None` wenn kein Handelstag."""
    sched = _schedule(date, exchange)
    if sched.empty:
        return None
    return sched.iloc[0]["market_close"].to_pydatetime()


def is_market_open(at: datetime | None = None, exchange: str = DEFAULT_EXCHANGE) -> bool:
    """True, wenn `at` (Default: jetzt) innerhalb der regulären Handelszeit liegt."""
    at = _as_utc(at or datetime.now(timezone.utc))
    open_ts, close_ts = market_open(at, exchange), market_close(at, exchange)
    if open_ts is None or close_ts is None:
        return False
    return open_ts <= at <= close_ts


def next_market_open(
    after: datetime | None = None, exchange: str = DEFAULT_EXCHANGE
) -> datetime:
    """Nächste Öffnungszeit STRIKT nach `after` (Default: jetzt) — liegt `after` bereits
    innerhalb eines laufenden Handelstags, wird die Öffnung der NÄCHSTEN Sitzung geliefert."""
    after = _as_utc(after or datetime.now(timezone.utc))
    window_end = pd.Timestamp(after).normalize() + pd.Timedelta(days=14)
    sched = _calendar(exchange).schedule(
        start_date=pd.Timestamp(after).normalize(), end_date=window_end
    )
    future_opens = sched["market_open"][sched["market_open"] > pd.Timestamp(after)]
    if future_opens.empty:
        raise ValueError(f"Kein Handelstag innerhalb von 14 Tagen nach {after} gefunden")
    return future_opens.iloc[0].to_pydatetime()


def minutes_to_close(
    at: datetime | None = None, exchange: str = DEFAULT_EXCHANGE
) -> float | None:
    """Minuten bis Handelsschluss, `None` wenn der Markt bei `at` nicht offen ist."""
    at = _as_utc(at or datetime.now(timezone.utc))
    if not is_market_open(at, exchange):
        return None
    close_ts = market_close(at, exchange)
    return (close_ts - at).total_seconds() / 60.0


def is_past_entry_cutoff(
    cutoff_minutes: float, at: datetime | None = None, exchange: str = DEFAULT_EXCHANGE
) -> bool:
    """True, wenn der Markt offen ist, aber weniger als `cutoff_minutes` bis Handelsschluss
    verbleiben — dann sollten keine NEUEN Positionen mehr eröffnet werden (Plan.md §10.1
    „Entry-Sperre relativ zum Close", DATA-002). False, wenn der Markt gerade geschlossen ist
    (dafür gilt die separate `is_market_open`-Prüfung) oder noch genug Zeit bis Handelsschluss
    verbleibt."""
    remaining = minutes_to_close(at, exchange)
    if remaining is None:
        return False
    return remaining < cutoff_minutes


def is_early_close(date, exchange: str = DEFAULT_EXCHANGE) -> bool:
    """True, wenn der Handelstag von `date` ein Frühschluss ist (z. B. Tag nach Thanksgiving).

    Vergleicht die Sitzungsdauer (Close − Open) mit der regulären 6h30-Sitzung statt einer
    festen Uhrzeit — robust gegenüber der US-Zeitumstellung (EST/EDT).
    """
    open_ts, close_ts = market_open(date, exchange), market_close(date, exchange)
    if open_ts is None or close_ts is None:
        return False
    return (close_ts - open_ts) < _FULL_SESSION
