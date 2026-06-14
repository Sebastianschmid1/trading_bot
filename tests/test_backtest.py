"""
Offline-Tests für Kennzahlen, Strategie-Registry, ADX-Strategie und Backtest-Engine.
Kein Netzwerk (synthetische Kurse).

Lauf:  python test_backtest.py   oder   pytest test_backtest.py
"""

import sys

import numpy as np
import pandas as pd

from stockbot.core import metrics
from stockbot.market import strategies
from stockbot.backtest import engine as backtest


# ── metrics ──────────────────────────────────────────────────────────────────

def test_profit_factor_and_winrate():
    trades = [{"pnl_eur": 30.0}, {"pnl_eur": -10.0}, {"pnl_eur": 20.0}, {"pnl_eur": -10.0}]
    m = metrics.compute_metrics(trades)
    assert m["gross_profit"] == 50.0 and m["gross_loss"] == -20.0
    assert m["profit_factor"] == 2.5          # 50 / 20
    assert m["win_rate"] == 50.0 and m["trades"] == 4


def test_profit_factor_no_losses_is_none():
    m = metrics.compute_metrics([{"pnl_eur": 5.0}, {"pnl_eur": 1.0}])
    assert m["profit_factor"] is None         # kein Verlust → ∞ (None)


def test_trade_level_extras():
    trades = [{"pnl_eur": 10.0, "pnl_pct": 10.0}, {"pnl_eur": -5.0, "pnl_pct": -5.0},
              {"pnl_eur": 10.0, "pnl_pct": 10.0}, {"pnl_eur": 10.0, "pnl_pct": 10.0}]
    m = metrics.compute_metrics(trades)
    assert m["payoff_ratio"] == 2.0                     # Ø10 / |Ø-5|
    assert m["max_consec_wins"] == 2 and m["max_consec_losses"] == 1
    assert m["best_trade_pct"] == 10.0 and m["worst_trade_pct"] == -5.0
    assert m["kelly_pct"] == 62.5                        # (0.75 − 0.25/2)·100
    assert m["t_stat"] is not None and m["t_stat"] > 0


def test_equity_metrics_returns_and_drawdown():
    eq = [100.0, 120.0, 90.0, 110.0, 130.0]
    m = metrics.equity_metrics(eq)
    assert round(m["total_return_pct"], 1) == 30.0       # 130/100 − 1
    assert m["max_dd_pct"] == 25.0                        # (120−90)/120
    assert m["max_dd_days"] == 2                          # 90,110 unter Wasser bis neues Hoch 130


def test_equity_metrics_beta_corr_identical_series():
    eq = [100.0, 110.0, 105.0, 115.0, 120.0]
    m = metrics.equity_metrics(eq, bench=eq)             # Strategie == Benchmark
    assert m["beta"] == 1.0 and m["corr"] == 1.0
    assert abs(m["alpha_pct"]) < 1e-6                     # kein Mehrwert ggü. sich selbst


def test_equity_metrics_flat_is_safe():
    m = metrics.equity_metrics([100.0, 100.0, 100.0])    # keine Bewegung
    assert m["sharpe"] is None and m["calmar"] is None and m["max_dd_pct"] == 0.0


def test_max_drawdown():
    # +100, dann -60 → Peak 1100, Tief 1040 → DD = 60/1100
    m = metrics.compute_metrics([{"pnl_eur": 100.0}, {"pnl_eur": -60.0}], initial_capital=1000.0)
    assert round(m["max_drawdown_pct"], 2) == round(60 / 1100 * 100, 2)


def test_empty_metrics_safe():
    m = metrics.compute_metrics([])
    assert m["trades"] == 0 and m["profit_factor"] is None


# ── Registry ─────────────────────────────────────────────────────────────────

def test_registry_has_two_strategies():
    keys = {s.key for s in strategies.all_strategies()}
    assert {"standard", "adx_trend"} <= keys


def test_get_falls_back_to_default():
    assert strategies.get("gibts_nicht").key == strategies.DEFAULT_STRATEGY
    assert strategies.get("adx_trend").key == "adx_trend"


# ── 52-Wochen-Hoch-Momentum (aus der Strategiesuche) ─────────────────────────

def test_registry_has_momentum_strategies():
    keys = {s.key for s in strategies.all_strategies()}
    assert {"high52", "high52_wide"} <= keys
    for k in ("high52", "high52_wide"):
        assert strategies.get(k).score is strategies.high52_score   # Live-Score verdrahtet


def test_high52_fires_near_high_not_in_downtrend():
    up = _series(100 + np.arange(300) * 0.5)            # stetiger Aufwärtstrend → am 52W-Hoch
    sig = strategies.high52_signal("X", {"1d": up})
    assert sig is not None and sig["strategy"] == "high52"
    assert sig["stop_loss"] < sig["price"] < sig["take_profit"]
    down = _series(250 - np.arange(300) * 0.6)          # Abwärtstrend → weit unter Hoch
    assert strategies.high52_signal("X", {"1d": down}) is None


def test_high52_wide_fires_when_strict_does_not():
    close = np.full(300, 100.0)
    close[-30:] = 100 + np.arange(30) * 2.0             # Ramp ans Hoch
    close[-1] = 0.96 * close.max()                      # knapp drunter: zwischen 95 % und 98 %
    df = _series(close)
    assert strategies.high52_signal("X", {"1d": df}) is None         # streng (≥98 %): nein
    assert strategies.high52_wide_signal("X", {"1d": df}) is not None  # aktiv (≥95 %): ja


def test_high52_score_high_near_high_low_when_broken():
    up = _series(100 + np.arange(300) * 0.5)
    down = _series(250 - np.arange(300) * 0.6)
    assert strategies.high52_score({"1d": up}) >= 70
    assert strategies.high52_score({"1d": down}) < 35


# ── ADX-Strategie ────────────────────────────────────────────────────────────

def _series(close, start="2022-01-01"):
    idx = pd.date_range(start, periods=len(close), freq="B")
    c = np.asarray(close, dtype=float)
    return pd.DataFrame({"Open": c, "High": c + 0.8, "Low": c - 0.8,
                         "Close": c, "Volume": np.full(len(c), 1_000_000.0)}, index=idx)


def test_adx_too_few_bars_returns_none():
    df = _series(100 + np.arange(50) * 0.1)
    assert strategies.adx_trend_signal("X", {"1d": df}) is None


# ── Fortlaufende Live-Scores (Monitoring je Strategie) ───────────────────────

def test_breakout_score_reflects_trend_health():
    up = _series(100 + np.arange(80) * 0.5)        # Kurs deutlich über MA50
    down = _series(120 - np.arange(80) * 0.5)      # Kurs unter MA50 → These gebrochen
    s_up = strategies.breakout_score({"1d": up})
    s_down = strategies.breakout_score({"1d": down})
    assert 0 <= s_down < 35 <= s_up <= 100         # intakt > Schwelle, gebrochen darunter


def test_rsi_revert_score_dead_when_trend_breaks():
    # Kurs unter MA200 → langfristiger Aufwärtstrend gebrochen → These tot (20)
    down = _series(np.concatenate([np.full(150, 150.0), 150 - np.arange(60) * 1.0]))
    assert strategies.rsi_revert_score({"1d": down}) == 20.0


def test_ma_trend_score_high_when_fully_stacked():
    up = _series(100 + np.arange(220) * 0.6)       # sauber gestapelter Aufwärtstrend
    assert strategies.ma_trend_score({"1d": up}) >= 70


def test_live_scores_uses_each_trades_own_strategy(monkeypatch=None):
    # _download_all_timeframes/_tf_data_for mocken → kein Netz; je Strategie eigener Score
    from stockbot.market import analyzer
    up = _series(100 + np.arange(220) * 0.6)
    orig_dl, orig_tf, orig_lp = (analyzer._download_all_timeframes,
                                 analyzer._tf_data_for, analyzer.last_price)
    analyzer._download_all_timeframes = lambda tickers: {"_": tickers}
    analyzer._tf_data_for = lambda downloads, ticker: {"1d": up}
    analyzer.last_price = lambda tf: 230.0
    try:
        out = strategies.live_scores({("AAA", "breakout"), ("BBB", "ma_trend")})
    finally:
        analyzer._download_all_timeframes, analyzer._tf_data_for, analyzer.last_price = \
            orig_dl, orig_tf, orig_lp
    assert out[("AAA", "breakout")]["strength"] == strategies.breakout_score({"1d": up})
    assert out[("BBB", "ma_trend")]["strength"] == strategies.ma_trend_score({"1d": up})
    assert out[("AAA", "breakout")]["price"] == 230.0


def test_adx_fires_on_trend_with_expansion():
    n = 320
    t = np.arange(n)
    close = 100 + 0.10 * t + 1.5 * np.sin(t / 6.0)     # Aufwärtstrend + Zyklus
    for k in range(6):                                  # Volatilitäts-/Momentum-Schub am Ende
        close[n - 6 + k] += 3.0 * (k + 1)
    df = _series(close)

    found = None
    for end in range(260, n + 1):                       # gesamten gültigen Bereich abscannen
        sig = strategies.adx_trend_signal("X", {"1d": df.iloc[:end]})
        if sig:
            found = sig
            break
    assert found is not None, "ADX-Strategie hätte im Expansions-Fenster auslösen sollen"
    assert found["direction"] == "long"
    assert found["stop_loss"] < found["price"] < found["take_profit"]
    assert 0 <= found["strength"] <= 100
    assert found["strategy"] == "adx_trend"


# ── Backtest-Engine (deterministisch mit Dummy-Strategie) ────────────────────

def _dummy_long():
    def gen(ticker, tf_data):
        df = tf_data["1d"]
        price = float(df["Close"].iloc[-1])
        return {"ticker": ticker, "direction": "long", "price": price,
                "stop_loss": price * 0.98, "take_profit": price * 1.05}
    return strategies.Strategy("dummy", "Dummy", gen)


def test_backtest_engine_detects_tp_then_sl():
    n = 15
    close = np.full(n, 100.0)
    high = close + 0.5
    low = close - 0.5
    high[3] = 106.0     # nach Einstieg bei i=2 → Take-Profit (105)
    low[5] = 97.0       # nach Einstieg bei i=4 → Stop-Loss (98)
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    df = pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close,
                       "Volume": np.full(n, 1e6)}, index=idx)

    trades = backtest.backtest_ticker(_dummy_long(), "X", df, trade_size=1000.0,
                                      max_hold=10, warmup=2)
    assert len(trades) >= 2
    assert trades[0]["reason"] == "Take-Profit" and trades[0]["pnl_eur"] > 0
    assert trades[1]["reason"] == "Stop-Loss" and trades[1]["pnl_eur"] < 0
    assert round(trades[0]["pnl_pct"], 1) == 5.0
    assert round(trades[1]["pnl_pct"], 1) == -2.0


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"OK  {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERR  {name}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
