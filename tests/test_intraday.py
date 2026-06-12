"""
Tests für Intraday-Features: Multi-Timeframe-Stärke (0-100), Auto-Close-Logik,
Tick-Verlauf und die Dashboard-Intraday-Daten. Offline (yfinance gemockt).

Lauf:  python test_intraday.py   oder   pytest test_intraday.py
"""

import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from stockbot.core import db
from stockbot.market import analyzer
from stockbot.tgbot import bot
from stockbot.web import dashboard

CHAT = 7373


def fresh_db():
    d = tempfile.mkdtemp(prefix="intratest_")
    db.DB_FILE = Path(d) / "test.db"
    db.init_db()


class _FakeFastInfo:
    def __init__(self, p): self.last_price = p
class _FakeTicker:
    def __init__(self, p): self._p = p
    @property
    def fast_info(self): return _FakeFastInfo(self._p)
class _FakeYF:
    def __init__(self, p): self._p = p
    def Ticker(self, t): return _FakeTicker(self._p)


# ── Multi-Timeframe-Stärke ───────────────────────────────────────────────────

def test_timeframe_score_bullish_beats_bearish():
    n = 80
    up   = (100 + np.arange(n) * 0.5).astype(float)     # Aufwärtstrend
    down = (140 - np.arange(n) * 0.5).astype(float)     # Abwärtstrend
    vol  = np.full(n, 1_000_000.0)
    s_up   = analyzer.compute_timeframe_score(up, vol)
    s_down = analyzer.compute_timeframe_score(down, vol)
    assert s_up is not None and s_down is not None
    assert 0 <= s_down <= s_up <= 100
    assert s_up > s_down


def test_timeframe_score_too_few_bars_returns_none():
    assert analyzer.compute_timeframe_score(np.arange(30, dtype=float), np.ones(30)) is None


def test_compute_strength_weighted_average():
    # Gewichte aus config: 5m .40 / 15m .30 / 1h .20 / 1d .10
    sc = {"5m": 80.0, "15m": 60.0, "1h": 40.0, "1d": 20.0}
    assert analyzer.compute_strength(sc) == 60.0          # 32+18+8+2
    # fehlende TF → über die vorhandenen normiert
    assert analyzer.compute_strength({"5m": 80.0, "15m": None, "1h": None, "1d": None}) == 80.0
    assert analyzer.compute_strength({"5m": None, "15m": None, "1h": None, "1d": None}) == 0.0


# ── Auto-Close-Logik (Punkt 4) ───────────────────────────────────────────────

def _trade(sl=95.0, tp=108.0):
    return {"ticker": "NVDA", "direction": "long", "entry": 100.0,
            "signal": {"stop_loss": sl, "take_profit": tp}}


def test_auto_close_stop_loss():
    assert "Stop-Loss" in bot.evaluate_active_trade(_trade(), price=94.0, strength=70.0)


def test_auto_close_take_profit():
    assert "Take-Profit" in bot.evaluate_active_trade(_trade(), price=109.0, strength=70.0)


def test_auto_close_weak_signal():
    assert "verschlechtert" in bot.evaluate_active_trade(_trade(), price=101.0, strength=30.0)


def test_auto_close_holds_when_fine():
    assert bot.evaluate_active_trade(_trade(), price=101.0, strength=70.0) is None


# ── Tick-Verlauf in der DB ───────────────────────────────────────────────────

def test_add_and_get_today_ticks():
    fresh_db()
    db.add_tick(CHAT, "AAPL", 100.0, 60.0)
    db.add_tick(CHAT, "AAPL", 101.0, 62.0)
    db.add_tick(CHAT, "MSFT", 400.0, 55.0)
    series = db.get_today_ticks(CHAT)
    assert set(series.keys()) == {"AAPL", "MSFT"}
    assert len(series["AAPL"]) == 2
    assert series["AAPL"][0]["price"] == 100.0 and series["AAPL"][1]["strength"] == 62.0


# ── Dashboard liefert Intraday-Verlauf ───────────────────────────────────────

def test_dashboard_includes_intraday():
    fresh_db()
    db.yf = _FakeYF(101.0)
    db.get_or_create_user(CHAT, "tester")
    db.save_profile(CHAT, trade_size_eur=25.0)
    db.add_pending(CHAT, {"ticker": "AAPL", "direction": "long", "price": 100.0,
                          "stop_loss": 97.0, "take_profit": 105.0}, 1)
    db.activate_trade(CHAT, "AAPL")
    db.add_tick(CHAT, "AAPL", 101.0, 70.0)
    db.add_tick(CHAT, "AAPL", 102.0, 72.0)

    data = dashboard.build_dashboard_data(db.get_user(CHAT))
    series = [s for s in data["intraday"] if s["ticker"] == "AAPL"]
    assert len(series) == 1
    assert len(series[0]["points"]) == 2
    assert series[0]["take_profit"] == 105.0


def test_dashboard_filters_by_strategy():
    fresh_db()
    db.yf = _FakeYF(101.0)
    db.get_or_create_user(CHAT, "tester")
    db.save_profile(CHAT, trade_size_eur=25.0)
    db.toggle_strategy(CHAT, "adx_trend")   # standard + adx_trend aktiv
    # zwei aktive Trades, je eine Strategie
    db.add_pending(CHAT, {"ticker": "AAPL", "direction": "long", "price": 100.0,
                          "stop_loss": 97.0, "take_profit": 105.0, "strategy": "standard"}, 1)
    db.activate_trade(CHAT, "AAPL")
    db.add_pending(CHAT, {"ticker": "MSFT", "direction": "long", "price": 200.0,
                          "stop_loss": 195.0, "take_profit": 210.0, "strategy": "adx_trend"}, 2)
    db.activate_trade(CHAT, "MSFT")
    user = db.get_user(CHAT)

    all_view = dashboard.build_dashboard_data(user)
    assert {t["ticker"] for t in all_view["active_trades"]} == {"AAPL", "MSFT"}
    # Tabs: „Alle" + 2 Strategien
    assert [t["key"] for t in all_view["strategies"]] == ["", "standard", "adx_trend"]

    adx_view = dashboard.build_dashboard_data(user, strategy="adx_trend")
    assert {t["ticker"] for t in adx_view["active_trades"]} == {"MSFT"}   # nur ADX-Trade
    assert adx_view["strategy"] == "adx_trend"


def test_dashboard_days_filter():
    fresh_db()
    db.get_or_create_user(CHAT, "tester")
    db.save_profile(CHAT, trade_size_eur=25.0)
    sj = '{"strategy": "standard"}'
    with db._connect() as conn:
        for off, pnl in [(0, 10.0), (20, -5.0)]:    # heute & vor 20 Tagen
            conn.execute(
                "INSERT INTO trades (user_id, trade_date, ticker, direction, signal_json, status, "
                "entry, exit, pnl_eur, pnl_pct) VALUES (?, date('now', ?), ?, 'long', ?, 'closed', 100, 110, ?, 10)",
                (CHAT, f"-{off} day", f"T{off}", sj, pnl),
            )
    user = db.get_user(CHAT)
    assert dashboard.build_dashboard_data(user)["summary"]["total_closed"] == 2          # alle
    last7 = dashboard.build_dashboard_data(user, days=7)
    assert last7["summary"]["total_closed"] == 1 and last7["summary"]["total_pnl"] == 10.0
    assert last7["days"] == 7 and 30 in last7["ranges"]


def test_dashboard_trades_curves_history_and_active():
    fresh_db()
    db.yf = _FakeYF(100.0)                        # Einstiegskurs des offenen Trades = 100
    db.get_or_create_user(CHAT, "tester")
    db.save_profile(CHAT, trade_size_eur=25.0)
    # ein abgeschlossener (historischer) Trade → Linie Einstieg(0%)→Ausstieg(+10%)
    with db._connect() as conn:
        conn.execute(
            "INSERT INTO trades (user_id, trade_date, ticker, direction, signal_json, status, "
            "entry, exit, pnl_eur, pnl_pct) VALUES (?, date('now','-3 day'), 'MSFT', 'long', "
            "'{\"strategy\": \"standard\"}', 'closed', 200, 220, 5.0, 10)",
            (CHAT,),
        )
    # ein offener (aktueller) Trade mit zwei Intraday-Ticks
    db.add_pending(CHAT, {"ticker": "AAPL", "direction": "long", "price": 100.0,
                          "stop_loss": 97.0, "take_profit": 130.0, "strategy": "standard"}, 1)
    db.activate_trade(CHAT, "AAPL")
    db.add_tick(CHAT, "AAPL", 105.0, 70.0)       # +5 %
    db.add_tick(CHAT, "AAPL", 110.0, 72.0)       # +10 %

    curves = dashboard.build_dashboard_data(db.get_user(CHAT))["trades_curves"]
    by_ticker = {x["ticker"]: x for x in curves}

    msft = by_ticker["MSFT"]
    assert msft["status"] == "closed" and msft["final_pct"] == 10
    assert msft["points"] == [{"x": 0.0, "y": 0.0}, {"x": 100.0, "y": 10}]   # Einstieg→Ausstieg

    aapl = by_ticker["AAPL"]
    assert aapl["status"] == "active"
    assert aapl["points"][0] == {"x": 0.0, "y": 0.0}        # startet am Einstieg (0 %)
    assert aapl["points"][-1]["y"] == 10.0                  # letzter Tick = +10 % ab Einstieg
    # historische zuerst, offene danach
    assert curves[0]["ticker"] == "MSFT" and curves[-1]["ticker"] == "AAPL"


# ── Detail-Analyse: Faktor-Verlauf einer Aktie (7 Tage) ──────────────────────

def test_factor_history_returns_factor_timeseries():
    from stockbot.market import analyzer
    n = 140
    close = 100 + np.arange(n) * 0.2 + 1.5 * np.sin(np.arange(n) / 5.0)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    df = pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1,
                       "Close": close, "Volume": np.full(n, 1_000_000.0)}, index=idx)

    class _FakeDL:
        @staticmethod
        def download(*a, **k):
            return df

    orig = analyzer.yf
    analyzer.yf = _FakeDL
    try:
        res = analyzer.factor_history("AAPL", days=7)
    finally:
        analyzer.yf = orig

    assert res["ticker"] == "AAPL"
    assert 1 <= len(res["points"]) <= 7
    p = res["points"][-1]
    for k in ("date", "price", "rsi", "macd_score", "trend_score", "vol_score", "score"):
        assert k in p
    assert 0 <= p["score"] <= 100
    assert 0 <= p["rsi_score"] <= 100


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"❌ {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"💥 {name}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
