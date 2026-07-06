"""
Tests für Intraday-Features: Multi-Timeframe-Stärke (0-100), Auto-Close-Logik,
Tick-Verlauf und die Dashboard-Intraday-Daten. Offline (yfinance gemockt).

Lauf:  python test_intraday.py   oder   pytest test_intraday.py
"""

import sys
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from stockbot.core import db
from stockbot.market import analyzer
from stockbot.tgbot import bot
from stockbot.web import dashboard
from stockbot.broker import reconcile as reconcile_mod

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


# ── Auto-Monitoring: Intervall + richtige Strategie je Trade ─────────────────

def test_register_jobs_schedules_monitor_every_interval():
    from unittest.mock import MagicMock
    from stockbot import config
    app = MagicMock()
    bot._register_jobs(app)
    jq = app.job_queue
    # zwei wiederkehrende Jobs: Intraday-Signal-Scan + Trade-Monitor
    assert jq.run_repeating.call_count == 2
    by_name = {c.kwargs.get("name"): c for c in jq.run_repeating.call_args_list}
    assert by_name["monitor_trades"].args[0] is bot.monitor_trades
    assert by_name["monitor_trades"].kwargs["interval"] == config.MONITOR_INTERVAL_SEC
    assert by_name["intraday_signals"].args[0] is bot.scan_intraday
    assert by_name["intraday_signals"].kwargs["interval"] == config.INTRADAY_SCAN_INTERVAL_SEC
    assert jq.run_daily.call_count == 4                        # Signale, Auswertung, Smart-Money, Labor
    daily_by_name = {c.kwargs.get("name"): c for c in jq.run_daily.call_args_list}
    assert daily_by_name["weekly_lab_optimization"].args[0] is bot.run_weekly_lab_optimization


def test_bot_broker_order_blocks_when_buying_power_is_insufficient():
    import asyncio
    from unittest.mock import AsyncMock
    fresh_db()
    db.yf = _FakeYF(827.51)
    db.get_or_create_user(CHAT, "brokerbp")
    db.save_profile(CHAT, trade_size_eur=1000.0)
    db.set_broker_exec(CHAT, True)
    db.add_pending(CHAT, {"ticker": "TSLA", "direction": "long", "price": 827.51,
                          "leverage": 1.0, "strength": 70.0}, 1)
    db.activate_trade(CHAT, "TSLA")
    trade = db.get_trade(CHAT, "TSLA")
    assert trade is not None
    submitted = {"called": False}

    orig_ready, orig_client = bot._alpaca_ready, bot._alpaca_client
    orig_market_open = bot._us_market_open
    orig_plan, orig_summary, orig_submit = bot.sizing.plan_order, bot.broker.account_summary, bot.broker.submit_buy
    bot._alpaca_ready = lambda user: True
    bot._alpaca_client = lambda user: object()
    bot._us_market_open = lambda extended=False: True
    bot.sizing.plan_order = lambda entry, budget, leverage, option_selector=None, extended=False, **kwargs: {"kind": "shares", "qty": 1, "notional": None}
    bot.broker.account_summary = lambda client=None: {"ok": True, "buying_power": 0.0}
    def fake_submit(*args, **kwargs):
        submitted["called"] = True
        return {"ok": True, "id": "should-not-submit"}
    bot.broker.submit_buy = fake_submit
    fake_bot = AsyncMock()
    try:
        asyncio.run(bot._maybe_broker_order(fake_bot, CHAT, trade))
    finally:
        bot._alpaca_ready, bot._alpaca_client = orig_ready, orig_client
        bot._us_market_open = orig_market_open
        bot.sizing.plan_order, bot.broker.account_summary, bot.broker.submit_buy = orig_plan, orig_summary, orig_submit

    assert submitted["called"] is False
    trade = db.get_trade(CHAT, "TSLA")
    assert trade is not None
    assert trade["status"] == "broker_failed"
    assert trade["broker_status"] == "insufficient_buying_power"
    assert "Buying Power reicht nicht" in fake_bot.send_message.await_args.kwargs["text"]


def test_monitor_promotes_filled_broker_pending_trade():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    fresh_db()
    db.yf = _FakeYF(100.0)
    db.get_or_create_user(CHAT, "tester")
    db.save_profile(CHAT, trade_size_eur=25.0)
    db.set_broker_exec(CHAT, True)
    db.add_pending(CHAT, {"ticker": "NVDA", "direction": "long", "price": 100.0,
                          "leverage": 1.0, "strength": 80}, 1)
    db.activate_trade(CHAT, "NVDA", status="broker_pending")
    db.mark_broker_pending(CHAT, "NVDA", order_id="ord-1", broker_status="accepted")

    orig_ready, orig_client, orig_status = bot._alpaca_ready, bot._alpaca_client, bot.broker.get_order_status
    bot._alpaca_ready = lambda user: True
    bot._alpaca_client = lambda user: object()
    bot.broker.get_order_status = lambda order_id, client=None: {
        "ok": True, "status": "filled", "filled_qty": 1.0, "filled_avg_price": 101.5}
    ctx = MagicMock(); ctx.bot = AsyncMock(); ctx.job_queue = MagicMock()
    try:
        asyncio.run(bot.monitor_broker_pending(ctx.bot))
    finally:
        bot._alpaca_ready, bot._alpaca_client, bot.broker.get_order_status = orig_ready, orig_client, orig_status

    trade = db.get_trade(CHAT, "NVDA")
    assert trade["status"] == "active"
    assert trade["entry"] == 101.5
    assert trade["broker_status"] == "filled"
    ctx.bot.send_message.assert_awaited()


def test_monitor_marks_canceled_broker_pending_trade_failed():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    fresh_db()
    db.yf = _FakeYF(100.0)
    db.get_or_create_user(CHAT, "tester")
    db.save_profile(CHAT, trade_size_eur=25.0)
    db.set_broker_exec(CHAT, True)
    db.add_pending(CHAT, {"ticker": "AAPL", "direction": "long", "price": 100.0,
                          "leverage": 1.0, "strength": 70.0}, 1)
    db.activate_trade(CHAT, "AAPL", status="broker_pending")
    db.mark_broker_pending(CHAT, "AAPL", order_id="ord-1", broker_status="accepted")
    orig_ready, orig_client, orig_status = bot._alpaca_ready, bot._alpaca_client, bot.broker.get_order_status
    bot._alpaca_ready = lambda user: True
    bot._alpaca_client = lambda user: object()
    bot.broker.get_order_status = lambda order_id, client=None: {"ok": True, "status": "canceled"}
    ctx = MagicMock(); ctx.bot = AsyncMock(); ctx.job_queue = MagicMock()
    try:
        asyncio.run(bot.monitor_broker_pending(ctx.bot))
    finally:
        bot._alpaca_ready, bot._alpaca_client, bot.broker.get_order_status = orig_ready, orig_client, orig_status

    trade = db.get_trade(CHAT, "AAPL")
    assert trade["status"] == "broker_failed"
    assert trade["broker_status"] == "canceled"
    assert db.get_active_trades(CHAT) == []
    ctx.bot.send_message.assert_awaited()


def test_monitor_promotes_filled_broker_closing_trade():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    fresh_db()
    db.yf = _FakeYF(100.0)
    db.get_or_create_user(CHAT, "tester")
    db.save_profile(CHAT, trade_size_eur=25.0)
    db.set_broker_exec(CHAT, True)
    db.add_pending(CHAT, {"ticker": "MSFT", "direction": "long", "price": 100.0,
                          "leverage": 1.0, "strength": 70.0}, 1)
    db.activate_trade(CHAT, "MSFT")
    db.mark_broker_closing(CHAT, "MSFT", order_id="ord-sell", broker_status="accepted")
    orig_ready, orig_client, orig_status = bot._alpaca_ready, bot._alpaca_client, bot.broker.get_order_status
    bot._alpaca_ready = lambda user: True
    bot._alpaca_client = lambda user: object()
    bot.broker.get_order_status = lambda order_id, client=None: {"ok": True, "status": "filled", "filled_qty": 1.0, "filled_avg_price": 104.0}
    ctx = MagicMock(); ctx.bot = AsyncMock(); ctx.job_queue = MagicMock()
    try:
        asyncio.run(bot.monitor_broker_closing(ctx.bot))
    finally:
        bot._alpaca_ready, bot._alpaca_client, bot.broker.get_order_status = orig_ready, orig_client, orig_status

    trade = db.get_trade(CHAT, "MSFT")
    assert trade["status"] == "closed"
    assert db.get_active_trades(CHAT) == []
    assert ctx.bot.send_message.await_count >= 1


def test_monitor_reverts_failed_broker_closing_trade():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    fresh_db()
    db.yf = _FakeYF(100.0)
    db.get_or_create_user(CHAT, "tester")
    db.save_profile(CHAT, trade_size_eur=25.0)
    db.set_broker_exec(CHAT, True)
    db.add_pending(CHAT, {"ticker": "TSLA", "direction": "long", "price": 100.0,
                          "leverage": 1.0, "strength": 70.0}, 1)
    db.activate_trade(CHAT, "TSLA")
    db.mark_broker_closing(CHAT, "TSLA", order_id="ord-sell2", broker_status="accepted")
    orig_ready, orig_client, orig_status = bot._alpaca_ready, bot._alpaca_client, bot.broker.get_order_status
    bot._alpaca_ready = lambda user: True
    bot._alpaca_client = lambda user: object()
    bot.broker.get_order_status = lambda order_id, client=None: {"ok": True, "status": "canceled"}
    ctx = MagicMock(); ctx.bot = AsyncMock(); ctx.job_queue = MagicMock()
    try:
        asyncio.run(bot.monitor_broker_closing(ctx.bot))
    finally:
        bot._alpaca_ready, bot._alpaca_client, bot.broker.get_order_status = orig_ready, orig_client, orig_status

    trade = db.get_trade(CHAT, "TSLA")
    assert trade["status"] == "active"
    assert trade["broker_status"] == "canceled"
    assert any(t["ticker"] == "TSLA" for t in db.get_active_trades(CHAT))
    ctx.bot.send_message.assert_awaited()


def test_monitor_closes_missing_broker_position_after_grace_period():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    fresh_db()
    db.yf = _FakeYF(100.0)
    db.get_or_create_user(CHAT, "tester")
    db.save_profile(CHAT, trade_size_eur=25.0)
    db.set_broker_exec(CHAT, True)
    db.add_pending(CHAT, {"ticker": "IBM", "direction": "long", "price": 100.0,
                          "leverage": 1.0, "strength": 80.0}, 1)
    db.activate_trade(CHAT, "IBM")
    with db._connect() as conn:
        conn.execute(
            "UPDATE trades SET broker_updated_at = '2026-01-01 00:00:00' WHERE user_id = ? AND ticker = ?",
            (CHAT, "IBM"),
        )

    orig_ready, orig_client = bot._alpaca_ready, bot._alpaca_client
    orig_positions, orig_price = reconcile_mod.broker.list_positions, reconcile_mod.get_current_price
    orig_exists = reconcile_mod.broker.position_exists
    orig_market_open = bot._us_market_open
    bot._alpaca_ready = lambda user: True
    bot._alpaca_client = lambda user: object()
    bot._us_market_open = lambda *a, **k: False
    reconcile_mod.broker.list_positions = lambda client=None: []
    reconcile_mod.broker.position_exists = lambda sym, c=None: False   # Einzelabfrage bestätigt: echt weg
    reconcile_mod.get_current_price = lambda ticker, fallback: 97.5
    ctx = MagicMock(); ctx.bot = AsyncMock(); ctx.job_queue = MagicMock()
    try:
        asyncio.run(bot.monitor_missing_broker_positions(ctx.bot))
    finally:
        bot._alpaca_ready, bot._alpaca_client = orig_ready, orig_client
        bot._us_market_open = orig_market_open
        reconcile_mod.broker.list_positions = orig_positions
        reconcile_mod.broker.position_exists = orig_exists
        reconcile_mod.get_current_price = orig_price

    trade = db.get_trade(CHAT, "IBM")
    assert trade["status"] == "closed"
    assert trade["broker_status"] == "reconciled_missing_position"
    assert trade["exit"] == 97.5
    ctx.bot.send_message.assert_awaited()


def test_monitor_reprices_stale_broker_closing_trade():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    fresh_db()
    db.yf = _FakeYF(100.0)
    db.get_or_create_user(CHAT, "tester")
    db.save_profile(CHAT, trade_size_eur=25.0)
    db.set_broker_exec(CHAT, True)
    db.add_pending(CHAT, {"ticker": "NVDA", "direction": "long", "price": 100.0,
                          "leverage": 1.0, "strength": 70.0}, 1)
    db.activate_trade(CHAT, "NVDA")
    db.mark_broker_closing(CHAT, "NVDA", order_id="ord-old", broker_status="accepted")
    with db._connect() as conn:
        conn.execute("UPDATE trades SET broker_updated_at = '2000-01-01 00:00:00' WHERE user_id = ? AND ticker = ?", (CHAT, "NVDA"))

    calls = {}
    orig_ready, orig_client, orig_status = bot._alpaca_ready, bot._alpaca_client, bot.broker.get_order_status
    orig_cancel, orig_pos, orig_submit, orig_price = bot.broker.cancel_order, bot.broker.get_position, bot.broker.submit_exit_order, bot.get_current_price
    bot._alpaca_ready = lambda user: True
    bot._alpaca_client = lambda user: object()
    bot.broker.get_order_status = lambda order_id, client=None: {"ok": True, "status": "accepted"}
    bot.broker.cancel_order = lambda order_id, client=None: {"ok": True, "canceled": True}
    bot.broker.get_position = lambda symbol, client=None: {"symbol": symbol, "qty": 2.0}
    def _submit_exit_order(symbol, **kwargs):
        calls["symbol"] = symbol
        calls.update(kwargs)
        return {"ok": True, "id": "ord-new", "detail": "repriced"}
    bot.broker.submit_exit_order = _submit_exit_order
    bot.get_current_price = lambda ticker, fallback: 100.0
    ctx = MagicMock(); ctx.bot = AsyncMock(); ctx.job_queue = MagicMock()
    try:
        asyncio.run(bot.monitor_broker_closing(ctx.bot))
    finally:
        bot._alpaca_ready, bot._alpaca_client, bot.broker.get_order_status = orig_ready, orig_client, orig_status
        bot.broker.cancel_order, bot.broker.get_position, bot.broker.submit_exit_order, bot.get_current_price = orig_cancel, orig_pos, orig_submit, orig_price

    trade = db.get_trade(CHAT, "NVDA")
    assert trade["status"] == "broker_closing"
    assert trade["broker_status"] == "repriced"
    assert trade["broker_order_id"] == "ord-new"
    assert calls["symbol"] == "NVDA"
    assert calls["side"] == "SELL"
    assert round(calls["limit_price"], 2) == 99.0
    ctx.bot.send_message.assert_awaited()


def test_monitor_evaluates_each_trade_with_its_own_strategy():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from stockbot.market import strategies
    fresh_db()
    db.yf = _FakeYF(100.0)                       # Einstiegskurs = 100
    db.get_or_create_user(CHAT, "tester")
    db.save_profile(CHAT, trade_size_eur=25.0)
    db.add_pending(CHAT, {"ticker": "NVDA", "direction": "long", "price": 100.0,
                          "stop_loss": 90.0, "take_profit": 130.0,
                          "strategy": "breakout", "strength": 80}, 1)
    db.activate_trade(CHAT, "NVDA")

    captured = {}
    def fake_live_scores(pairs):
        captured["pairs"] = set(pairs)
        # schwacher Strategie-Score (<35) → Auto-Close „Signal verschlechtert"
        return {(t, k): {"price": 101.0, "strength": 10.0} for (t, k) in pairs}

    orig_open, orig_ls = bot._us_market_open, strategies.live_scores
    bot._us_market_open = lambda *a, **k: True
    strategies.live_scores = fake_live_scores
    ctx = MagicMock(); ctx.bot = AsyncMock(); ctx.job_queue = MagicMock()
    try:
        asyncio.run(bot.monitor_trades(ctx))
    finally:
        bot._us_market_open, strategies.live_scores = orig_open, orig_ls

    # Score wurde mit der RICHTIGEN Strategie des Trades angefordert
    assert captured["pairs"] == {("NVDA", "breakout")}
    # mit diesem Strategie-Score (10 < Schwelle 35) wurde der Trade geschlossen
    assert db.get_active_trades(CHAT) == []
    ctx.bot.send_message.assert_awaited()
    # der aufgezeichnete Tick trägt den Strategie-Score (nicht das generische Standard-Momentum)
    ticks = db.get_today_ticks(CHAT).get("NVDA", [])
    assert ticks and ticks[-1]["strength"] == 10.0


# ── SL/TP-Modus „aus": einmalige Heads-up-Warnung (kein Auto-Close) ──────────

def test_sltp_off_warns_once_on_weak_signal():
    import asyncio
    from unittest.mock import AsyncMock
    bot._weak_warned.clear()
    fake_bot = AsyncMock()
    trade = {"ticker": "VLO", "direction": "long", "entry": 100.0,
             "signal": {"sl_tp_mode": "aus", "strength": 72.0, "leverage": 1.0}}
    asyncio.run(bot._maybe_warn_sltp_off(fake_bot, 1, trade, 98.0, 10.0))   # schwach → Warnung
    asyncio.run(bot._maybe_warn_sltp_off(fake_bot, 1, trade, 97.0, 8.0))    # gleiche Aktie/Tag → keine zweite
    assert fake_bot.send_message.await_count == 1


def test_sltp_off_no_warning_when_mode_on_or_strong():
    import asyncio
    from unittest.mock import AsyncMock
    bot._weak_warned.clear()
    fake_bot = AsyncMock()
    # Modus nicht „aus" → keine Warnung (wird ohnehin automatisch geschlossen)
    t1 = {"ticker": "AAA", "direction": "long", "entry": 100.0,
          "signal": {"sl_tp_mode": "normal", "strength": 72.0}}
    asyncio.run(bot._maybe_warn_sltp_off(fake_bot, 1, t1, 98.0, 5.0))
    # „aus", aber Signal weiterhin stark → keine Warnung
    t2 = {"ticker": "BBB", "direction": "long", "entry": 100.0,
          "signal": {"sl_tp_mode": "aus", "strength": 72.0}}
    asyncio.run(bot._maybe_warn_sltp_off(fake_bot, 1, t2, 101.0, 99.0))
    assert fake_bot.send_message.await_count == 0


# ── Aktive Trades: Einsatz/Exposure + gehebelte Rendite ──────────────────────

def test_active_trade_shows_invested_exposure_and_roi():
    fresh_db()
    db.yf = _FakeYF(100.0)                        # Einstiegskurs = 100
    db.get_or_create_user(CHAT, "tester")
    db.save_profile(CHAT, trade_size_eur=1000.0)
    db.add_pending(CHAT, {"ticker": "AAPL", "direction": "long", "price": 100.0,
                          "stop_loss": 95.0, "take_profit": 110.0, "leverage": 5.0}, 1)
    db.activate_trade(CHAT, "AAPL")
    db.add_tick(CHAT, "AAPL", 102.0, 60.0)       # +2 % Kursbewegung

    t = [x for x in dashboard.build_dashboard_data(db.get_user(CHAT))["active_trades"]
         if x["ticker"] == "AAPL"][0]
    assert t["invested"] == 1000.0               # eingesetzte Margin
    assert t["exposure"] == 5000.0               # Margin × Hebel = Position im Markt
    assert round(t["pnl_pct"], 1) == 2.0         # reine Kursbewegung
    assert round(t["roi_pct"], 1) == 10.0        # +2 % × 5× = +10 % auf die Margin (passt zum €)
    assert round(t["pnl_eur"], 0) == 100.0       # 1000 € × 2 % × 5


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
    """Gemeinsame Zeitachse: x = echter Zeitpunkt (Epoch-ms). Ein geschlossener Trade endet an
    seinem Ausstiegs-Zeitpunkt (vor 'jetzt'); ein offener läuft bis 'jetzt' (rechter Rand)."""
    import time
    fresh_db()
    db.yf = _FakeYF(100.0)                        # Einstiegskurs des offenen Trades = 100
    db.get_or_create_user(CHAT, "tester")
    db.save_profile(CHAT, trade_size_eur=25.0)
    # ein abgeschlossener (historischer) Trade vor 3 Tagen → Linie Einstieg(0%)→Ausstieg(+10%)
    with db._connect() as conn:
        conn.execute(
            "INSERT INTO trades (user_id, trade_date, ticker, direction, signal_json, status, "
            "entry, exit, pnl_eur, pnl_pct, broker_status, broker_updated_at, created_at) "
            "VALUES (?, date('now','-3 day'), 'MSFT', 'long', '{\"strategy\": \"standard\"}', "
            "'closed', 200, 220, 5.0, 10, 'reconciled', datetime('now','-3 day'), datetime('now','-3 day'))",
            (CHAT,),
        )
    # ein offener (aktueller) Trade mit zwei Intraday-Ticks
    db.add_pending(CHAT, {"ticker": "AAPL", "direction": "long", "price": 100.0,
                          "stop_loss": 97.0, "take_profit": 130.0, "strategy": "standard"}, 1)
    db.activate_trade(CHAT, "AAPL")
    db.add_tick(CHAT, "AAPL", 105.0, 70.0)       # +5 %
    db.add_tick(CHAT, "AAPL", 110.0, 72.0)       # +10 %

    data = dashboard.build_dashboard_data(db.get_user(CHAT))
    curves = data["trades_curves"]
    by_ticker = {x["ticker"]: x for x in curves}
    now_ms = time.time() * 1000.0

    msft = by_ticker["MSFT"]
    assert msft["status"] == "closed" and msft["final_pct"] == 10
    assert len(msft["points"]) == 2
    assert msft["points"][0]["y"] == 0.0 and msft["points"][1]["y"] == 10   # Einstieg→Ausstieg
    # Ausstieg liegt VOR jetzt (vor ~3 Tagen) und wird nicht bis zum rechten Rand gezeichnet
    assert 0 <= msft["points"][1]["x"] - msft["points"][0]["x"] <= 2000     # ~gleicher Zeitpunkt (Tagestrade)
    assert msft["points"][-1]["x"] < now_ms - 2 * 86400_000                 # endet > 2 Tage vor jetzt

    aapl = by_ticker["AAPL"]
    assert aapl["status"] == "active"
    assert aapl["points"][0]["y"] == 0.0                    # startet am Einstieg (0 %)
    assert aapl["points"][-1]["y"] == 10.0                  # zuletzt +10 % ab Einstieg
    assert aapl["points"][-1]["x"] >= now_ms - 5000         # offener Trade reicht bis ~jetzt

    # x-Domäne: frühester Einstieg (MSFT vor 3 Tagen) → jetzt
    assert data["trades_curves_x"]["max"] >= now_ms - 5000
    assert data["trades_curves_x"]["min"] <= now_ms - 2 * 86400_000
    # historische zuerst, offene danach
    assert curves[0]["ticker"] == "MSFT" and curves[-1]["ticker"] == "AAPL"


# ── Extended Hours (prepost) + Berliner Zeitachse ────────────────────────────

def test_download_all_timeframes_uses_prepost():
    from stockbot.market import analyzer
    calls = []
    class _FakeYF:
        @staticmethod
        def download(*a, **k):
            calls.append(k)
            return None
    orig = analyzer.yf
    analyzer.yf = _FakeYF
    try:
        analyzer._download_all_timeframes(["AAPL"])
    finally:
        analyzer.yf = orig
    # je Timeframe ein Download, jeweils mit Pre-/After-Market-Bars
    assert calls and all(k.get("prepost") is True for k in calls)


def test_dashboard_uses_berlin_time_for_ticks():
    # 13:30 UTC im Juni = 15:30 Berliner Sommerzeit (CEST, +2)
    assert dashboard._berlin_hhmm("2026-06-12 13:30:00") == "15:30"
    assert dashboard._berlin_hhmm(None) == ""

    fresh_db()
    db.yf = _FakeYF(101.0)
    db.get_or_create_user(CHAT, "tester")
    db.save_profile(CHAT, trade_size_eur=25.0)
    db.add_pending(CHAT, {"ticker": "AAPL", "direction": "long", "price": 100.0,
                          "stop_loss": 97.0, "take_profit": 105.0}, 1)
    db.activate_trade(CHAT, "AAPL")
    # Tick mit explizitem UTC-Zeitstempel schreiben (trade_date wie der Code = _today(),
    # damit es auch um Mitternacht/UTC-Versatz deterministisch ist)
    with db._connect() as conn:
        conn.execute(
            "INSERT INTO trade_ticks (user_id, trade_date, ticker, ts, price, strength) "
            "VALUES (?, ?, 'AAPL', '2026-06-12 13:30:00', 101.0, 60.0)",
            (CHAT, db._today()),
        )
    data = dashboard.build_dashboard_data(db.get_user(CHAT))
    pts = [s for s in data["intraday"] if s["ticker"] == "AAPL"][0]["points"]
    assert pts[-1]["t"] == "15:30"        # Berliner Zeit, nicht 13:30 UTC


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


# ── _us_market_open: Markt-offen-Gating (Wochentag + ET-Zeitfenster) ─────────

class _FixedClock:
    """Ersetzt bot.datetime, damit _us_market_open eine feste 'jetzt'-Zeit sieht."""
    def __init__(self, dt): self._dt = dt
    def now(self, tz=None): return self._dt


def _market_open(dt, extended):
    orig = bot.datetime
    bot.datetime = _FixedClock(dt)
    try:
        return bot._us_market_open(extended=extended)
    finally:
        bot.datetime = orig


def test_us_market_open_regular_vs_extended_hours():
    wed = lambda h, m=0: datetime(2026, 6, 10, h, m)   # Mittwoch (weekday 2)
    # 10:00 ET → regulär offen (9:30–16:00)
    assert _market_open(wed(10), extended=False) is True
    # 08:00 ET → regulär ZU, aber mit Extended Hours offen (4:00–20:00)
    assert _market_open(wed(8), extended=False) is False
    assert _market_open(wed(8), extended=True) is True


def test_us_market_closed_at_night_and_on_weekend():
    # 21:00 ET → auch Extended Hours zu (Fenster endet 20:00)
    assert _market_open(datetime(2026, 6, 10, 21, 0), extended=True) is False
    # Samstag (weekday 5) → immer zu, egal welche Uhrzeit
    assert _market_open(datetime(2026, 6, 13, 12, 0), extended=False) is False


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
