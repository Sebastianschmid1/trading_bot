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

import pandas as pd

from starlette.testclient import TestClient

from stockbot.core import db
from stockbot.market import lookup
from stockbot.market import strategies
from stockbot.backtest import engine as backtest_engine
from stockbot.services import notifications as notify_svc
from stockbot.web import auth
from stockbot.web import dashboard as _dashboard   # zuerst importieren: vermeidet Zirkularimport
from stockbot.web import webapp                     # (webapp wird normal über dashboard geladen)

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


def _trend_df(start=50.0, end=100.0, n=260):
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    close_vals = [start + (end - start) * i / (n - 1) for i in range(n)]
    close = pd.Series(close_vals, index=idx)
    high = close * 1.01
    low = close * 0.99
    open_ = close.shift(1).fillna(close.iloc[0])
    vol = pd.Series([1_000_000] * n, index=idx)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol})


def fresh():
    d = tempfile.mkdtemp(prefix="webtest_")
    db.DB_FILE = Path(d) / "web.db"
    db.init_db()
    db.yf = _FakeYF(100.0)
    db.get_or_create_user(CHAT, "weytester")
    db.save_profile(CHAT, trade_size_eur=100.0)
    auth._auth_hits.clear()          # Rate-Limit-Zähler je Test zurücksetzen (sonst kumuliert er)
    webapp._scan_cache.clear()       # On-Demand-Scan-Cache je Test leeren (modulglobal)
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


def test_app_dashboard_is_integrated_into_site_layout():
    fresh()
    c = _client()
    r = c.get("/app/dashboard")
    assert r.status_code == 200
    assert "Trading Cockpit" in r.text            # Seitentitel des Cockpit-Dashboards
    assert "Dashboard" in r.text                   # Navigation der Website ist eingebunden


def test_history_page_lists_closed_trades():
    from stockbot.services import trades as trade_svc
    fresh()
    db.add_pending(CHAT, {"ticker": "NVDA", "direction": "long", "price": 100.0,
                          "leverage": 1.0, "strength": 70.0}, 1)
    trade_svc.accept_trade(CHAT, "NVDA")
    db.close_all(CHAT, [{"ticker": "NVDA", "exit": 110.0, "pnl_eur": 10.0, "pnl_pct": 10.0}])

    c = _client()
    r = c.get("/app/history")
    assert r.status_code == 200
    assert "Verlauf" in r.text                     # Seiten-/Nav-Titel
    assert "NVDA" in r.text                         # abgeschlossener Trade gelistet
    assert '/static/tokens.css' in r.text           # zentrale Design-Tokens eingebunden
    assert "fonts.googleapis" not in r.text        # Fonts bleiben CSP-/offline-sicher


def test_design_tokens_and_mode_badge_are_served():
    fresh()
    c = _client()

    page = c.get("/app")
    assert 'class="mode-badge mode-badge--shadow' in page.text
    assert 'data-trading-mode="shadow"' in page.text
    assert "SHADOW" in page.text

    css = c.get("/static/tokens.css")
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    for token in ("--bg-base", "--text-primary", "--primary", "--space-1",
                  "--radius-sm", "--shadow-sm", "--font-size-display"):
        assert token in css.text
    for mode in ("backtest", "shadow", "paper", "live"):
        assert f".mode-badge--{mode}" in css.text


def test_history_empty_state_renders():
    fresh()
    c = _client()
    r = c.get("/app/history")
    assert r.status_code == 200 and "Noch keine abgeschlossenen Trades" in r.text


def test_new_website_element_aliases_redirect_to_app_routes():
    fresh()
    c = _client()
    for plain, app_path in [
        ("/backtest", "/app/backtest"),
        ("/reports", "/app/reports"),
        ("/dashboard", "/app/dashboard"),
    ]:
        r = c.get(plain, follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == app_path


def test_app_shows_friendly_trade_status_text():
    fresh()
    db.add_pending(CHAT, {"ticker": "AAPL", "direction": "long", "price": 100.0,
                          "leverage": 1.0, "strength": 70.0}, 1)
    db.activate_trade(CHAT, "AAPL", status="broker_pending")
    db.mark_broker_pending(CHAT, "AAPL", order_id="ord-1", broker_status="accepted")
    c = _client()
    html = c.get("/app").text
    assert "AAPL" in html
    assert "Broker wartet" in html


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


def test_web_accept_with_broker_exec_tracks_order_fill(monkeypatch):
    fresh()
    db.set_broker_exec(CHAT, True)
    db.add_pending(CHAT, {"ticker": "NVDA", "direction": "long", "price": 100.0,
                          "leverage": 1.0, "strength": 70.0}, 1)
    calls = []
    monkeypatch.setattr(webapp, "_alpaca_ready", lambda user: True)
    monkeypatch.setattr(webapp, "_alpaca_client", lambda user: object())
    monkeypatch.setattr(webapp.sizing, "plan_order", lambda entry, budget, leverage, option_selector=None, extended=False, **kwargs: {"kind": "stock", "qty": 1})
    monkeypatch.setattr(webapp.broker, "submit_buy", lambda symbol, **kwargs: calls.append((symbol, kwargs)) or {"ok": True, "id": "ord-web", "detail": "NVDA ×1"})
    monkeypatch.setattr(webapp.broker, "get_order_status", lambda order_id, client=None: {"ok": True, "status": "filled", "filled_qty": 1.0, "filled_avg_price": 101.25})

    r = _client().post("/app/accept", data={"ticker": "NVDA"}, headers={"X-Requested-With": "fetch"})

    assert r.json()["status"] == "filled"
    assert calls and calls[0][0] == "NVDA"
    trade = db.get_trade(CHAT, "NVDA")
    assert trade["status"] == "active"
    assert trade["broker_status"] == "filled"
    assert trade["broker_order_id"] == "ord-web"
    assert db.get_active_trades(CHAT)[0]["ticker"] == "NVDA"
    with db._connect() as conn:
        intent = conn.execute("SELECT * FROM trade_intents").fetchone()
        order = conn.execute("SELECT * FROM orders").fetchone()
    assert intent["source_channel"] == "web"
    assert intent["idempotency_key"] == f"web:{CHAT}:{trade['id']}:accept"
    assert order["broker_order_id"] == "ord-web"


def test_web_live_account_without_release_is_blocked_by_oms(monkeypatch):
    fresh()
    db.set_broker_exec(CHAT, True)
    db.add_pending(CHAT, {"ticker": "LIVE", "direction": "long", "price": 100.0,
                          "leverage": 1.0, "strength": 70.0}, 1)

    class LiveClient:
        _paper = False

    submitted = {"called": False}
    monkeypatch.setattr(webapp.config, "LIVE_TRADING_ENABLED", False)
    monkeypatch.setattr(webapp, "_alpaca_ready", lambda user: True)
    monkeypatch.setattr(webapp, "_alpaca_client", lambda user: LiveClient())
    monkeypatch.setattr(webapp.broker, "market_open", lambda client: True)
    monkeypatch.setattr(webapp.broker, "account_summary",
                        lambda client=None: {"ok": True, "buying_power": 1000.0})
    monkeypatch.setattr(webapp.broker, "submit_buy",
                        lambda *args, **kwargs: submitted.__setitem__("called", True))

    response = _client().post(
        "/app/accept", data={"ticker": "LIVE"}, headers={"X-Requested-With": "fetch"})

    assert response.json()["status"] == "broker_failed"
    assert submitted["called"] is False
    trade = db.get_trade(CHAT, "LIVE")
    assert trade["broker_status"] == "live_blocked"
    assert db.get_order_by_idempotency_key(f"web:{CHAT}:{trade['id']}:accept") is None


def test_web_accept_with_broker_exec_stays_pending_until_fill(monkeypatch):
    fresh()
    db.set_broker_exec(CHAT, True)
    db.add_pending(CHAT, {"ticker": "AAPL", "direction": "long", "price": 100.0,
                          "leverage": 1.0, "strength": 70.0}, 1)
    monkeypatch.setattr(webapp, "_alpaca_ready", lambda user: True)
    monkeypatch.setattr(webapp, "_alpaca_client", lambda user: object())
    monkeypatch.setattr(webapp.broker, "account_summary", lambda client=None: {"ok": True, "buying_power": 1000.0})
    monkeypatch.setattr(webapp.sizing, "plan_order", lambda entry, budget, leverage, option_selector=None, extended=False, **kwargs: {"kind": "stock", "qty": 1})
    monkeypatch.setattr(webapp.broker, "submit_buy", lambda symbol, **kwargs: {"ok": True, "id": "ord-wait", "detail": "AAPL ×1"})
    monkeypatch.setattr(webapp.broker, "get_order_status", lambda order_id, client=None: {"ok": True, "status": "accepted"})

    r = _client().post("/app/accept", data={"ticker": "AAPL"}, headers={"X-Requested-With": "fetch"})

    assert r.json()["status"] == "broker_pending"
    trade = db.get_trade(CHAT, "AAPL")
    assert trade["status"] == "broker_pending"
    assert trade["broker_status"] == "accepted"
    assert trade["broker_order_id"] == "ord-wait"
    assert db.get_active_trades(CHAT) == []
    assert any(t["ticker"] == "AAPL" for t in db.get_broker_pending_trades(CHAT))


def test_web_accept_does_not_submit_when_buying_power_is_insufficient(monkeypatch):
    fresh()
    db.set_broker_exec(CHAT, True)
    db.add_pending(CHAT, {"ticker": "TSLA", "direction": "long", "price": 827.51,
                          "leverage": 1.0, "strength": 70.0}, 1)
    submitted = {"called": False}
    monkeypatch.setattr(webapp, "_alpaca_ready", lambda user: True)
    monkeypatch.setattr(webapp, "_alpaca_client", lambda user: object())
    monkeypatch.setattr(webapp.broker, "account_summary", lambda client=None: {"ok": True, "buying_power": 0.0})
    monkeypatch.setattr(webapp.sizing, "plan_order", lambda entry, budget, leverage, option_selector=None, extended=False, **kwargs: {"kind": "shares", "qty": 1, "notional": None})
    def fake_submit(*args, **kwargs):
        submitted["called"] = True
        return {"ok": True, "id": "should-not-submit"}
    monkeypatch.setattr(webapp.broker, "submit_buy", fake_submit)

    r = _client().post("/app/accept", data={"ticker": "TSLA"}, headers={"X-Requested-With": "fetch"})

    assert r.json()["status"] == "broker_failed"
    assert "Buying Power" in r.json()["msg"]
    assert submitted["called"] is False
    trade = db.get_trade(CHAT, "TSLA")
    assert trade is not None
    assert trade["status"] == "broker_failed"
    assert trade["broker_status"] == "insufficient_buying_power"
    assert db.get_active_trades(CHAT) == []


def test_web_accept_rejects_broker_order_with_stale_leverage_above_one(monkeypatch):
    """TSAFE-002: ein Signal mit Hebel > MAX_LEVERAGE (z. B. ein Altwert aus der Zeit vor dem
    harten Server-Cap) darf niemals eine echte Broker-Order auslösen — die Order wird explizit
    abgelehnt statt still auf Aktien/1x herabgestuft zu werden."""
    fresh()
    db.set_broker_exec(CHAT, True)
    db.add_pending(CHAT, {"ticker": "TSLA", "direction": "long", "price": 100.0,
                          "leverage": 5.0, "strength": 70.0}, 1)
    monkeypatch.setattr(webapp, "_alpaca_ready", lambda user: True)
    monkeypatch.setattr(webapp, "_alpaca_client", lambda user: object())
    submitted = {"called": False}
    def fake_submit(*args, **kwargs):
        submitted["called"] = True
        return {"ok": True, "id": "should-not-submit"}
    monkeypatch.setattr(webapp.broker, "submit_buy", fake_submit)
    monkeypatch.setattr(webapp.broker, "submit_option_buy", fake_submit)

    r = _client().post("/app/accept", data={"ticker": "TSLA"}, headers={"X-Requested-With": "fetch"})

    assert r.json()["status"] == "broker_failed"
    assert "Hebel" in r.json()["msg"]
    assert submitted["called"] is False
    trade = db.get_trade(CHAT, "TSLA")
    assert trade["status"] == "broker_failed"
    assert trade["broker_status"] == "leverage_blocked"


def test_web_sell_with_broker_exec_sets_broker_closing(monkeypatch):
    fresh()
    db.set_broker_exec(CHAT, True)
    db.add_pending(CHAT, {"ticker": "NVDA", "direction": "long", "price": 100.0,
                          "leverage": 1.0, "strength": 70.0}, 1)
    monkeypatch.setattr(webapp, "_alpaca_ready", lambda user: True)
    monkeypatch.setattr(webapp, "_alpaca_client", lambda user: object())
    monkeypatch.setattr(webapp.sizing, "plan_order", lambda entry, budget, leverage, option_selector=None, extended=False, **kwargs: {"kind": "stock", "qty": 1})
    monkeypatch.setattr(webapp.broker, "submit_buy", lambda symbol, **kwargs: {"ok": True, "id": "ord-buy", "detail": "NVDA ×1"})
    monkeypatch.setattr(webapp.broker, "get_order_status", lambda order_id, client=None: {"ok": True, "status": "filled", "filled_qty": 1.0, "filled_avg_price": 100.0})
    _client().post("/app/accept", data={"ticker": "NVDA"}, headers={"X-Requested-With": "fetch"})

    monkeypatch.setattr(webapp.broker, "close_position", lambda symbol, client=None: {"ok": True, "closed": True, "id": "ord-sell"})
    monkeypatch.setattr(webapp.broker, "get_order_status", lambda order_id, client=None: {"ok": True, "status": "accepted"})

    _client().post("/app/sell", data={"ticker": "NVDA"}, follow_redirects=False)
    trade = db.get_trade(CHAT, "NVDA")
    assert trade["status"] == "broker_closing"
    assert trade["broker_status"] == "accepted"
    assert trade["broker_order_id"] == "ord-sell"
    assert db.get_active_trades(CHAT) == []
    assert any(t["ticker"] == "NVDA" for t in db.get_broker_closing_trades(CHAT))


def test_web_sell_with_broker_exec_closes_after_fill(monkeypatch):
    fresh()
    db.set_broker_exec(CHAT, True)
    db.add_pending(CHAT, {"ticker": "TSLA", "direction": "long", "price": 100.0,
                          "leverage": 1.0, "strength": 70.0}, 1)
    monkeypatch.setattr(webapp, "_alpaca_ready", lambda user: True)
    monkeypatch.setattr(webapp, "_alpaca_client", lambda user: object())
    monkeypatch.setattr(webapp.sizing, "plan_order", lambda entry, budget, leverage, option_selector=None, extended=False, **kwargs: {"kind": "stock", "qty": 1})
    monkeypatch.setattr(webapp.broker, "submit_buy", lambda symbol, **kwargs: {"ok": True, "id": "ord-buy2", "detail": "TSLA ×1"})
    monkeypatch.setattr(webapp.broker, "get_order_status", lambda order_id, client=None: {"ok": True, "status": "filled", "filled_qty": 1.0, "filled_avg_price": 100.0})
    _client().post("/app/accept", data={"ticker": "TSLA"}, headers={"X-Requested-With": "fetch"})

    monkeypatch.setattr(webapp.broker, "close_position", lambda symbol, client=None: {"ok": True, "closed": True, "id": "ord-sell2"})
    monkeypatch.setattr(webapp.broker, "get_order_status", lambda order_id, client=None: {"ok": True, "status": "filled", "filled_qty": 1.0, "filled_avg_price": 105.0})

    _client().post("/app/sell", data={"ticker": "TSLA"}, follow_redirects=False)
    trade = db.get_trade(CHAT, "TSLA")
    assert trade["status"] == "closed"
    assert db.get_active_trades(CHAT) == []


def test_reject_via_web():
    fresh()
    db.add_pending(CHAT, {"ticker": "AAPL", "direction": "long", "price": 100.0}, 1)
    c = _client()
    c.post("/app/reject", data={"ticker": "AAPL"}, follow_redirects=False)
    assert db.get_pending_trades(CHAT) == []


def test_lev_via_web():
    # TSAFE-002: kein UI-Auswahl mehr, Backend klemmt serverseitig hart auf MAX_LEVERAGE (1×).
    fresh()
    db.add_pending(CHAT, {"ticker": "NVDA", "direction": "long", "price": 100.0, "leverage": 1.0}, 1)
    c = _client()
    c.post("/app/lev", data={"ticker": "NVDA", "leverage": "3"}, follow_redirects=False)
    assert db.get_trade(CHAT, "NVDA")["signal"]["leverage"] == 1.0


def test_backtest_editor_supports_single_compare_and_portfolio(monkeypatch):
    fresh()
    c = _client()
    monkeypatch.setattr(backtest_engine, "_download_daily", lambda tickers, years: {"AAPL": _trend_df()})

    single = c.post(
        "/app/backtest",
        data={
            "mode": "single",
            "strategy": "high52_wide",
            "tickers": "AAPL",
            "years": "1",
            "trade_size": "100.0",
            "top_n": "10",
            "leverage": "5.0",
            "max_concurrent": "10",
            "max_hold": "20",
            "q": "high52",
        },
        follow_redirects=False,
    )
    assert single.status_code == 200
    assert "Ergebnis" in single.text and "Trades" in single.text

    monkeypatch.setattr(
        backtest_engine,
        "compare_strategies",
        lambda keys, tickers=None, years=2, trade_size=100.0, allow_short=False: [
            {"label": "A", "metrics": {"profit_factor": 1.5, "win_rate": 55.0, "total_pnl_eur": 12.3, "trades": 3}},
            {"label": "B", "metrics": {"profit_factor": 1.2, "win_rate": 50.0, "total_pnl_eur": 7.1, "trades": 2}},
        ],
    )
    compare = c.post(
        "/app/backtest",
        data={
            "mode": "compare",
            "strategy": "high52_wide",
            "compare_keys": "high52_wide, breakout",
            "tickers": "AAPL",
            "years": "1",
            "trade_size": "100.0",
            "top_n": "10",
            "leverage": "5.0",
            "max_concurrent": "10",
            "max_hold": "20",
            "q": "",
        },
        follow_redirects=False,
    )
    assert compare.status_code == 200
    assert "Compare Results" in compare.text
    assert "A" in compare.text and "B" in compare.text

    monkeypatch.setattr(
        backtest_engine,
        "backtest_portfolio",
        lambda *args, **kwargs: {
            "label": "Portfolio",
            "n_tickers": 1,
            "years": 1,
            "metrics": {"trades": 2, "profit_factor": 1.6, "win_rate": 60.0, "total_pnl_eur": 14.5, "max_drawdown_pct": 3.2, "avg_win": 9.0, "avg_loss": -2.0},
            "trades": [{"ticker": "AAPL", "entry_date": "2024-01-01", "exit_date": "2024-01-03", "pnl_pct": 2.0, "pnl_eur": 9.0, "reason": "Take-Profit"}],
        },
    )
    portfolio = c.post(
        "/app/backtest",
        data={
            "mode": "portfolio",
            "strategy": "high52_wide",
            "tickers": "AAPL",
            "years": "1",
            "trade_size": "100.0",
            "top_n": "5",
            "leverage": "4.0",
            "max_concurrent": "3",
            "max_hold": "12",
            "q": "high52",
        },
        follow_redirects=False,
    )
    assert portfolio.status_code == 200
    assert "Ergebnis: Portfolio" in portfolio.text
    assert "Take-Profit" in portfolio.text


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


def test_settings_page_shows_readonly_risk_parameters():
    # UI für Risikoeinstellungen (PLAN_CHECKLIST.md Phase 3) — die serverseitigen
    # Sicherheitsgrenzen müssen auf der Einstellungsseite sichtbar sein, auch schreibgeschützt.
    fresh()
    c = _client()
    r = c.get("/app/settings")
    assert r.status_code == 200
    assert "Risiko-Parameter" in r.text
    assert "Maximaler Hebel" in r.text
    assert f"{webapp.config.MAX_LEVERAGE:g}×" in r.text


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


# ── Asset-Klassen-Dropdown ───────────────────────────────────────────────────

def test_app_shows_asset_dropdown_with_aktien():
    fresh()
    c = _client()
    r = c.get("/app")
    assert r.status_code == 200 and "Anlageklasse" in r.text and "Aktien" in r.text


def test_set_asset_only_persists_registered_keys():
    """Die Route validiert gegen die Registry: registrierte Klassen werden gespeichert,
    unbekannte fallen auf Aktien zurück (so landen nie ungültige Werte in der DB)."""
    fresh()
    c = _client()
    for key in ("etf", "crypto", "commodity"):
        c.post("/app/asset", data={"asset": key}, follow_redirects=False)
        assert db.get_user(CHAT)["asset_pref"] == key
    c.post("/app/asset", data={"asset": "quatsch"}, follow_redirects=False)
    assert db.get_user(CHAT)["asset_pref"] == "stocks"  # unbekannt → Fallback Aktien


def test_dropdown_lists_all_asset_classes():
    fresh()
    r = _client().get("/app")
    for label in ("Aktien", "ETFs", "Krypto", "Rohstoffe"):
        assert label in r.text


def test_lab_page_shows_daily_schedule_and_manual_start_for_admin(monkeypatch):
    fresh()
    monkeypatch.setattr(webapp.config, "ADMIN_CHAT_ID", CHAT)
    monkeypatch.setattr(webapp.config, "LAB_DAILY_OPTIMIZATION", True)
    monkeypatch.setattr(webapp.config, "LAB_DAILY_HOUR", 16)
    monkeypatch.setattr(webapp.config, "LAB_DAILY_MIN", 0)
    r = _client().get("/app/lab")
    assert r.status_code == 200
    assert "1× täglich Mo–Fr um 16:00 Berlin" in r.text
    assert "▶ Manuellen KI-Lauf starten" in r.text
    assert "keine automatische Live-Übernahme" in r.text


def test_lab_page_explains_manual_start_admin_only(monkeypatch):
    fresh()
    monkeypatch.setattr(webapp.config, "ADMIN_CHAT_ID", 999999)
    r = _client().get("/app/lab")
    assert r.status_code == 200
    assert "▶ Manueller Start nur für Admin" in r.text


# ── On-Demand-Signal-Scan + 7-Tage-Chart ────────────────────────────────────

def test_sparkline_points_and_direction():
    from stockbot.web import webapp
    assert webapp._sparkline([1]) is None                 # zu wenig Daten
    up = webapp._sparkline([1, 2, 3])
    assert up["up"] is True and up["points"].count(",") == 3
    assert webapp._sparkline([3, 2, 1])["up"] is False


def test_scan_populates_cards_and_accept_starts_trade(monkeypatch):
    fresh()
    from stockbot.market import analyzer, asset_classes
    monkeypatch.setattr(asset_classes.universes, "get_tickers", lambda region, auto=True: ["NVDA"])
    sig = {"ticker": "NVDA", "direction": "long", "price": 100.0, "strength": 72.0,
           "rsi_comment": "32.1 — Überverkauft 📉", "macd_comment": "Bullish Crossover ✅",
           "trend_comment": "Aufwärtstrend 📈", "stop_loss": 95.0, "take_profit": 110.0}
    monkeypatch.setattr(analyzer, "analyze_universe", lambda tickers, profile=None: [sig])
    monkeypatch.setattr(analyzer, "price_history_batch",
                        lambda tickers, days=7: {"NVDA": {"dates": [], "closes": [1, 2, 3, 4, 5, 6, 7]}})
    c = _client()
    c.post("/app/scan", follow_redirects=False)
    r = c.get("/app")
    assert "Angeforderte Signale" in r.text and "NVDA" in r.text
    assert "Bullish Crossover" in r.text and "<polyline" in r.text       # volle Infos + Mini-Chart
    c.post("/app/scan/accept", data={"ticker": "NVDA"}, follow_redirects=False)
    act = db.get_active_trades(CHAT)
    assert act and act[0]["ticker"] == "NVDA"


def test_scan_with_asset_switches_class_and_shows_it(monkeypatch):
    """Auswahl + Anfordern in einem Schritt: POST /app/scan mit asset=crypto persistiert die
    Klasse UND scannt sie (kein separater Klick/JS nötig)."""
    fresh()
    from stockbot.market import analyzer
    monkeypatch.setattr(analyzer, "analyze_universe",
                        lambda tickers, profile=None: [{"ticker": tickers[0], "direction": "long",
                                                        "price": 50.0, "strength": 80.0}])
    monkeypatch.setattr(analyzer, "price_history_batch", lambda tickers, days=7: {})
    c = _client()
    c.post("/app/scan", data={"asset": "crypto"}, follow_redirects=False)
    assert db.get_user(CHAT)["asset_pref"] == "crypto"
    r = c.get("/app")
    assert "BTC-USD" in r.text and "Krypto" in r.text and "Alle" in r.text   # Dropdown hat 'Alle'


def test_scan_all_merges_classes_by_strength(monkeypatch):
    fresh()
    from stockbot.market import analyzer, asset_classes
    monkeypatch.setattr(asset_classes.universes, "get_tickers", lambda region, auto=True: ["NVDA"])
    monkeypatch.setattr(analyzer, "analyze_universe",
                        lambda tickers, profile=None: [{"ticker": t, "direction": "long", "price": 10.0,
                                                        "strength": 95.0 if t == "BTC-USD" else 60.0}
                                                       for t in tickers])
    monkeypatch.setattr(analyzer, "price_history_batch", lambda tickers, days=7: {})
    c = _client()
    c.post("/app/scan", data={"asset": "all"}, follow_redirects=False)
    assert db.get_user(CHAT)["asset_pref"] == "all"
    r = c.get("/app")
    assert "BTC-USD" in r.text and "Krypto" in r.text          # stärkster Treffer quer über alle Klassen


def test_scan_applies_user_sl_tp_mode(monkeypatch):
    """Der gewählte SL/TP-Modus (hier passiv) wirkt auf die angeforderten Signale."""
    fresh()
    db.set_sl_tp_mode(CHAT, "passiv")        # (1.0, 1.5)
    from stockbot.market import analyzer, asset_classes
    monkeypatch.setattr(asset_classes.universes, "get_tickers", lambda region, auto=True: ["NVDA"])
    monkeypatch.setattr(analyzer, "analyze_universe",
                        lambda tickers, profile=None: [{"ticker": "NVDA", "direction": "long",
                                                        "price": 100.0, "atr": 2.0, "strength": 70.0,
                                                        "stop_loss": 80.0, "take_profit": 140.0}])
    monkeypatch.setattr(analyzer, "price_history_batch", lambda tickers, days=7: {})
    c = _client()
    c.post("/app/scan", data={"asset": "stocks"}, follow_redirects=False)
    r = c.get("/app")
    assert "$98.00" in r.text and "$103.00" in r.text       # passiv: SL 98 / TP 103 statt 80/140


def test_active_trades_asset_filter():
    fresh()
    for tk in ("NVDA", "BTC-USD"):
        db.add_pending(CHAT, {"ticker": tk, "direction": "long", "price": 100.0, "leverage": 1.0}, 1)
        db.activate_trade(CHAT, tk)
    c = _client()
    all_text = c.get("/app").text
    assert "NVDA" in all_text and "BTC-USD" in all_text
    crypto = c.get("/app?atf=crypto").text
    assert "BTC-USD" in crypto and "NVDA" not in crypto      # gefiltert auf Krypto


def _write_reports(d):
    """Schreibt synthetische Report-JSONs (Overall + 105er-Matrix-Struktur) nach d."""
    import json as _json
    LEVS, MODES = [1, 2], ["passiv", "normal"]
    STRATS = [{"key": "breakout", "label": "Breakout"}, {"key": "ma_trend", "label": "MA-Trend"}]
    meta = {"generated_at": "2026-06-14 00:00 UTC", "region": "sp500", "years": 2,
            "n_tickers": 500, "trade_size_usd": 1000.0, "portfolio_top_n": 10,
            "start_capital_usd": 10000.0, "universe": "voll"}

    def row(key, label, trades, pnl, **extra):
        inv = trades * 1000
        return {"key": key, "label": label, "trades": trades, "win_rate": 55.0, "profit_factor": 1.5,
                "total_pnl_eur": pnl, "max_drawdown_pct": 5.0, "expectancy": 1.0,
                "invested_capital": inv, "end_capital": inv + pnl,
                "return_pct": (pnl / inv * 100) if inv else None,
                "avg_hold_days": 3.2, "liquidations": 0, **extra}

    Path(d, "strategies.json").write_text(_json.dumps({**meta, "rows": [
        row("breakout", "Breakout", 100, 5000), row("ma_trend", "MA-Trend", 80, 2000),
        row("buyhold_sp500", "Buy & Hold S&P 500", 1, 1500)]}), encoding="utf-8")

    matrix_rows = []
    for s in STRATS:
        for lev in LEVS:
            for mode in MODES:
                matrix_rows.append(row(s["key"], s["label"], 50, 1000,
                                       mode=mode, leverage=lev, liquidations=(2 if lev > 1 else 0)))
    Path(d, "matrix.json").write_text(_json.dumps({**meta, "modes": MODES, "leverages": LEVS,
                                                   "strategies": STRATS, "rows": matrix_rows}), encoding="utf-8")

    curves = {}
    for s in STRATS:
        for lev in LEVS:
            for mode in MODES:
                curves[f"{s['key']}|{lev}|{mode}"] = [["2024-01-02", 10000.0], ["2026-01-02", 11000.0]]
    Path(d, "equity.json").write_text(_json.dumps({"start_capital": 10000.0, "start": "2024-01-02",
                                                   "end": "2026-01-02", "curves": curves,
                                                   "benchmark": [["2024-01-02", 10000.0], ["2026-01-02", 10800.0]]}),
                                      encoding="utf-8")


def test_reports_tab_offers_trading_data_export():
    fresh()
    r = _client().get("/app/reports")
    assert r.status_code == 200
    assert "Trading-Daten" in r.text and "exportieren" in r.text
    assert "/app/backtest/export?format=csv" in r.text
    assert "/app/backtest/export?format=json" in r.text
    assert "kind=events" in r.text                     # Status-Events-Export angeboten


def test_backtest_export_trading_data_csv_and_json():
    fresh()
    db.add_pending(CHAT, {"ticker": "AAPL", "direction": "long", "price": 100.0,
                          "strength": 71.5, "leverage": 2.0,
                          "stop_loss": 95.0, "take_profit": 110.0,
                          "strategy": "breakout"}, 1)
    db.activate_trade(CHAT, "AAPL")
    db.close_all(CHAT, [{"ticker": "AAPL", "exit": 108.0, "pnl_eur": 8.0, "pnl_pct": 8.0}])
    c = _client()

    csv_res = c.get("/app/backtest/export?format=csv")
    assert csv_res.status_code == 200
    assert csv_res.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in csv_res.headers["content-disposition"]
    assert "ticker,direction,status" in csv_res.text
    assert "AAPL,long,closed" in csv_res.text
    assert "breakout" in csv_res.text

    json_res = c.get("/app/backtest/export?format=json")
    assert json_res.status_code == 200
    payload = json_res.json()
    assert payload["count"] == 1
    assert payload["trades"][0]["ticker"] == "AAPL"
    assert payload["trades"][0]["signal"]["strength"] == 71.5


def test_reports_tab_without_data_shows_hint(tmp_path, monkeypatch):
    fresh()
    from stockbot import paths
    monkeypatch.setattr(paths, "REPORTS_DIR", tmp_path)     # leeres Verzeichnis
    r = _client().get("/app/reports")
    assert r.status_code == 200 and "Reports" in r.text and "Noch keine Reports" in r.text


def test_reports_overall_and_matrix_columns(tmp_path, monkeypatch):
    fresh()
    from stockbot import paths
    monkeypatch.setattr(paths, "REPORTS_DIR", tmp_path)
    _write_reports(tmp_path)
    r = _client().get("/app/reports")
    assert r.status_code == 200
    for col in ("Overall View", "Eingesetzt", "Endkapital", "Rendite%",
                "Matrix", "Ø&nbsp;Halten", "Liquid.", "Kapitalpool", "10,000 USD"):
        assert col in r.text
    assert "8 von 8 Analysen" in r.text          # 2 Strat × 2 Hebel × 2 Modi
    assert 'class="sortable-table"' in r.text and "data-sort=" in r.text   # sortierbare Spalten


def test_reports_three_multiselect_filters(tmp_path, monkeypatch):
    fresh()
    from stockbot import paths
    monkeypatch.setattr(paths, "REPORTS_DIR", tmp_path)
    _write_reports(tmp_path)
    c = _client()
    assert "4 von 8 Analysen" in c.get("/app/reports?strat=breakout").text          # nur 1 Strategie
    assert "4 von 8 Analysen" in c.get("/app/reports?lev=1").text                    # nur Hebel 1
    assert "2 von 8 Analysen" in c.get("/app/reports?strat=breakout&lev=1").text     # Strat × Hebel
    assert "1 von 8 Analysen" in c.get("/app/reports?strat=breakout&lev=1&mode=passiv").text  # alle drei


def test_reports_equity_endpoint(tmp_path, monkeypatch):
    fresh()
    from stockbot import paths
    monkeypatch.setattr(paths, "REPORTS_DIR", tmp_path)
    _write_reports(tmp_path)
    c = _client()
    # Zeilen sind klickbar (Markup), und die Equity-Kurve ist abrufbar
    assert 'class="matrow"' in c.get("/app/reports").text
    r = c.get("/app/reports/equity?key=breakout&lev=1&mode=passiv")
    assert r.status_code == 200 and r.json()["points"][0] == ["2024-01-02", 10000.0]
    assert r.json()["start_capital"] == 10000.0
    assert r.json()["benchmark"][-1] == ["2026-01-02", 10800.0]      # S&P-500-Referenz mitgeliefert
    assert c.get("/app/reports/equity?key=nope&lev=9&mode=x").status_code == 404


def test_reports_shows_sp500_benchmark_row(tmp_path, monkeypatch):
    fresh()
    from stockbot import paths
    monkeypatch.setattr(paths, "REPORTS_DIR", tmp_path)
    _write_reports(tmp_path)
    assert "Buy &amp; Hold S&amp;P 500" in _client().get("/app/reports").text   # Overall-Benchmark-Zeile


def test_scan_accept_expired_signal_is_safe():
    fresh()
    c = _client()
    r = c.post("/app/scan/accept", data={"ticker": "GHOST"}, follow_redirects=False)
    assert "abgelaufen" in r.headers["location"]
    assert db.get_active_trades(CHAT) == []


# ── 15-Min-Fenster (optional) & Signal-Annahme ───────────────────────────────

def test_signal_window_toggle_via_web():
    fresh()
    c = _client()
    assert db.get_user(CHAT)["signal_window"] is False          # Default: dauerhaft annehmbar
    c.post("/app/settings/set", data={"action": "set_window", "value": "1"}, follow_redirects=False)
    assert db.get_user(CHAT)["signal_window"] is True
    c.post("/app/settings/set", data={"action": "set_window", "value": "0"}, follow_redirects=False)
    assert db.get_user(CHAT)["signal_window"] is False


def test_signal_card_window_is_optional():
    from stockbot.tgbot import bot
    sig = {"ticker": "NVDA", "direction": "long", "strength": 70.0, "price": 100.0, "rsi": 40.0,
           "rsi_comment": "x", "macd_comment": "y", "trend_comment": "z", "volume_comment": "v",
           "sr_comment": "-", "weekly_comment": "-"}
    perm, _ = bot._signal_card(dict(sig), 100.0, True, None)
    assert "Jederzeit annehmbar" in perm and "innerhalb von" not in perm
    win, _ = bot._signal_card(dict(sig), 100.0, True, 15)
    assert "innerhalb von 15 Minuten" in win


# ── Mitteilungen ────────────────────────────────────────────────────────────

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


# ── Sicherheit: Header & Cookie-Flags ───────────────────────────────────────

def test_security_headers_present():
    fresh()
    c = TestClient(__import__("stockbot.web.dashboard", fromlist=["app"]).app)
    r = c.get("/login")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "content-security-policy" in r.headers


def test_hsts_only_when_enabled():
    fresh()
    from stockbot import config
    orig = config.HSTS_ENABLED
    try:
        config.HSTS_ENABLED = False
        c = TestClient(__import__("stockbot.web.dashboard", fromlist=["app"]).app)
        assert "strict-transport-security" not in c.get("/login").headers
        config.HSTS_ENABLED = True
        c2 = TestClient(__import__("stockbot.web.dashboard", fromlist=["app"]).app)
        assert "strict-transport-security" in c2.get("/login").headers
    finally:
        config.HSTS_ENABLED = orig


def test_session_cookie_secure_flag_follows_config():
    tok = fresh()
    from stockbot import config
    orig = config.COOKIE_SECURE
    try:
        config.COOKIE_SECURE = True
        c = TestClient(__import__("stockbot.web.dashboard", fromlist=["app"]).app)
        r = c.get(f"/auth/token?token={tok}", follow_redirects=False)
        assert "secure" in r.headers.get("set-cookie", "").lower()
    finally:
        config.COOKIE_SECURE = orig


# ── CSRF / Rate-Limit / Session-Hygiene ──────────────────────────────────────

def test_csrf_blocks_cross_origin_post():
    fresh()
    c = _client()
    # gleicher Origin → ok
    ok = c.post("/app/asset", data={"asset": "stocks"},
                headers={"origin": "http://testserver"}, follow_redirects=False)
    assert ok.status_code == 303
    # fremder Origin → 403
    bad = c.post("/app/asset", data={"asset": "stocks"},
                 headers={"origin": "http://evil.example"}, follow_redirects=False)
    assert bad.status_code == 403


def test_csrf_tolerates_opaque_and_missing_origin():
    """Telegram-In-App-Browser/Sandbox senden teils 'Origin: null' oder gar keins — das darf
    legitime Nutzer NICHT blockieren (SameSite=lax schützt weiter)."""
    fresh()
    c = _client()
    assert c.post("/app/asset", data={"asset": "stocks"},
                  headers={"origin": "null"}, follow_redirects=False).status_code == 303
    assert c.post("/app/asset", data={"asset": "stocks"},
                  follow_redirects=False).status_code == 303


def test_rate_limit_blocks_after_many_attempts():
    fresh()
    from stockbot.web import auth
    auth._auth_hits.clear()
    assert all(auth.rate_ok("ip:test", limit=3, window=60) for _ in range(3))
    assert auth.rate_ok("ip:test", limit=3, window=60) is False


def test_logout_all_clears_all_sessions():
    fresh()
    db.create_session(CHAT)                       # zweite Session „auf anderem Gerät"
    c = _client()                                 # legt dritte Session an
    c.post("/logout/all", headers={"origin": "http://testserver"}, follow_redirects=False)
    assert c.get("/app", follow_redirects=False).status_code == 303   # ausgeloggt


def test_delete_expired_sessions():
    fresh()
    db.create_session(CHAT, days=-1)              # bereits abgelaufen
    fresh_tok = db.create_session(CHAT, days=30)
    removed = db.delete_expired_sessions()
    assert removed == 1
    assert db.user_id_for_session(fresh_tok) == CHAT


# ── Telegram-Login-HMAC ─────────────────────────────────────────────────────

# ── Ablehnungsgründe im UI sichtbar (PLAN_CHECKLIST.md Phase 3) ─────────────

def test_broker_status_label_covers_leverage_blocked():
    # leverage_blocked wird live gesetzt (webapp.py/bot.py bei TSAFE-002-Ablehnung) —
    # ohne Label-Eintrag würde die UI nur den rohen Code zeigen statt eines lesbaren Grunds.
    assert _dashboard.broker_status_label("leverage_blocked") == \
        "Durch Risikoregel blockiert (Hebel über Maximum)"


def test_broker_status_label_falls_back_for_unknown_code():
    assert _dashboard.broker_status_label("some_new_code") == "Some New Code"


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
