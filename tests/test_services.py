"""
Tests für die framework-neutrale Service-Schicht (stockbot/services/*).
Diese Logik nutzen Telegram-Bot UND künftig das Web-Backend gemeinsam (Phase 0).

Lauf:  python -m tests.test_services   oder   pytest tests/test_services.py
Alle Tests laufen offline.
"""

import sys
import asyncio  # noqa: F401  (nicht nötig, aber konsistent zu anderen Suiten)
import tempfile
from pathlib import Path

from stockbot.core import db
from stockbot.market import lookup
from stockbot.ai import llm_ranker
from stockbot.services import trades as trade_svc
from stockbot.services import settings as settings_svc
from stockbot.services import watchlist as watchlist_svc

CHAT = 7788


class _FakeYF:
    """yfinance-Ersatz: activate_trade liest fast_info.last_price."""
    def __init__(self, price): self._p = price
    def Ticker(self, t):
        price = self._p
        class _T:  # noqa: E306
            @property
            def fast_info(self):
                class _FI:  # noqa: E306
                    last_price = price
                return _FI()
        return _T()


def fresh_db(price=100.0):
    d = tempfile.mkdtemp(prefix="svctest_")
    db.DB_FILE = Path(d) / "test.db"
    db.init_db()
    db.yf = _FakeYF(price)
    db.get_or_create_user(CHAT, "tester")
    db.save_profile(CHAT, trade_size_eur=100.0)


def _pending(ticker="NVDA", price=100.0, leverage=1.0, strength=70.0):
    db.add_pending(CHAT, {"ticker": ticker, "direction": "long", "price": price,
                          "leverage": leverage, "strength": strength}, 1)


# ── trades-Service ─────────────────────────────────────────────────────────────

def test_accept_and_reject_trade():
    fresh_db()
    _pending("NVDA")
    res = trade_svc.accept_trade(CHAT, "NVDA")
    assert res["ok"] and res["trade"]["status"] == "active"
    # erneut akzeptieren → nicht mehr verfügbar
    assert trade_svc.accept_trade(CHAT, "NVDA")["ok"] is False

    _pending("AAPL")
    assert trade_svc.reject_trade(CHAT, "AAPL") is True
    assert trade_svc.reject_trade(CHAT, "AAPL") is False     # schon abgelehnt


def test_accept_trade_can_wait_for_broker_fill_before_becoming_active():
    fresh_db()
    _pending("NVDA")

    res = trade_svc.accept_trade(CHAT, "NVDA", status="broker_pending")

    assert res["ok"] and res["trade"]["status"] == "broker_pending"
    assert db.get_active_trades(CHAT) == []
    db.mark_broker_pending(CHAT, "NVDA", order_id="ord-1", broker_status="accepted")
    trade = db.get_trade(CHAT, "NVDA")
    assert trade["broker_order_id"] == "ord-1"
    assert trade["broker_status"] == "accepted"

    db.mark_broker_filled(CHAT, "NVDA", broker_status="filled", filled_qty=2, filled_avg_price=101.5)

    trade = db.get_trade(CHAT, "NVDA")
    assert trade["status"] == "active"
    assert trade["entry"] == 101.5
    assert trade["broker_status"] == "filled"
    assert db.get_active_trades(CHAT)[0]["ticker"] == "NVDA"


def test_broker_rejected_trade_does_not_remain_active():
    fresh_db()
    _pending("AAPL")

    res = trade_svc.accept_trade(CHAT, "AAPL", status="broker_pending")
    assert res["ok"]
    db.mark_broker_failed(CHAT, "AAPL", broker_status="rejected")

    trade = db.get_trade(CHAT, "AAPL")
    assert trade["status"] == "broker_failed"
    assert trade["broker_status"] == "rejected"
    assert db.get_active_trades(CHAT) == []


def test_set_pending_leverage_only_while_pending():
    fresh_db()
    _pending("NVDA", leverage=1.0)
    updated = trade_svc.set_pending_leverage(CHAT, "NVDA", 3.0)
    assert updated is not None and updated["signal"]["leverage"] == 3.0
    trade_svc.reject_trade(CHAT, "NVDA")
    assert trade_svc.set_pending_leverage(CHAT, "NVDA", 5.0) is None     # nicht mehr pending


def test_sell_trade_computes_leveraged_pnl_and_closes():
    fresh_db(price=100.0)
    _pending("NVDA", leverage=3.0)
    trade_svc.accept_trade(CHAT, "NVDA")                     # entry = 100 (Fake-Kurs)

    orig = trade_svc.get_current_price
    trade_svc.get_current_price = lambda ticker, fallback: 110.0
    try:
        res = trade_svc.sell_trade(CHAT, "NVDA")
    finally:
        trade_svc.get_current_price = orig

    assert res["ok"] and res["current"] == 110.0
    assert round(res["pnl_pct"], 1) == 10.0
    assert round(res["pnl_eur"], 2) == 30.0                  # 100€ × 10 % × 3
    assert db.get_active_trades(CHAT) == []                  # geschlossen
    # erneuter Verkauf → nicht mehr aktiv
    assert trade_svc.sell_trade(CHAT, "NVDA")["ok"] is False


# ── settings-Service ───────────────────────────────────────────────────────────

def test_apply_setting_dispatch_and_validation():
    fresh_db()
    settings_svc.apply_setting(CHAT, "set_size", "250")
    assert db.get_user(CHAT)["trade_size_eur"] == 250.0
    settings_svc.apply_setting(CHAT, "set_lev", "999")       # wird in db auf max 20 geclamped
    assert db.get_user(CHAT)["leverage"] == 20.0
    settings_svc.apply_setting(CHAT, "set_strat", "adx_trend")
    assert set(db.get_user(CHAT)["strategies"]) == {"standard", "adx_trend"}
    settings_svc.apply_setting(CHAT, "set_size", "keine-zahl")   # ungültig → ignoriert
    assert db.get_user(CHAT)["trade_size_eur"] == 250.0


def test_apply_setting_broker_gated_by_alpaca_ready():
    fresh_db()
    settings_svc.apply_setting(CHAT, "set_broker", "1", alpaca_ready=False)
    assert db.get_user(CHAT)["broker_exec"] is False         # ohne Alpaca kein Effekt
    settings_svc.apply_setting(CHAT, "set_broker", "1", alpaca_ready=True)
    assert db.get_user(CHAT)["broker_exec"] is True


# ── watchlist-Service ──────────────────────────────────────────────────────────

def test_add_to_watchlist_added_without_broker():
    fresh_db()
    o = lookup.validate_ticker
    lookup.validate_ticker = lambda s: {"ok": True, "symbol": "AAPL", "name": "Apple Inc.",
                                        "quote_type": "EQUITY", "price": 185.0}
    try:
        res = watchlist_svc.add_to_watchlist(CHAT, "aapl")   # kein alpaca_client
    finally:
        lookup.validate_ticker = o
    assert res["status"] == "added" and res["asset"] is None
    assert res["watchlist"] == ["AAPL"] and db.get_user(CHAT)["watchlist"] == ["AAPL"]


def test_add_to_watchlist_not_found_uses_search_then_llm():
    fresh_db()
    o_val, o_search, o_sug = lookup.validate_ticker, lookup.search_symbols, llm_ranker.suggest_tickers
    lookup.validate_ticker = lambda s: {"ok": False, "symbol": s}
    # Fall 1: yfinance-Suche liefert Treffer
    lookup.search_symbols = lambda s, limit=5: [{"symbol": "AAPL", "name": "Apple", "quote_type": "EQUITY"}]
    try:
        r1 = watchlist_svc.add_to_watchlist(CHAT, "appel")
        assert r1["status"] == "not_found" and r1["suggestions"] == ["AAPL"]
        # Fall 2: keine Suchtreffer → LLM-Fallback
        lookup.search_symbols = lambda s, limit=5: []
        llm_ranker.suggest_tickers = lambda s: ["MSFT", "APPEL"]
        r2 = watchlist_svc.add_to_watchlist(CHAT, "appel")
        assert r2["suggestions"] == ["MSFT"]                # "APPEL" == Eingabe → herausgefiltert
    finally:
        lookup.validate_ticker, lookup.search_symbols, llm_ranker.suggest_tickers = o_val, o_search, o_sug
    assert db.get_user(CHAT)["watchlist"] == []             # nichts hinzugefügt


def test_remove_from_watchlist():
    fresh_db()
    db.add_watchlist_tickers(CHAT, ["AAPL", "MSFT"])
    assert watchlist_svc.remove_from_watchlist(CHAT, "aapl") == ["MSFT"]


# ── Runner (ohne pytest nutzbar) ─────────────────────────────────────────────

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
