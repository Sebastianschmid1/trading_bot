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
from stockbot.backtest.clock import BarClock


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


def test_registry_has_exact_v1_production_classification():
    assert len(strategies.REGISTRY) == 16
    production = {s.key: s.family for s in strategies.production_strategies()}
    assert production == {
        "standard": "intraday_momentum",
        "ai_adaptive": "swing_trend",
        "bb_revert": "mean_reversion",
    }
    assert all(s.family in {
        "intraday_momentum", "swing_trend", "mean_reversion", "research_only",
    } for s in strategies.all_strategies())
    assert all(s.family == "research_only" and not s.production
               for s in strategies.all_strategies() if s.key not in production)
    assert strategies.is_selectable_for_new_users("standard") is True
    assert strategies.is_selectable_for_new_users("supertrend") is False
    assert strategies.is_selectable_for_new_users("does_not_exist") is False


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
    assert found["raw_score"] == found["strength"]
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

    # cost_pct=0: dieser Test prüft die reine SL/TP-Mechanik ohne Transaktionskosten.
    trades = backtest.backtest_ticker(_dummy_long(), "X", df, trade_size=1000.0,
                                      max_hold=10, warmup=2, cost_pct=0.0)
    assert len(trades) >= 2
    assert trades[0]["reason"] == "Take-Profit" and trades[0]["pnl_eur"] > 0
    assert trades[1]["reason"] == "Stop-Loss" and trades[1]["pnl_eur"] < 0
    assert round(trades[0]["pnl_pct"], 1) == 5.0
    assert round(trades[1]["pnl_pct"], 1) == -2.0


def test_backtest_engine_applies_transaction_costs():
    """Round-Trip-Kosten (Spread cost_pct % je Seite + konservative Slippage) mindern P&L in
    % und €. Der Default folgt der Config und ist NICHT mehr slippage-blind."""
    from stockbot.backtest.cost_model import CostModel
    frac = CostModel().slippage_spread_fraction        # Default-Slippage als Bruchteil des Spreads
    n = 15
    close = np.full(n, 100.0)
    high = close + 0.5
    low = close - 0.5
    high[3] = 106.0
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    df = pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close,
                       "Volume": np.full(n, 1e6)}, index=idx)

    gross = backtest.backtest_ticker(_dummy_long(), "X", df, trade_size=1000.0,
                                     max_hold=10, warmup=2, cost_pct=0.0)
    net = backtest.backtest_ticker(_dummy_long(), "X", df, trade_size=1000.0,
                                   max_hold=10, warmup=2, cost_pct=0.1)
    exp_pct = 2 * 0.1 * (1 + frac)                      # Spread beidseitig + Slippage darauf
    assert round(gross[0]["pnl_pct"] - net[0]["pnl_pct"], 4) == round(exp_pct, 4)
    assert round(gross[0]["pnl_eur"] - net[0]["pnl_eur"], 4) == round(1000.0 * exp_pct / 100.0, 4)
    # Default (None) folgt der Config und ist teurer als kostenlos (Spread + Slippage).
    default = backtest.backtest_ticker(_dummy_long(), "X", df, trade_size=1000.0,
                                       max_hold=10, warmup=2)
    from stockbot import config as _cfg
    assert round(gross[0]["pnl_pct"] - default[0]["pnl_pct"], 4) == \
        round(2 * _cfg.BACKTEST_COST_PCT * (1 + frac), 4)


def _provider_bars():
    n = backtest.WARMUP_BARS + 12
    close = np.full(n, 100.0)
    high = close + 0.5
    low = close - 0.5
    high[backtest.WARMUP_BARS + 1] = 106.0
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close,
                         "Volume": np.full(n, 1e6)}, index=idx)


def test_run_backtest_uses_injected_provider_and_preserves_result(monkeypatch):
    """Provider-Seam ist offline nutzbar; dieselben Bars ergeben dasselbe Resultat wie `data`."""
    df = _provider_bars()

    class FakeProvider:
        def __init__(self):
            self.calls = []

        def get_bars(self, ticker, **kwargs):
            self.calls.append((ticker, kwargs))
            return df.copy()

    provider = FakeProvider()
    strategy = _dummy_long()
    monkeypatch.setattr(backtest.strat_mod, "get", lambda key: strategy)

    via_provider = backtest.run_backtest(
        "dummy", tickers=["X"], years=2, data_provider=provider, jobs=1, cost_pct=0.0)
    via_data = backtest.run_backtest(
        "dummy", tickers=["X"], years=2, data={"X": df.copy()}, jobs=1, cost_pct=0.0)

    assert provider.calls == [("X", {"period": "3y", "interval": "1d"})]
    assert via_provider == via_data


def test_clock_tracks_current_bar_in_decision_path():
    df = _provider_bars()
    clock = BarClock()
    seen = []

    def generate(ticker, tf_data):
        seen.append(clock.now())
        return None

    strategy = strategies.Strategy("clock", "Clock", generate)
    backtest.backtest_ticker(strategy, "X", df, 1000.0, clock=clock, cost_pct=0.0)

    assert seen
    assert seen[0] == df.index[backtest.WARMUP_BARS].to_pydatetime()


def test_bar_clock_preserves_aware_daily_bar_timezone():
    bar_time = pd.Timestamp("2023-01-03 16:00:00", tz="America/New_York")
    clock = BarClock()
    clock.advance_to(bar_time)

    assert pd.Timestamp(clock.now()).tz == bar_time.tz


def test_backtest_decision_ignores_future_bar_perturbation():
    """Die Signalentscheidung für t erhält höchstens die Bars bis einschließlich t."""
    n, warmup, decision_idx = 10, 2, 4
    close = np.full(n, 100.0)
    df = _series(close)
    perturbed = df.copy()
    perturbed.loc[perturbed.index[decision_idx + 1]:, ["Open", "High", "Low", "Close"]] = 1_000.0
    seen = []

    def generate(ticker, tf_data):
        bars = tf_data["1d"]
        if bars.index[-1] == df.index[decision_idx]:
            seen.append((len(bars), float(bars["Close"].iloc[-1])))
            price = float(bars["Close"].iloc[-1])
            return {"ticker": ticker, "direction": "long", "price": price,
                    "stop_loss": price * 0.98, "take_profit": price * 1.05}
        return None

    strategy = strategies.Strategy("future_safe", "Future Safe", generate)
    baseline = backtest.backtest_ticker(strategy, "X", df, 1000.0, warmup=warmup, cost_pct=0.0)
    altered = backtest.backtest_ticker(strategy, "X", perturbed, 1000.0, warmup=warmup, cost_pct=0.0)

    assert seen == [(decision_idx + 1, 100.0), (decision_idx + 1, 100.0)]
    assert baseline[0]["entry"] == altered[0]["entry"] == 100.0
    assert baseline[0]["entry_date"] == altered[0]["entry_date"] == str(df.index[decision_idx].date())


def test_backtest_never_decides_or_enters_on_last_potentially_open_daily_bar():
    n, warmup = 8, 2
    df = _series(np.full(n, 100.0))
    clock = BarClock()
    decisions = []

    def generate(ticker, tf_data):
        decisions.append((tf_data["1d"].index[-1], clock.now()))
        if tf_data["1d"].index[-1] != df.index[-2]:
            return None
        price = float(tf_data["1d"]["Close"].iloc[-1])
        return {"ticker": ticker, "direction": "long", "price": price,
                "stop_loss": price * 0.98, "take_profit": price * 1.05}

    strategy = strategies.Strategy("closed_bars", "Closed Bars", generate)
    trades = backtest.backtest_ticker(strategy, "X", df, 1000.0, warmup=warmup,
                                      max_hold=1, clock=clock, cost_pct=0.0)

    last_bar = df.index[-1]
    assert decisions[-1][0] == df.index[-2]
    assert all(bar_time < last_bar and clock_time == bar_time.to_pydatetime()
               for bar_time, clock_time in decisions)
    assert all(trade["entry_date"] != str(last_bar.date()) for trade in trades)


def test_generate_uses_shared_strategy_and_analyzer_paths(monkeypatch):
    """Der Backtest delegiert Signale an dieselben Module wie der produktive Strategiepfad."""
    calls = []
    strategy = strategies.Strategy(
        "standard", "Standard", lambda ticker, data: calls.append("strategy") or {"ok": True})
    assert backtest._generate(strategy, "X", {"1d": pd.DataFrame()}, False) == {"ok": True}

    monkeypatch.setattr(
        backtest.analyzer, "analyze_ticker",
        lambda ticker, data, allow_short: calls.append(("analyzer", allow_short)) or {"ok": True})
    signal = backtest._generate(strategy, "X", {"1d": pd.DataFrame()}, True)

    assert calls == ["strategy", ("analyzer", True)]
    assert signal == {"ok": True, "strategy": "standard"}


# ── Survivorship-Bias: Point-in-Time-Universum (Part A) ──────────────────────

def test_resolve_universe_uses_historical_members_when_pit_data_present():
    """Mit vorhandener Point-in-Time-Historie zieht ein vergangener Backtest die DAMALIGE
    Mitgliederliste — inkl. seither entfernter Titel, ohne Survivorship-Warnung."""
    from stockbot.backtest.universe_history import PointInTimeUniverse, UniverseSnapshot
    pit = PointInTimeUniverse([
        UniverseSnapshot("2016-01-01", ["AAA", "OLD1", "OLD2"]),
        UniverseSnapshot("2024-01-01", ["AAA", "NEW1"]),
    ])
    tickers, warn = backtest.resolve_backtest_universe(5, pit_universe=pit, as_of="2017-06-30")
    assert tickers == ["AAA", "OLD1", "OLD2"]
    assert warn is None


def test_resolve_universe_degrades_honestly_without_pit_data(monkeypatch):
    """Ohne Historie NICHT still die heutige Liste nehmen, sondern degradieren + warnen."""
    monkeypatch.setattr(backtest, "_load_point_in_time_universe", lambda region: None)
    monkeypatch.setattr(backtest.universes, "get_tickers",
                        lambda region, auto=False: ["TODAY1", "TODAY2"])
    tickers, warn = backtest.resolve_backtest_universe(5, as_of="2017-06-30")
    assert tickers == ["TODAY1", "TODAY2"]
    assert warn == backtest.SURVIVORSHIP_WARNING


def test_resolve_universe_degrades_when_date_before_history(monkeypatch):
    """Deckt die Historie den Stichtag nicht ab (Datum vor erster Aufnahme) → ehrliche Warnung."""
    from stockbot.backtest.universe_history import PointInTimeUniverse, UniverseSnapshot
    pit = PointInTimeUniverse([UniverseSnapshot("2020-01-01", ["A", "B"])])
    monkeypatch.setattr(backtest.universes, "get_tickers",
                        lambda region, auto=False: ["TODAY1"])
    tickers, warn = backtest.resolve_backtest_universe(5, pit_universe=pit, as_of="2015-06-30")
    assert tickers == ["TODAY1"]
    assert warn == backtest.SURVIVORSHIP_WARNING


def test_resolve_universe_keeps_explicit_tickers_without_bias_claim():
    tickers, warn = backtest.resolve_backtest_universe(5, tickers=["X", "Y"])
    assert tickers == ["X", "Y"] and warn is None


def test_run_backtest_surfaces_survivorship_warning(monkeypatch):
    """Die Degradations-Warnung landet im Ergebnis (statt still verschwiegen zu werden)."""
    df = _provider_bars()
    strategy = _dummy_long()
    monkeypatch.setattr(backtest.strat_mod, "get", lambda key: strategy)
    monkeypatch.setattr(backtest, "_load_point_in_time_universe", lambda region: None)
    monkeypatch.setattr(backtest.universes, "get_tickers", lambda region, auto=False: ["X"])
    monkeypatch.setattr(backtest, "_download_daily",
                        lambda tickers, years, *a, **k: {"X": df.copy()})
    res = backtest.run_backtest("dummy", years=2, jobs=1, cost_pct=0.0)
    assert res["universe_warning"] == backtest.SURVIVORSHIP_WARNING
    # Bei explizit vorgegebenen Tickern gibt es keine Bias-Aussage.
    res2 = backtest.run_backtest("dummy", tickers=["X"], data={"X": df.copy()}, jobs=1, cost_pct=0.0)
    assert res2["universe_warning"] is None


# ── Gap-blinde Exits (Part B): Fill am Open statt am Level ────────────────────

def _walk_df(rows):
    idx = pd.bdate_range("2020-01-01", periods=len(rows["Open"]))
    return pd.DataFrame({**rows, "Volume": 1e6}, index=idx)


def test_walk_exit_long_gap_below_stop_fills_at_open():
    """Öffnet die Bar per Gap UNTER dem Stop, füllt der reale Fill am (schlechteren) Open."""
    df = _walk_df({"Open": [100, 92, 100, 100], "High": [101, 93, 101, 101],
                   "Low": [99, 90, 99, 99], "Close": [100, 92, 100, 100]})
    j, price, reason = backtest._walk_exit(df, 0, sl=95.0, tp=200.0, leverage=1.0, direction="long")
    assert reason == "Stop-Loss" and j == 1
    assert price == 92.0            # Open, NICHT das SL-Level 95


def test_walk_exit_long_non_gap_fills_at_level():
    """Öffnet die Bar diesseits und berührt den Stop erst intrabar → Fill am Level (Abgrenzung)."""
    df = _walk_df({"Open": [100, 99, 100, 100], "High": [101, 100, 101, 101],
                   "Low": [99, 94, 99, 99], "Close": [100, 99, 100, 100]})
    j, price, reason = backtest._walk_exit(df, 0, sl=95.0, tp=200.0, leverage=1.0, direction="long")
    assert reason == "Stop-Loss" and j == 1
    assert price == 95.0            # intrabar am SL-Level


def test_walk_exit_short_gap_above_stop_fills_at_open():
    """Short-Analogon: Gap ÜBER den (oben liegenden) Short-Stop → Fill am schlechteren Open."""
    df = _walk_df({"Open": [100, 108, 100, 100], "High": [101, 110, 101, 101],
                   "Low": [99, 107, 99, 99], "Close": [100, 108, 100, 100]})
    j, price, reason = backtest._walk_exit(df, 0, sl=105.0, tp=90.0, leverage=1.0, direction="short")
    assert reason == "Stop-Loss" and j == 1
    assert price == 108.0           # Open, NICHT das SL-Level 105


def test_walk_exit_short_non_gap_fills_at_level():
    df = _walk_df({"Open": [100, 101, 100, 100], "High": [101, 106, 101, 101],
                   "Low": [99, 100, 99, 99], "Close": [100, 101, 100, 100]})
    j, price, reason = backtest._walk_exit(df, 0, sl=105.0, tp=90.0, leverage=1.0, direction="short")
    assert reason == "Stop-Loss" and j == 1
    assert price == 105.0           # intrabar am SL-Level


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
