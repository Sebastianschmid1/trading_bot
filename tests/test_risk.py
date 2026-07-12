"""
Tests für die zentrale Pre-Trade-Risk-Vorprüfung (stockbot.core.risk).
Phase 0 / TSAFE-007 — harte Invarianten: Live-Kill-Switch, Hebel-Deckel, Optionsverbot.
Phase 3 / RISK-003 — Markt offen + Quote-Frische/Spread (die übrigen Schritte folgen separat).
"""

from datetime import datetime, timedelta, timezone

from stockbot.core import risk
from stockbot.core.domain import SignalStatus
from stockbot.core.market_data import Quote
from stockbot import config


def test_allows_plain_paper_share_order():
    d = risk.pretrade_check(leverage=1.0, is_option=False, is_live_account=False)
    assert d.ok is True


def test_blocks_live_account_when_live_disabled():
    orig = config.LIVE_TRADING_ENABLED
    config.LIVE_TRADING_ENABLED = False
    try:
        d = risk.pretrade_check(is_live_account=True)
        assert d.ok is False and d.code == "live_blocked"
    finally:
        config.LIVE_TRADING_ENABLED = orig


def test_blocks_leverage_over_max():
    d = risk.pretrade_check(leverage=config.MAX_LEVERAGE + 1)
    assert d.ok is False and d.code == "leverage_blocked"


def test_blocks_options_when_disabled():
    orig = config.ALLOW_OPTIONS
    config.ALLOW_OPTIONS = False
    try:
        d = risk.pretrade_check(is_option=True)
        assert d.ok is False and d.code == "options_blocked"
    finally:
        config.ALLOW_OPTIONS = orig


def test_live_account_allowed_when_live_enabled():
    orig = config.LIVE_TRADING_ENABLED
    config.LIVE_TRADING_ENABLED = True
    try:
        assert risk.pretrade_check(leverage=1.0, is_live_account=True).ok is True
    finally:
        config.LIVE_TRADING_ENABLED = orig


# ── RISK-003: Markt offen + Quote-Frische/Spread ─────────────────────────────

def test_blocks_when_market_closed():
    d = risk.pretrade_check(market_open=False)
    assert d.ok is False and d.code == "market_closed"


def test_allows_when_market_open_true():
    assert risk.pretrade_check(market_open=True).ok is True


def test_market_open_check_skipped_when_not_provided():
    assert risk.pretrade_check().ok is True


def test_blocks_on_stale_quote():
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    q = Quote(ticker="AAPL", price=100.0, as_of=now - timedelta(seconds=120),
             fetched_at=now, provider="stub")
    d = risk.pretrade_check(quote=q, max_quote_age_seconds=30, now=now)
    assert d.ok is False and d.code == "quote_stale"


def test_allows_fresh_quote():
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    q = Quote(ticker="AAPL", price=100.0, as_of=now - timedelta(seconds=5),
             fetched_at=now, provider="stub")
    assert risk.pretrade_check(quote=q, max_quote_age_seconds=30, now=now).ok is True


def test_blocks_on_wide_spread():
    q = Quote(ticker="AAPL", price=100.0, as_of=datetime.now(timezone.utc),
             fetched_at=datetime.now(timezone.utc), provider="stub", bid=100.0, ask=101.0)
    d = risk.pretrade_check(quote=q, max_spread_bps=10)
    assert d.ok is False and d.code == "spread_wide"


def test_quote_checks_skipped_without_quote():
    assert risk.pretrade_check(max_quote_age_seconds=1, max_spread_bps=1).ok is True


def test_leverage_check_still_wins_over_market_closed():
    # Reihenfolge: Kill-Switch/Hebel/Optionen zuerst, dann erst Markt-offen (RISK-003-Reihenfolge)
    d = risk.pretrade_check(leverage=config.MAX_LEVERAGE + 1, market_open=False)
    assert d.ok is False and d.code == "leverage_blocked"


# ── RISK-003: Signal gültig/nicht abgelaufen, Strategie erlaubt, Liquidität ──

def test_blocks_when_signal_status_not_accepted():
    d = risk.pretrade_check(signal_status=SignalStatus.PUBLISHED)
    assert d.ok is False and d.code == "signal_invalid"


def test_allows_when_signal_status_accepted():
    assert risk.pretrade_check(signal_status=SignalStatus.ACCEPTED).ok is True


def test_signal_status_check_skipped_when_not_provided():
    assert risk.pretrade_check().ok is True


def test_blocks_when_signal_expired():
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    d = risk.pretrade_check(signal_expires_at=now - timedelta(minutes=1), now=now)
    assert d.ok is False and d.code == "signal_expired"


def test_allows_when_signal_not_yet_expired():
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    assert risk.pretrade_check(signal_expires_at=now + timedelta(minutes=1), now=now).ok is True


def test_blocks_when_strategy_not_in_allowed_list():
    d = risk.pretrade_check(strategy_key="mean_reversion", allowed_strategies=("swing_trend",))
    assert d.ok is False and d.code == "strategy_not_allowed"


def test_allows_when_strategy_in_allowed_list():
    assert risk.pretrade_check(
        strategy_key="swing_trend", allowed_strategies=("swing_trend", "intraday_momentum")).ok is True


def test_strategy_check_skipped_when_allowed_strategies_empty():
    # Leere allowed_strategies (RiskProfile-Default) blockiert nichts.
    assert risk.pretrade_check(strategy_key="anything", allowed_strategies=()).ok is True


def test_blocks_on_low_liquidity():
    d = risk.pretrade_check(average_dollar_volume=50_000, min_average_dollar_volume=100_000)
    assert d.ok is False and d.code == "liquidity_low"


def test_allows_sufficient_liquidity():
    assert risk.pretrade_check(
        average_dollar_volume=200_000, min_average_dollar_volume=100_000).ok is True


def test_liquidity_check_skipped_without_min_volume():
    assert risk.pretrade_check(average_dollar_volume=1.0).ok is True


def test_signal_invalid_wins_over_market_closed():
    d = risk.pretrade_check(signal_status=SignalStatus.EXPIRED, market_open=False)
    assert d.ok is False and d.code == "signal_invalid"
