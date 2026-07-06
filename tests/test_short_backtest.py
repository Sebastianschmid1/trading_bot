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


# ── Portfolio-Pfad (Report-Pipeline): gather_fires / simulate_portfolio / backtest_portfolio ──

def _data_and_start(df):
    """Hilfsfunktion: {ticker: df} + start_after (ab Warmup) für die Portfolio-Funktionen."""
    start_after = df.index[engine.WARMUP_BARS]
    return {"AAA": df, "BBB": df}, start_after


def test_gather_fires_long_only_has_no_short_fires():
    df = _mixed_df()
    data, start_after = _data_and_start(df)
    by_date = engine.gather_fires(S.get("standard"), data, start_after, allow_short=False)
    dirs = {f["direction"] for fires in by_date.values() for f in fires}
    assert dirs and dirs <= {"long"}            # ohne Flag keine Shorts


def test_gather_fires_allow_short_collects_short_fires():
    df = _mixed_df()
    data, start_after = _data_and_start(df)
    by_date = engine.gather_fires(S.get("standard"), data, start_after, allow_short=True)
    dirs = {f["direction"] for fires in by_date.values() for f in fires}
    assert "short" in dirs                       # mit Flag tauchen Shorts auf


def test_walk_exit_short_mirrors_stop_and_target():
    """Short-Ausstieg: ein über den SL steigender Kurs stoppt aus; ein unter den TP fallender
    Kurs nimmt Gewinn mit — gespiegelt zum Long."""
    idx = pd.bdate_range("2020-01-01", periods=10)
    # Kurs steigt zuerst (löst Short-SL aus): SL über Einstieg muss greifen.
    up = pd.DataFrame({"Open": 100.0, "High": [100, 101, 109, 109, 109, 109, 109, 109, 109, 109],
                       "Low": 99.0, "Close": 100.0, "Volume": 1e6}, index=idx)
    j, price, reason = engine._walk_exit(up, 0, sl=108.0, tp=90.0, leverage=1.0, direction="short")
    assert reason == "Stop-Loss" and price == 108.0

    down = pd.DataFrame({"Open": 100.0, "High": 101.0,
                         "Low": [99, 99, 89, 89, 89, 89, 89, 89, 89, 89],
                         "Close": 100.0, "Volume": 1e6}, index=idx)
    j, price, reason = engine._walk_exit(down, 0, sl=112.0, tp=90.0, leverage=1.0, direction="short")
    assert reason == "Take-Profit" and price == 90.0


def test_walk_exit_trailing_stop_secures_long_profit():
    idx = pd.bdate_range("2020-01-01", periods=8)
    df = pd.DataFrame({
        "Open":  [100, 104, 110, 115, 116, 112, 111, 111],
        "High":  [101, 106, 112, 118, 118, 113, 112, 112],
        "Low":   [ 99, 103, 109, 114, 113, 110, 109, 109],
        "Close": [100, 105, 111, 116, 114, 111, 110, 110],
        "Volume": 1e6,
    }, index=idx)

    j, price, reason = engine._walk_exit(df, 0, sl=95.0, tp=200.0, leverage=1.0,
                                         direction="long", trail_mode="atr", trail_mult=1.0)

    assert reason == "Trailing-Stop"
    assert price > 100.0
    assert j == 6


def test_backtest_ticker_trailing_mode_keeps_fix_mode_unchanged(monkeypatch):
    idx = pd.bdate_range("2020-01-01", periods=12)
    close = [100, 100, 100, 104, 110, 115, 116, 112, 111, 111, 111, 111]
    df = pd.DataFrame({
        "Open": close,
        "High": [c + 2 for c in close],
        "Low": [c - 2 for c in close],
        "Close": close,
        "Volume": 1e6,
    }, index=idx)

    class OneShot:
        key = "one"
        label = "One"
        def __init__(self):
            self.calls = 0
        def generate(self, ticker, tf_data):
            self.calls += 1
            if self.calls == 1:
                return {"ticker": ticker, "direction": "long", "price": 100.0,
                        "stop_loss": 95.0, "take_profit": 200.0}
            return None

    orig = engine.WARMUP_BARS
    monkeypatch.setattr(engine, "WARMUP_BARS", 2)
    fixed = engine.backtest_ticker(OneShot(), "T", df, 1000.0, max_hold=5, warmup=2)
    trailing = engine.backtest_ticker(OneShot(), "T", df, 1000.0, max_hold=5, warmup=2,
                                      trail_mode="atr", trail_mult=1.0)
    monkeypatch.setattr(engine, "WARMUP_BARS", orig)

    assert fixed[0]["reason"] == "Zeitlimit"
    assert trailing[0]["reason"] == "Trailing-Stop"
    assert trailing[0]["exit"] > trailing[0]["entry"]


def test_simulate_portfolio_short_pnl_sign():
    """Über die Feuer-Events der gemischten Reihe entstehen Shorts; deren P&L hat das
    korrekte Vorzeichen (Ausstieg unter Einstieg = Gewinn)."""
    df = _mixed_df()
    data, start_after = _data_and_start(df)
    by_date = engine.gather_fires(S.get("standard"), data, start_after, allow_short=True)
    trades = engine.simulate_portfolio(data, by_date, top_n=10, leverage=1.0,
                                       trade_size=1000.0, max_concurrent=10, max_hold=40)
    shorts = [t for t in trades if t["direction"] == "short"]
    assert shorts
    for t in shorts:
        if t["exit"] != t["entry"]:
            assert (t["exit"] < t["entry"]) == (t["pnl_eur"] > 0)


def test_backtest_portfolio_direction_split(monkeypatch):
    df = _mixed_df()
    monkeypatch.setattr(engine, "_download_daily", lambda tickers, years: {"AAA": df, "BBB": df})
    res = engine.backtest_portfolio("standard", tickers=["AAA", "BBB"], years=4,
                                    top_n=5, leverage=1.0, allow_short=True)
    assert res["allow_short"] is True
    assert res["direction_split"]["n_short"] > 0
