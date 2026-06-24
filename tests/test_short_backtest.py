"""
Tests für Short-Signale + Short-Backtest (nur Backtest; Live bleibt long-only).

- analyzer.analyze_ticker(allow_short=True) erzeugt Short-Signale (gespiegelte Logik);
  ohne Flag (Live-Pfad) NIE Shorts.
- backtest_ticker(allow_short=True) simuliert Short-Trades mit korrektem P&L-Vorzeichen.
- engine._direction_split zählt Long/Short.

Lauf:  pytest tests/test_short_backtest.py   (offline, synthetische Kurse)
"""

import numpy as np
import pandas as pd

from stockbot.backtest import engine
from stockbot.market import strategies as S
from stockbot.market import analyzer


def _mixed_df(seed=3, years=6):
    """Synthetische Tageskurse mit Auf- UND Abwärtsphase (löst beide Richtungen aus)."""
    n = years * 252
    idx = pd.bdate_range("2015-01-01", periods=n)
    rng = np.random.default_rng(seed)
    drift = np.concatenate([np.full(n // 2, 0.0006), np.full(n - n // 2, -0.0006)])
    ret = rng.normal(0, 0.015, n) + drift
    close = 100 * np.exp(np.cumsum(ret))
    return pd.DataFrame({"Open": close * 0.999, "High": close * 1.012, "Low": close * 0.988,
                         "Close": close, "Volume": rng.integers(1e6, 5e6, n).astype(float)}, index=idx)


# ── Live bleibt long-only ────────────────────────────────────────────────────

def test_live_standard_generate_never_shorts():
    """Der Live-Pfad (strategies.standard.generate, ohne allow_short) liefert NIE einen Short —
    über viele Zeitfenster der gemischten Reihe geprüft."""
    df = _mixed_df()
    gen = S.get("standard").generate
    seen = set()
    for i in range(260, len(df), 25):
        sig = gen("T", {"1d": df.iloc[:i + 1]})
        if sig:
            seen.add(sig["direction"])
    assert "short" not in seen        # Live ist und bleibt long-only
    assert seen <= {"long"}


def test_backtest_long_only_has_no_shorts():
    df = _mixed_df()
    trades = engine.backtest_ticker(S.get("standard"), "T", df, 1000.0, allow_short=False)
    assert trades and all(t["direction"] == "long" for t in trades)


# ── Short-Signale + Short-Backtest ───────────────────────────────────────────

def test_backtest_allow_short_produces_shorts():
    df = _mixed_df()
    trades = engine.backtest_ticker(S.get("standard"), "T", df, 1000.0, allow_short=True)
    shorts = [t for t in trades if t["direction"] == "short"]
    assert len(shorts) > 0


def test_short_trade_pnl_sign_is_correct():
    """Bei einem Short ist ein Ausstieg UNTER dem Einstieg ein Gewinn (und umgekehrt)."""
    df = _mixed_df()
    trades = engine.backtest_ticker(S.get("standard"), "T", df, 1000.0, allow_short=True)
    shorts = [t for t in trades if t["direction"] == "short"]
    assert shorts
    for t in shorts:
        if t["exit"] != t["entry"]:
            assert (t["exit"] < t["entry"]) == (t["pnl_eur"] > 0)


def test_short_signal_has_mirrored_sl_tp():
    """Ein Short-Signal hat SL ÜBER und TP UNTER dem Kurs (gespiegelt zum Long)."""
    df = _mixed_df()
    found = None
    for i in range(260, len(df)):
        sig = analyzer.analyze_ticker("T", {"1d": df.iloc[:i + 1]}, allow_short=True)
        if sig and sig["direction"] == "short" and sig.get("stop_loss"):
            found = sig
            break
    assert found is not None
    assert found["stop_loss"] > found["price"] > found["take_profit"]


def test_direction_split_counts():
    trades = [
        {"direction": "long", "pnl_eur": 10.0},
        {"direction": "long", "pnl_eur": -4.0},
        {"direction": "short", "pnl_eur": 7.0},
    ]
    d = engine._direction_split(trades)
    assert d["n_long"] == 2 and d["n_short"] == 1
    assert d["pnl_long"] == 6.0 and d["pnl_short"] == 7.0


def test_run_backtest_result_has_direction_split(monkeypatch):
    df = _mixed_df()
    # Download umgehen: zwei Ticker mit denselben synthetischen Daten.
    monkeypatch.setattr(engine, "_download_daily", lambda tickers, years: {"AAA": df, "BBB": df})
    res = engine.run_backtest("standard", tickers=["AAA", "BBB"], years=4, allow_short=True)
    assert res["allow_short"] is True
    assert "direction_split" in res and res["direction_split"]["n_short"] > 0
