"""
Tests für die Datenqualitäts-Gates (DATA-004, stockbot/core/data_quality.py, Plan.md §10.4).

Reine Entscheidungstests, kein IO — dieselbe Struktur wie tests/test_risk.py (Decision-Dataclass
mit ok/reason/code statt Exceptions).
"""

from datetime import datetime, timedelta, timezone

import pandas as pd

from stockbot.core import data_quality as dq
from stockbot.core.market_data import CorporateAction, MarketStatus, Quote

NOW = datetime(2026, 6, 10, 15, 0, tzinfo=timezone.utc)


def _quote(**overrides):
    fields = dict(ticker="AAPL", price=100.0, as_of=NOW, fetched_at=NOW, provider="stub")
    fields.update(overrides)
    return Quote(**fields)


def _bars(**overrides):
    data = {"Open": [1.0], "High": [1.1], "Low": [0.9], "Close": [1.05], "Volume": [1000]}
    data.update(overrides)
    return pd.DataFrame(data)


# ── check_quote_age ──────────────────────────────────────────────────────────

def test_quote_age_ok_when_fresh():
    q = _quote(as_of=NOW - timedelta(seconds=5))
    d = dq.check_quote_age(q, max_age_seconds=30, now=NOW)
    assert d.ok is True


def test_quote_age_blocked_when_stale():
    q = _quote(as_of=NOW - timedelta(seconds=60))
    d = dq.check_quote_age(q, max_age_seconds=30, now=NOW)
    assert d.ok is False and d.code == "quote_stale"


# ── check_spread ─────────────────────────────────────────────────────────────

def test_spread_ok_within_limit():
    q = _quote(bid=100.0, ask=100.05)
    d = dq.check_spread(q, max_spread_bps=10)
    assert d.ok is True


def test_spread_blocked_when_too_wide():
    q = _quote(bid=100.0, ask=101.0)   # 100 bps
    d = dq.check_spread(q, max_spread_bps=10)
    assert d.ok is False and d.code == "spread_wide"


def test_spread_not_applicable_without_bid_ask():
    q = _quote(bid=None, ask=None)
    assert dq.check_spread(q, max_spread_bps=1).ok is True


# ── check_bars_complete / check_no_nan ───────────────────────────────────────

def test_bars_complete_ok():
    assert dq.check_bars_complete(_bars()).ok is True


def test_bars_complete_blocked_when_empty():
    d = dq.check_bars_complete(pd.DataFrame())
    assert d.ok is False and d.code == "bars_missing"


def test_bars_complete_blocked_when_none():
    d = dq.check_bars_complete(None)
    assert d.ok is False and d.code == "bars_missing"


def test_bars_complete_blocked_when_column_missing():
    bars = _bars()[["Open", "High", "Low", "Close"]]   # Volume fehlt
    d = dq.check_bars_complete(bars)
    assert d.ok is False and d.code == "bars_incomplete"


def test_bars_complete_blocked_when_too_few_rows():
    d = dq.check_bars_complete(_bars(), min_rows=5)
    assert d.ok is False and d.code == "bars_incomplete"


def test_no_nan_ok():
    assert dq.check_no_nan(_bars()).ok is True


def test_no_nan_blocked_when_nan_present():
    bars = _bars(Close=[float("nan")])
    d = dq.check_no_nan(bars)
    assert d.ok is False and d.code == "nan_values"


# ── check_symbol_active / check_no_halt / check_market_status ───────────────

def test_symbol_active_ok():
    assert dq.check_symbol_active(tradable=True).ok is True


def test_symbol_active_blocked_when_not_tradable():
    d = dq.check_symbol_active(tradable=False)
    assert d.ok is False and d.code == "symbol_inactive"


def test_no_halt_ok():
    assert dq.check_no_halt(halted=False).ok is True


def test_no_halt_blocked_when_halted():
    d = dq.check_no_halt(halted=True)
    assert d.ok is False and d.code == "trading_halted"


def test_market_status_ok_when_matching_calendar():
    status = MarketStatus(is_open=True, session="regular", as_of=NOW, provider="stub")
    assert dq.check_market_status(status, exchange_open=True).ok is True


def test_market_status_blocked_on_mismatch():
    status = MarketStatus(is_open=True, session="regular", as_of=NOW, provider="stub")
    d = dq.check_market_status(status, exchange_open=False)
    assert d.ok is False and d.code == "market_status_mismatch"


# ── check_corporate_actions ──────────────────────────────────────────────────

def test_corporate_actions_ok_when_none_recent():
    actions = [CorporateAction(ticker="AAPL", action_type="dividend", ex_date=NOW - timedelta(days=1))]
    d = dq.check_corporate_actions(actions, since=NOW - timedelta(hours=1))
    assert d.ok is True


def test_corporate_actions_blocked_on_recent_split():
    actions = [CorporateAction(ticker="AAPL", action_type="split", ex_date=NOW - timedelta(hours=1))]
    d = dq.check_corporate_actions(actions, since=NOW - timedelta(days=1))
    assert d.ok is False and d.code == "corporate_action_pending"


# ── evaluate_quality (Bündelung, feste Reihenfolge) ──────────────────────────

def test_evaluate_quality_ok_with_no_optional_inputs():
    assert dq.evaluate_quality().ok is True


def test_evaluate_quality_blocks_on_inactive_symbol_first():
    d = dq.evaluate_quality(tradable=False, halted=True)
    assert d.ok is False and d.code == "symbol_inactive"     # vor Halt-Check


def test_evaluate_quality_blocks_on_halt():
    d = dq.evaluate_quality(tradable=True, halted=True)
    assert d.ok is False and d.code == "trading_halted"


def test_evaluate_quality_blocks_on_stale_quote():
    q = _quote(as_of=NOW - timedelta(seconds=120))
    d = dq.evaluate_quality(quote=q, max_quote_age_seconds=30)
    assert d.ok is False and d.code == "quote_stale"


def test_evaluate_quality_blocks_on_incomplete_bars():
    d = dq.evaluate_quality(bars=pd.DataFrame())
    assert d.ok is False and d.code == "bars_missing"


def test_evaluate_quality_ignores_checks_without_inputs():
    # Ohne quote/bars/market_status übergeben zu haben, dürfen die jeweiligen Checks nicht greifen.
    d = dq.evaluate_quality(max_quote_age_seconds=1, max_spread_bps=1)
    assert d.ok is True
