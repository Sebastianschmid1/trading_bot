"""
Offline-Tests für Kennzahlen, Strategie-Registry, ADX-Strategie und Backtest-Engine.
Kein Netzwerk (synthetische Kurse).

Lauf:  python test_backtest.py   oder   pytest test_backtest.py
"""

import sys

import numpy as np
import pandas as pd

import metrics
import strategies
import backtest


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


# ── ADX-Strategie ────────────────────────────────────────────────────────────

def _series(close, start="2022-01-01"):
    idx = pd.date_range(start, periods=len(close), freq="B")
    c = np.asarray(close, dtype=float)
    return pd.DataFrame({"Open": c, "High": c + 0.8, "Low": c - 0.8,
                         "Close": c, "Volume": np.full(len(c), 1_000_000.0)}, index=idx)


def test_adx_too_few_bars_returns_none():
    df = _series(100 + np.arange(50) * 0.1)
    assert strategies.adx_trend_signal("X", {"1d": df}) is None


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
