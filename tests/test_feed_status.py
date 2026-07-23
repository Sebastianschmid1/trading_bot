"""Feed-Status (Stylekonzept §32.3).

Zwei Schwellen, weil zwei verschiedene Groessen gemessen werden: die Gate-Grenze aus
`core/data_quality.check_quote_age` trennt „aktuell" von „verzoegert" (Alter der
Ausfuehrungs-Quote), gesperrt wird erst ab der UI-Konstante `UI_STALE_SECONDS` (Alter
des angezeigten Preises als Entscheidungsgrundlage)."""
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

def test_fresh_ends_exactly_at_the_gate_limit():
    """„aktuell" wird nie fuer etwas behauptet, das das Ausfuehrungs-Gate ablehnen wuerde."""
    s = _status(0.0)
    assert s.state == "fresh"
    assert s.label == "aktuell"
    assert s.chip_class == "chip--go"
    assert _status(MAX_AGE).state == "fresh"            # Grenzwert selbst ist noch ok
    assert _status(MAX_AGE + 0.1).state != "fresh"


def test_delayed_spans_from_the_gate_limit_up_to_the_ui_block_threshold():
    s = _status(MAX_AGE + 0.1)
    assert s.state == "delayed"
    assert s.chip_class == "chip--caution"
    assert s.blocks_orders is False
    assert _status(120.0).label == "verzögert · 120 s"
    assert _status(feed_status.UI_STALE_SECONDS - 0.1).state == "delayed"


def test_stale_starts_at_the_named_ui_constant_not_at_the_gate_limit():
    assert feed_status.UI_STALE_SECONDS == 180.0
    s = _status(feed_status.UI_STALE_SECONDS)
    assert s.state == "stale"
    assert s.label == "veraltet – keine neuen Trades"
    assert s.chip_class == "chip--warn"


# ── blocks_orders nur bei stale ──────────────────────────────────────────────

@pytest.mark.parametrize("age,expected", [(0.0, False), (30.0, False), (60.0, False),
                                          (60.5, False), (179.9, False),
                                          (180.0, True), (600.0, True), (None, False)])
def test_blocks_orders_only_when_stale(age, expected):
    s = feed_status.evaluate(age, max_quote_age_seconds=MAX_AGE)
    assert s.blocks_orders is expected
    assert (s.state == "stale") is expected


def test_stale_reason_names_age_ui_limit_and_that_exits_stay_possible():
    s = _status(300.0)
    assert "300" in s.reason and "180" in s.reason
    assert "Neue Einstiege sind gesperrt" in s.reason
    assert "schließen" in s.reason          # Exits bleiben ausdruecklich moeglich
    assert _status(5.0).reason == ""


def test_ordering_holds_when_the_gate_limit_exceeds_the_ui_threshold():
    """max_quote_age_seconds > UI_STALE_SECONDS darf die Staffelung nicht kippen."""
    generous = feed_status.UI_STALE_SECONDS + 600.0     # Gate wuerde 780 s durchlassen
    assert feed_status.is_stale(200.0, max_quote_age_seconds=generous) is False
    s = feed_status.evaluate(200.0, max_quote_age_seconds=generous)
    assert s.state == "stale"                            # stale gewinnt ueber fresh
    assert s.blocks_orders is True
    # unterhalb der UI-Grenze bleibt es bei fresh
    assert feed_status.evaluate(179.9, max_quote_age_seconds=generous).state == "fresh"


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
def test_fresh_boundary_matches_the_backend_gate(max_age, delta):
    """Inkl. Grenzwert selbst: `is_stale` und das Gate sind sich an jeder Schwelle einig,
    und „aktuell" endet genau dort, wo das Gate ablehnen wuerde."""
    age = max_age + delta
    if age < 0:
        pytest.skip("negatives Alter ist kein gueltiger Quote-Fall")
    gate_rejects = _gate_rejects(age, max_age)
    assert feed_status.is_stale(age, max_quote_age_seconds=max_age) is gate_rejects
    if age < feed_status.UI_STALE_SECONDS:      # darueber uebersteuert die UI-Blockgrenze
        is_fresh = feed_status.evaluate(age, max_quote_age_seconds=max_age).state == "fresh"
        assert is_fresh is (not gate_rejects)


def test_default_risk_profile_limit_is_the_one_the_ui_uses():
    """Das UI zieht die fresh-Grenze aus RiskProfile — kein zweiter hartkodierter Wert."""
    limit = float(RiskProfile(user_id=0).max_quote_age_seconds)
    assert feed_status.evaluate(limit, max_quote_age_seconds=limit).state == "fresh"
    assert feed_status.evaluate(limit + 1, max_quote_age_seconds=limit).state == "delayed"


def test_ui_block_threshold_is_independent_of_the_risk_profile():
    """Die Blockgrenze misst eine andere Groesse und darf nicht mit dem Profil wandern."""
    age = feed_status.UI_STALE_SECONDS + 1
    for max_age in (5.0, 60.0, 900.0):
        assert feed_status.evaluate(age, max_quote_age_seconds=max_age).blocks_orders is True
        assert feed_status.evaluate(
            feed_status.UI_STALE_SECONDS - 1, max_quote_age_seconds=max_age
        ).blocks_orders is False
