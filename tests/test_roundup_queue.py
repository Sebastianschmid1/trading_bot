"""
Tests für die drei Sizing-/Order-Verbesserungen:

(a) Queue: Bruchteil-Orders außerhalb regulärer Zeit werden vorgemerkt (broker_pending/
    queued_regular) statt zu scheitern.
(b) Aufrunden: ist eine Aktie nur etwas teurer als das Budget (≤ Faktor), kauft der Bot 1 ganze
    Aktie statt eines Bruchteils. Plus sizing_hint (Warnung/Empfehlung).
(c) Auto-Accept nur in regulärer Sitzung (Logik über _us_market_open).

Lauf:  pytest tests/test_roundup_queue.py   (offline)
"""

from stockbot.broker import sizing
from stockbot.web import dashboard


# ── (b) Aufrunden ────────────────────────────────────────────────────────────

def test_roundup_to_one_whole_share_within_factor():
    p = sizing.plan_share_order(30.0, 25.0, roundup_factor=1.5)   # 30 ≤ 25×1.5
    assert p["qty"] == 1 and p["fractional"] is False and p["rounded_up"] is True


def test_no_roundup_when_too_expensive():
    p = sizing.plan_share_order(60.0, 25.0, roundup_factor=1.5)   # 60 > 37.5 → Bruchteil
    assert p["qty"] is None and p["notional"] == 25.0 and p["fractional"] is True


def test_roundup_off_by_default_factor_one():
    p = sizing.plan_share_order(30.0, 25.0, roundup_factor=1.0)
    assert p["fractional"] is True and p["rounded_up"] is False


def test_plan_order_passes_roundup_factor():
    p = sizing.plan_order(30.0, 25.0, 1.0, option_selector=None, extended=False, roundup_factor=1.5)
    assert p["kind"] == "shares" and p["qty"] == 1 and p["rounded_up"] is True


# ── (b) sizing_hint ──────────────────────────────────────────────────────────

def _trades(prices):
    return [{"signal": {"price": p}} for p in prices]


def test_sizing_hint_recommends_when_budget_too_small():
    # Budget 25; Preise überwiegend > 25 → Hinweis mit Empfehlung (auf nächste 50 gerundet).
    hint = dashboard.sizing_hint(25.0, _trades([100, 120, 80, 200, 60, 150, 90]))
    assert hint is not None
    assert hint["trade_size"] == 25 and hint["frac_rate"] == 100
    assert hint["recommended"] % 50 == 0 and hint["recommended"] >= 150


def test_sizing_hint_none_when_budget_sufficient():
    # Budget 1000; alle Preise darunter → kein Hinweis.
    assert dashboard.sizing_hint(1000.0, _trades([100, 120, 80, 200, 60])) is None


def test_sizing_hint_none_with_too_few_trades():
    assert dashboard.sizing_hint(25.0, _trades([100, 120])) is None


# ── (a) Queue im Order-Pfad (Web) ────────────────────────────────────────────

def test_web_order_queues_fractional_in_extended_hours(monkeypatch):
    import tempfile
    from pathlib import Path
    from stockbot.core import db
    from stockbot.web import webapp

    d = tempfile.mkdtemp(prefix="queuetest_")
    db.DB_FILE = Path(d) / "q.db"
    db.init_db()
    CHAT = 555
    db.get_or_create_user(CHAT, "u")
    db.save_profile(CHAT, trade_size_eur=25.0)
    db.add_pending(CHAT, {"ticker": "FIX", "direction": "long", "price": 2000.0,
                          "leverage": 1.0, "strength": 60.0}, 1)
    with db._connect() as c:
        c.execute("UPDATE trades SET status='broker_pending', entry=2000.0 WHERE ticker='FIX'")

    # Alpaca: Markt geschlossen (→ extended), Order soll NICHT gesendet, sondern vorgemerkt werden.
    monkeypatch.setattr(webapp.broker, "market_open", lambda c: False)
    monkeypatch.setattr(webapp.config, "EXTENDED_HOURS", True)
    monkeypatch.setattr(webapp, "_alpaca_client", lambda u: object())
    monkeypatch.setattr(webapp, "_broker_will_execute", lambda u: True)
    monkeypatch.setattr(webapp.broker, "account_summary", lambda c: {"ok": True, "buying_power": 1e9})
    sent = {"n": 0}
    monkeypatch.setattr(webapp.broker, "submit_buy", lambda *a, **k: sent.__setitem__("n", sent["n"] + 1) or {"ok": True, "id": "x"})

    user = db.get_user(CHAT)
    trade = db.get_trade(CHAT, "FIX")
    res = webapp._execute_broker_order_for_web(user, trade)
    assert res["status"] == "broker_pending" and "vorgemerkt" in res["msg"].lower()
    assert sent["n"] == 0                                   # nichts gesendet
    t = db.get_trade(CHAT, "FIX")
    assert t["broker_status"] == "queued_regular" and t["status"] == "broker_pending"


# ── (c) Auto-Accept nur in regulärer Sitzung ─────────────────────────────────

def _make_signal(ticker="NVDA", price=100.0):
    return {"ticker": ticker, "price": price, "as_of": "2026-06-05", "direction": "long",
            "strength": 60, "rsi": 32.0, "rsi_comment": "Überverkauft", "macd_comment": "Bullish",
            "trend_comment": "Auf", "weekly_comment": "Auf", "volume_comment": "Hoch",
            "sr_comment": "Support · Widerstand", "stop_loss": price * 0.97,
            "take_profit": price * 1.05, "sl_pct": -3.0, "tp_pct": 5.0, "risk_reward": 1.67}


def _fake_bot():
    from unittest.mock import AsyncMock, MagicMock
    b = MagicMock()
    b.send_message = AsyncMock(return_value=MagicMock(message_id=111))
    b.delete_message = AsyncMock()
    return b


def test_auto_accept_skipped_outside_regular_session(monkeypatch):
    import asyncio
    import tempfile
    from pathlib import Path
    from stockbot.core import db
    from stockbot.tgbot import bot

    db.DB_FILE = Path(tempfile.mkdtemp(prefix="autoacc_")) / "a.db"
    db.init_db()
    CHAT = 4242
    db.get_or_create_user(CHAT, "u")
    db.save_profile(CHAT, trade_size_eur=25.0)

    # market_open (extended) wird True übergeben, aber die reguläre Sitzung ist geschlossen.
    monkeypatch.setattr(bot, "_us_market_open", lambda extended=None: False)
    sent = asyncio.run(bot.send_signal(_fake_bot(), CHAT, _make_signal("NVDA"), 25.0,
                                       market_open=True, auto_accept=True))
    assert sent is True
    # KEIN Auto-Start: der Trade bleibt 'pending' (Button-Karte statt sofortiger Aktivierung).
    t = db.get_trade(CHAT, "NVDA")
    assert t is not None and t["status"] == "pending"
