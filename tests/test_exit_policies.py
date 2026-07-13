from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from stockbot.market.exit_policies import (
    evaluate_strategy_exit,
    mean_reversion_exit,
    momentum_exit,
    swing_trend_exit,
)


NOW = datetime(2026, 7, 13, 16, 0)


def _bars(closes) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "Open": closes,
        "High": closes + 0.5,
        "Low": closes - 0.5,
        "Close": closes,
        "Volume": np.full(len(closes), 1_000.0),
    })


@pytest.mark.parametrize("policy", [momentum_exit, swing_trend_exit])
def test_trailing_stop_fires_at_threshold_but_not_just_above(policy):
    assert policy(current_price=94.0, highest_price_since_entry=100.0, atr=2.0).code == "trailing_stop"
    assert not policy(current_price=94.01, highest_price_since_entry=100.0, atr=2.0).close


def test_momentum_break_fires_and_near_miss_does_not():
    falling = _bars(np.r_[np.linspace(80, 110, 35), np.linspace(109, 90, 10)])
    assert momentum_exit(bars=falling).code == "momentum_break"

    # Negative MACD alone is insufficient when the injected current price remains above MA20.
    assert not momentum_exit(bars=falling, current_price=200.0).close


def test_momentum_entry_timeout_fires_only_past_age_and_below_minimum_gain():
    inputs = dict(entry_price=100.0, current_price=100.99, opened_at=NOW - timedelta(minutes=391),
                  now=NOW, minimum_gain_pct=1.0)
    assert momentum_exit(**inputs).code == "entry_timeout"
    assert not momentum_exit(**{**inputs, "current_price": 101.0}).close
    assert not momentum_exit(**{**inputs, "opened_at": NOW - timedelta(minutes=390)}).close


def test_market_close_fires_at_threshold_but_not_just_above():
    assert momentum_exit(minutes_to_close=15).code == "market_close"
    assert not momentum_exit(minutes_to_close=15.01).close


def test_swing_structure_break_by_ma200_and_near_miss():
    bars = _bars(np.full(200, 100.0))
    assert swing_trend_exit(bars=bars, current_price=99.99).code == "structure_break"
    assert not swing_trend_exit(bars=bars, current_price=100.0).close


def test_swing_structure_break_by_bearish_supertrend(monkeypatch):
    bars = _bars(np.r_[np.full(20, 100.0), np.linspace(99, 70, 20)])
    monkeypatch.setattr(
        "stockbot.market.exit_policies.strategies._supertrend_dir",
        lambda *args, **kwargs: np.r_[np.ones(len(bars) - 1), -1.0],
    )
    assert swing_trend_exit(bars=bars, current_price=70.0).code == "structure_break"


def test_swing_max_hold_fires_only_after_default_threshold():
    assert swing_trend_exit(opened_at=NOW - timedelta(days=40, seconds=1), now=NOW).code == "max_hold"
    assert not swing_trend_exit(opened_at=NOW - timedelta(days=40), now=NOW).close


def test_swing_event_filter_is_injected_boolean_hook():
    assert swing_trend_exit(blocking_event=True).code == "event_filter"
    assert not swing_trend_exit(blocking_event=False).close


def test_mean_reverted_fires_at_band_midpoint_but_not_just_below():
    history = np.linspace(90, 110, 19)
    bars = _bars(np.r_[history, 100.0])
    midpoint = float(np.mean(bars.Close.iloc[-20:]))
    assert mean_reversion_exit(bars=bars, current_price=midpoint).code == "mean_reverted"
    assert not mean_reversion_exit(bars=bars, current_price=midpoint - 0.01).close


def test_mean_reversion_regime_break_and_near_miss():
    bars = _bars(np.r_[np.full(180, 98.0), np.full(20, 110.0)])
    ma200 = float(bars.Close.mean())
    assert mean_reversion_exit(bars=bars, current_price=ma200 - 0.01).code == "regime_break"
    # At MA200 the strict regime-break threshold does not fire; price is still below MA20.
    assert not mean_reversion_exit(bars=bars, current_price=ma200).close


def test_mean_reversion_time_exit_fires_only_after_default_threshold():
    assert mean_reversion_exit(opened_at=NOW - timedelta(days=10, seconds=1), now=NOW).code == "time_exit"
    assert not mean_reversion_exit(opened_at=NOW - timedelta(days=10), now=NOW).close


@pytest.mark.parametrize("policy", [momentum_exit, swing_trend_exit, mean_reversion_exit])
def test_missing_inputs_hold(policy):
    decision = policy()
    assert decision.close is False
    assert decision.code == ""


@pytest.mark.parametrize(
    ("key", "inputs", "expected"),
    [
        ("standard", {"minutes_to_close": 15}, "market_close"),
        ("ai_adaptive", {"blocking_event": True}, "event_filter"),
        ("bb_revert", {"opened_at": NOW - timedelta(days=11), "now": NOW}, "time_exit"),
    ],
)
def test_dispatcher_selects_registry_family(key, inputs, expected):
    assert evaluate_strategy_exit(key, **inputs).code == expected


def test_dispatcher_returns_no_policy_for_research_only_and_unknown():
    assert evaluate_strategy_exit("adx_trend").code == "no_policy"
    assert evaluate_strategy_exit("does_not_exist").code == "no_policy"
