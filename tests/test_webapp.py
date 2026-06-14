"""
Tests für die Website (Phase 1–2): Auth/Session, Signal-Aktionen, Einstellungen, Watchlist,
Mitteilungen, notify_channel und der Telegram-Login-HMAC. Nutzt den Starlette-TestClient.

Lauf:  python -m tests.test_webapp   oder   pytest tests/test_webapp.py
Offline (yfinance/Alpaca/LLM gemockt).
"""

import sys
import hmac
import time
import hashlib
import tempfile
from pathlib import Path

from starlette.testclient import TestClient

from stockbot.core import db
from stockbot.market import lookup
from stockbot.services import notifications as notify_svc
from stockbot.web import auth

CHAT = 4242


class _FakeYF:
    def __init__(self, price): self._p = price
    def Ticker(self, t):
        price = self._p
        class _T:
            @property
            def fast_info(self):
                class _FI: last_price = price
                return _FI()
        return _T()


def fresh():
    d = tempfile.mkdtemp(prefix="webtest_")
    db.DB_FILE = Path(d) / "web.db"
    db.init_db()
    db.yf = _FakeYF(100.0)
    db.get_or_create_user(CHAT, "weytester")
    db.save_profile(CHAT, trade_size_eur=100.0)
    return db.get_or_create_dashboard_token(CHAT)


def _client(login=True):
    from stockbot.web.dashboard import app
    c = TestClient(app)
    if login:
        tok = db.get_or_create_dashboard_token(CHAT)
        c.get(f"/auth/token?token={tok}")
    return c


# ── Auth ──────────────────────────────────────────────────────────────────────

def test_app_requires_login():
    fresh()
    c = TestClient(__import__("stockbot.web.dashboard", fromlist=["app"]).app)
    r = c.get("/app", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_token_login_sets_session_and_grants_access():
    tok = fresh()
    c = TestClient(__import__("stockbot.web.dashboard", fromlist=["app"]).app)
    r = c.get(f"/auth/token?token={tok}", follow_redirects=False)
    assert r.status_code == 303 and "sb_session" in r.headers.get("set-cookie", "")
    assert c.get("/app").status_code == 200


def test_invalid_token_redirects_to_login():
    fresh()
    c = TestClient(__import__("stockbot.web.dashboard", fromlist=["app"]).app)
    r = c.get("/auth/token?token=falsch", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_logout_clears_session():
    fresh()
    c = _client()
    assert c.get("/app").status_code == 200
    c.post("/logout")
    assert c.get("/app", follow_redirects=False).status_code == 303


# ── Signal-Aktionen (über dieselbe Service-Schicht wie Telegram) ─────────────

def test_accept_and_sell_via_web():
    fresh()
    db.add_pending(CHAT, {"ticker": "NVDA", "direction": "long", "price": 100.0,
                          "leverage": 1.0, "strength": 70.0}, 1)
    c = _client()
    assert any(t["ticker"] == "NVDA" for t in db.get_pending_trades(CHAT))
    c.post("/app/accept", data={"ticker": "NVDA"}, follow_redirects=False)
    assert db.get_active_trades(CHAT) and db.get_active_trades(CHAT)[0]["ticker"] == "NVDA"
    c.post("/app/sell", data={"ticker": "NVDA"}, follow_redirects=False)
    assert db.get_active_trades(CHAT) == []


def test_reject_via_web():
    fresh()
    db.add_pending(CHAT, {"ticker": "AAPL", "direction": "long", "price": 100.0}, 1)
    c = _client()
    c.post("/app/reject", data={"ticker": "AAPL"}, follow_redirects=False)
    assert db.get_pending_trades(CHAT) == []


def test_lev_via_web():
    fresh()
    db.add_pending(CHAT, {"ticker": "NVDA", "direction": "long", "price": 100.0, "leverage": 1.0}, 1)
    c = _client()
    c.post("/app/lev", data={"ticker": "NVDA", "leverage": "3"}, follow_redirects=False)
    assert db.get_trade(CHAT, "NVDA")["signal"]["leverage"] == 3.0


# ── Einstellungen & Watchlist ───────────────────────────────────────────────

def test_settings_and_notify_channel_via_web():
    fresh()
    c = _client()
    c.post("/app/settings/set", data={"action": "set_size", "value": "250"}, follow_redirects=False)
    assert db.get_user(CHAT)["trade_size_eur"] == 250.0
    c.post("/app/settings/set", data={"action": "set_strat", "value": "adx_trend"}, follow_redirects=False)
    assert "adx_trend" in db.get_user(CHAT)["strategies"]
    c.post("/app/settings/notify", data={"value": "web"}, follow_redirects=False)
    assert db.get_user(CHAT)["notify_channel"] == "web"


def test_watchlist_add_remove_via_web():
    fresh()
    c = _client()
    o = lookup.validate_ticker
    lookup.validate_ticker = lambda s: {"ok": True, "symbol": "AAPL", "name": "Apple Inc.",
                                        "quote_type": "EQUITY", "price": 185.0}
    try:
        c.post("/app/watchlist/add", data={"symbol": "aapl"}, follow_redirects=False)
    finally:
        lookup.validate_ticker = o
    assert db.get_user(CHAT)["watchlist"] == ["AAPL"]
    c.post("/app/watchlist/remove", data={"symbol": "AAPL"}, follow_redirects=False)
    assert db.get_user(CHAT)["watchlist"] == []


def test_watchlist_add_unknown_shows_suggestions():
    fresh()
    c = _client()
    o_val, o_search = lookup.validate_ticker, lookup.search_symbols
    lookup.validate_ticker = lambda s: {"ok": False, "symbol": s}
    lookup.search_symbols = lambda s, limit=5: [{"symbol": "AAPL", "name": "Apple", "quote_type": "EQUITY"}]
    try:
        r = c.post("/app/watchlist/add", data={"symbol": "appel"}, follow_redirects=False)
    finally:
        lookup.validate_ticker, lookup.search_symbols = o_val, o_search
    assert "suggestions=AAPL" in r.headers["location"]
    assert db.get_user(CHAT)["watchlist"] == []


# ── Mitteilungen ────────────────────────────────────────────────────────────

def test_notifications_page_and_mark_read():
    fresh()
    db.add_notification(CHAT, "Neues Signal", "NVDA", "signal")
    assert db.unread_count(CHAT) == 1
    c = _client()
    r = c.get("/app/notifications")
    assert r.status_code == 200 and "Neues Signal" in r.text
    assert db.unread_count(CHAT) == 0          # Aufruf markiert als gelesen


def test_notify_service_respects_channel():
    fresh()
    db.set_notify_channel(CHAT, "telegram")
    assert notify_svc.notify(CHAT, "x") is False and db.unread_count(CHAT) == 0
    db.set_notify_channel(CHAT, "web")
    assert notify_svc.notify(CHAT, "y") is True and db.unread_count(CHAT) == 1


# ── /website-Befehl im Telegram-Bot ─────────────────────────────────────────

def test_cmd_website_sends_one_click_login_link():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from stockbot.tgbot import bot
    fresh()
    tok = db.get_or_create_dashboard_token(CHAT)
    upd = MagicMock()
    upd.effective_chat.id = CHAT
    upd.message.reply_text = AsyncMock()
    asyncio.run(bot.cmd_website(upd, MagicMock()))
    sent = upd.message.reply_text.call_args.args[0]
    assert f"/auth/token?token={tok}" in sent


# ── Telegram-Login-HMAC ─────────────────────────────────────────────────────

def test_verify_telegram_login_valid_and_tampered():
    orig = auth.TELEGRAM_TOKEN
    auth.TELEGRAM_TOKEN = "123:test-bot-token"
    try:
        data = {"id": "555", "first_name": "Max", "auth_date": str(int(time.time()))}
        check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
        secret = hashlib.sha256(auth.TELEGRAM_TOKEN.encode()).digest()
        data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        assert auth.verify_telegram_login(data) == 555
        bad = dict(data); bad["hash"] = "deadbeef"
        assert auth.verify_telegram_login(bad) is None
    finally:
        auth.TELEGRAM_TOKEN = orig


# ── Runner ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn(); print(f"✅ {name}"); passed += 1
        except AssertionError as e:
            print(f"❌ {name}: {e}"); failed += 1
        except Exception as e:
            print(f"💥 {name}: {type(e).__name__}: {e}"); failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
