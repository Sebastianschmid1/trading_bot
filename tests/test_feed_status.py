"""Feed-Status (Stylekonzept §32.3): drei Zustaende an denselben Schwellen wie das
Backend-Gate `core/data_quality.check_quote_age`."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from stockbot.core.data_quality import check_quote_age
from stockbot.core.domain import RiskProfile
from stockbot.core.market_data import Quote
from stockbot.web import feed_status

MAX_AGE = 60.0


def _status(age):
    return feed_status.evaluate(age, max_quote_age_seconds=MAX_AGE)


# ── Die drei Zustaende an ihren Schwellen ────────────────────────────────────

def test_fresh_below_the_delayed_threshold():
    s = _status(0.0)
    assert s.state == "fresh"
    assert s.label == "aktuell"
    assert s.chip_class == "chip--go"
    assert _status(29.9).state == "fresh"


def test_delayed_starts_exactly_at_the_named_fraction():
    assert feed_status.DELAYED_FRACTION == 0.5
    boundary = feed_status.DELAYED_FRACTION * MAX_AGE
    assert _status(boundary - 0.1).state == "fresh"
    s = _status(boundary)
    assert s.state == "delayed"
    assert s.chip_class == "chip--caution"
    assert s.label == "verzögert · 30 s"


def test_stale_starts_just_above_the_gate_limit():
    assert _status(MAX_AGE).state == "delayed"          # Grenzwert selbst ist NICHT stale
    s = _status(MAX_AGE + 0.1)
    assert s.state == "stale"
    assert s.label == "veraltet – Orders blockiert"
    assert s.chip_class == "chip--warn"


# ── blocks_orders nur bei stale ──────────────────────────────────────────────

@pytest.mark.parametrize("age,expected", [(0.0, False), (30.0, False), (60.0, False),
                                          (60.5, True), (600.0, True), (None, False)])
def test_blocks_orders_only_when_stale(age, expected):
    s = feed_status.evaluate(age, max_quote_age_seconds=MAX_AGE)
    assert s.blocks_orders is expected
    assert (s.state == "stale") is expected


def test_stale_reason_names_age_and_limit():
    s = _status(125.0)
    assert "125" in s.reason and "60" in s.reason
    assert "blockiert" in s.reason
    assert _status(5.0).reason == ""


# ── „unbekannt": wie verzoegert dargestellt, blockiert aber nie ──────────────

def test_unknown_age_is_explicit_and_never_blocks():
    s = feed_status.evaluate(None, max_quote_age_seconds=MAX_AGE)
    assert s.state == "unknown"
    assert s.label == "Datenalter unbekannt"
    assert s.chip_class == "chip--caution"        # wie „verzoegert" behandelt
    assert s.age_seconds is None
    assert s.blocks_orders is False
    assert s == feed_status.unknown()


def test_negative_age_is_clamped_instead_of_shown_as_future():
    s = _status(-5.0)
    assert s.state == "fresh"
    assert s.age_seconds == 0.0


# ── Die eigentliche Zusicherung: gleiche Grenze wie das Gate ─────────────────

def _gate_rejects(age: float, max_age: float) -> bool:
    """Ruft das echte Backend-Gate mit einem Quote dieses Alters auf."""
    now = datetime(2024, 5, 6, 12, 0, tzinfo=timezone.utc)
    quote = Quote(ticker="AAPL", price=1.0, as_of=now - timedelta(seconds=age),
                  fetched_at=now, provider="test")
    return not check_quote_age(quote, max_age_seconds=max_age, now=now).ok


@pytest.mark.parametrize("max_age", [5.0, 30.0, 60.0, 120.0, 900.0])
@pytest.mark.parametrize("delta", [-10.0, -1.0, -0.001, 0.0, 0.001, 1.0, 10.0])
def test_stale_boundary_matches_the_backend_gate(max_age, delta):
    """Inkl. Grenzwert selbst: UI und Gate sind sich an jeder Schwelle einig."""
    age = max_age + delta
    if age < 0:
        pytest.skip("negatives Alter ist kein gueltiger Quote-Fall")
    ui_blocks = feed_status.evaluate(age, max_quote_age_seconds=max_age).blocks_orders
    assert ui_blocks is _gate_rejects(age, max_age)
    assert feed_status.is_stale(age, max_quote_age_seconds=max_age) is ui_blocks


def test_default_risk_profile_limit_is_the_one_the_ui_uses():
    """Das UI zieht seinen Grenzwert aus RiskProfile — kein zweiter hartkodierter Wert."""
    limit = float(RiskProfile(user_id=0).max_quote_age_seconds)
    assert feed_status.evaluate(limit, max_quote_age_seconds=limit).blocks_orders is False
    assert feed_status.evaluate(limit + 1, max_quote_age_seconds=limit).blocks_orders is True
