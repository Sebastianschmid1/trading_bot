"""
Tests für die persönliche Watchlist:
  - DB-Schicht: add/remove/parse + Migration (Spalte ergänzen)
  - lookup.validate_ticker / search_symbols (yfinance gemockt)
  - broker.get_asset_info (Alpaca-Client gemockt)
  - llm_ranker.suggest_tickers ("Meinten Sie?"-Fallback, Anthropic-Client gemockt)
  - Bot-Commands /watchadd, /watchdel (Telegram/yfinance/LLM gemockt)
  - send_daily_signals: Watchlist-Treffer erscheint auch außerhalb von top_n

Lauf:  python -m tests.test_watchlist   oder   pytest tests/test_watchlist.py
Alle Tests laufen offline.
"""

import sys
import asyncio
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from stockbot.core import db
from stockbot.tgbot import bot
from stockbot.market import lookup
from stockbot.broker import client as broker
from stockbot.ai import llm_ranker

CHAT = 6161


def fresh_db():
    d = tempfile.mkdtemp(prefix="wltest_")
    db.DB_FILE = Path(d) / "test.db"
    db.init_db()


def _complete_user(top_n=5, auto_universe=0):
    db.get_or_create_user(CHAT, "tester")
    db.save_profile(CHAT, trade_size_eur=25.0)
    with db._connect() as conn:
        conn.execute("UPDATE users SET top_n_signals = ?, auto_universe = ? WHERE user_id = ?",
                     (top_n, auto_universe, CHAT))


def _cmd(args):
    upd = MagicMock()
    upd.effective_chat.id = CHAT
    upd.message.reply_text = AsyncMock()
    ctx = MagicMock()
    ctx.args = args
    return upd, ctx


def _replies(upd):
    return " ".join(str(c.args[0]) for c in upd.message.reply_text.call_args_list if c.args)


# ── DB-Schicht ───────────────────────────────────────────────────────────────

def test_watchlist_add_remove_roundtrip_and_dedupe():
    fresh_db()
    db.get_or_create_user(CHAT)
    assert db.get_user(CHAT)["watchlist"] == []
    db.add_watchlist_tickers(CHAT, ["aapl", "MSFT", "aapl"])   # Klein/Doppelt
    assert db.get_user(CHAT)["watchlist"] == ["AAPL", "MSFT"]  # großgeschrieben, sortiert, dedupe
    db.add_watchlist_tickers(CHAT, ["spy"])
    assert db.get_user(CHAT)["watchlist"] == ["AAPL", "MSFT", "SPY"]
    db.remove_watchlist_ticker(CHAT, "msft")
    assert db.get_user(CHAT)["watchlist"] == ["AAPL", "SPY"]


def test_user_watchlist_helper_defaults_empty():
    fresh_db()
    db.get_or_create_user(CHAT)
    assert bot._user_watchlist(db.get_user(CHAT)) == []


def test_migration_adds_watchlist_column_to_old_db():
    d = tempfile.mkdtemp(prefix="wlmig_")
    db.DB_FILE = Path(d) / "old.db"
    # alte DB ohne watchlist-Spalte simulieren
    with sqlite3.connect(db.DB_FILE) as conn:
        conn.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, username TEXT)")
        conn.commit()
    db.init_db()   # _migrate ergänzt fehlende Spalten
    with db._connect() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    assert "watchlist" in cols


# ── lookup (yfinance gemockt) ────────────────────────────────────────────────

class _FastInfo:
    def __init__(self, price): self.last_price = price
class _LkTicker:
    def __init__(self, price, info): self._price, self._info = price, info
    @property
    def fast_info(self): return _FastInfo(self._price)
    @property
    def info(self): return self._info
class _FakeYf:
    def __init__(self, price=100.0, info=None, quotes=None):
        self._price, self._info, self._quotes = price, (info or {}), (quotes or [])
    def Ticker(self, sym): return _LkTicker(self._price, self._info)
    def Search(self, q, max_results=5):
        quotes = self._quotes
        class _S:  # noqa: E306
            pass
        s = _S(); s.quotes = quotes
        return s


def _with_yf(fake, fn):
    orig = lookup.yf
    lookup.yf = fake
    try:
        return fn()
    finally:
        lookup.yf = orig


def test_validate_ticker_ok_equity_and_etf():
    eq = _with_yf(_FakeYf(185.0, {"shortName": "Apple Inc.", "quoteType": "EQUITY"}),
                  lambda: lookup.validate_ticker("aapl"))
    assert eq == {"ok": True, "symbol": "AAPL", "name": "Apple Inc.",
                  "quote_type": "EQUITY", "price": 185.0}
    etf = _with_yf(_FakeYf(500.0, {"shortName": "SPDR S&P 500", "quoteType": "ETF"}),
                   lambda: lookup.validate_ticker("SPY"))
    assert etf["ok"] and etf["quote_type"] == "ETF"


def test_validate_ticker_not_found_when_no_price():
    res = _with_yf(_FakeYf(None), lambda: lookup.validate_ticker("NOPE"))
    assert res == {"ok": False, "symbol": "NOPE"}


def test_search_symbols_maps_quotes():
    fake = _FakeYf(quotes=[{"symbol": "aapl", "shortname": "Apple Inc.", "quoteType": "EQUITY"}])
    hits = _with_yf(fake, lambda: lookup.search_symbols("Apple"))
    assert hits and hits[0]["symbol"] == "AAPL" and hits[0]["name"] == "Apple Inc."


def test_search_symbols_empty_on_no_results():
    assert _with_yf(_FakeYf(quotes=[]), lambda: lookup.search_symbols("zzzzz")) == []


# ── broker.get_asset_info (Alpaca-Client gemockt) ────────────────────────────

class _Asset:
    def __init__(self, tradable): self.tradable, self.asset_class, self.symbol = tradable, "us_equity", "AAPL"
class _FakeAlpaca:
    def __init__(self, tradable=True, raise_=False): self._t, self._raise = tradable, raise_
    def get_asset(self, sym):
        if self._raise:
            raise RuntimeError("unknown symbol")
        return _Asset(self._t)


def test_get_asset_info_tradable_and_not():
    assert broker.get_asset_info("AAPL", _FakeAlpaca(tradable=True)) == {
        "ok": True, "symbol": "AAPL", "tradable": True, "asset_class": "us_equity"}
    assert broker.get_asset_info("AAPL", _FakeAlpaca(tradable=False))["tradable"] is False


def test_get_asset_info_handles_unknown_and_no_client():
    assert broker.get_asset_info("ZZZ", _FakeAlpaca(raise_=True))["ok"] is False
    # ohne Client + Alpaca global aus → ok False (kein Netz)
    orig = broker.config.ALPACA_ENABLED
    broker.config.ALPACA_ENABLED = False
    try:
        assert broker.get_asset_info("AAPL")["ok"] is False
    finally:
        broker.config.ALPACA_ENABLED = orig


# ── llm_ranker.suggest_tickers (Anthropic-Client gemockt) ────────────────────

class _Block:
    def __init__(self, text): self.type, self.text = "text", text
class _Resp:
    def __init__(self, text): self.content = [_Block(text)]
class _FakeAnthropic:
    def __init__(self, text): self._t = text
    @property
    def messages(self):
        outer = self
        class _M:  # noqa: E306
            def create(self, **k): return _Resp(outer._t)
        return _M()


def test_suggest_tickers_parses_and_caps_three():
    cli = _FakeAnthropic('{"tickers": ["aapl", "aaple", "appn", "extra"]}')
    assert llm_ranker.suggest_tickers("Apple", client=cli) == ["AAPL", "AAPLE", "APPN"]


def test_suggest_tickers_empty_without_client():
    assert llm_ranker.suggest_tickers("Apple", client=None) == []   # LLM aus → []


# ── Bot-Commands ─────────────────────────────────────────────────────────────

def test_cmd_watchadd_valid_symbol_added():
    fresh_db()
    _complete_user()
    bot.ALPACA_ENABLED = False
    monkey = bot.lookup.validate_ticker
    bot.lookup.validate_ticker = lambda s: {"ok": True, "symbol": "AAPL",
                                            "name": "Apple Inc.", "quote_type": "EQUITY", "price": 185.0}
    try:
        upd, ctx = _cmd(["aapl"])
        asyncio.run(bot.cmd_watchadd(upd, ctx))
    finally:
        bot.lookup.validate_ticker = monkey
    assert db.get_user(CHAT)["watchlist"] == ["AAPL"]
    assert "AAPL" in _replies(upd) and "✅" in _replies(upd)


def test_cmd_watchadd_unknown_suggests_alternatives():
    fresh_db()
    _complete_user()
    bot.ALPACA_ENABLED = False
    o_val, o_search = bot.lookup.validate_ticker, bot.lookup.search_symbols
    bot.lookup.validate_ticker = lambda s: {"ok": False, "symbol": s}
    bot.lookup.search_symbols = lambda s, limit=5: [{"symbol": "AAPL", "name": "Apple", "quote_type": "EQUITY"}]
    try:
        upd, ctx = _cmd(["appel"])
        asyncio.run(bot.cmd_watchadd(upd, ctx))
    finally:
        bot.lookup.validate_ticker, bot.lookup.search_symbols = o_val, o_search
    assert db.get_user(CHAT)["watchlist"] == []        # nichts hinzugefügt
    assert "Meinten Sie" in _replies(upd) and "AAPL" in _replies(upd)


def test_cmd_watchadd_unknown_no_suggestions_via_llm():
    fresh_db()
    _complete_user()
    bot.ALPACA_ENABLED = False
    o_val, o_search, o_sug = bot.lookup.validate_ticker, bot.lookup.search_symbols, bot.llm_ranker.suggest_tickers
    bot.lookup.validate_ticker = lambda s: {"ok": False, "symbol": s}
    bot.lookup.search_symbols = lambda s, limit=5: []
    bot.llm_ranker.suggest_tickers = lambda s: []
    try:
        upd, ctx = _cmd(["zzzzz"])
        asyncio.run(bot.cmd_watchadd(upd, ctx))
    finally:
        bot.lookup.validate_ticker, bot.lookup.search_symbols, bot.llm_ranker.suggest_tickers = o_val, o_search, o_sug
    assert "nicht gefunden" in _replies(upd)


def test_cmd_watchdel_removes():
    fresh_db()
    _complete_user()
    db.add_watchlist_tickers(CHAT, ["AAPL", "MSFT"])
    upd, ctx = _cmd(["aapl"])
    asyncio.run(bot.cmd_watchdel(upd, ctx))
    assert db.get_user(CHAT)["watchlist"] == ["MSFT"]


# ── send_daily_signals: Watchlist-Treffer umgeht top_n ───────────────────────

def _sig(ticker, strength):
    return {"ticker": ticker, "strength": strength, "rsi": 50.0, "direction": "long", "price": 100.0}


def test_send_daily_signals_includes_watchlist_beyond_top_n():
    fresh_db()
    _complete_user(top_n=1, auto_universe=0)          # nur 1 reguläres Signal erlaubt
    db.add_watchlist_tickers(CHAT, ["WATCH"])

    def fake_analyze(tickers, generate=None):
        if set(tickers) == {"WATCH"}:
            return [_sig("WATCH", 50)]                 # Watchlist-Treffer (schwächer)
        return [_sig("BIG1", 90), _sig("BIG2", 80)]    # reguläre Körbe

    sent = []
    async def fake_send_signal(b, chat_id, signal, size, **k):
        sent.append((signal["ticker"], signal.get("watchlist")))
        return True

    o_an, o_send, o_open = bot.analyze_universe, bot.send_signal, bot._us_market_open
    bot.analyze_universe = fake_analyze
    bot.send_signal = fake_send_signal
    bot._us_market_open = lambda *a, **k: True
    ctx = MagicMock()
    ctx.bot.send_message = AsyncMock()
    try:
        asyncio.run(bot.send_daily_signals(ctx))
    finally:
        bot.analyze_universe, bot.send_signal, bot._us_market_open = o_an, o_send, o_open

    tickers_sent = [t for t, _ in sent]
    assert "BIG1" in tickers_sent          # das eine reguläre top_n-Signal
    assert "BIG2" not in tickers_sent      # vom top_n=1 weggeschnitten
    assert "WATCH" in tickers_sent         # Watchlist trotzdem dabei …
    assert ("WATCH", True) in sent         # … und als Watchlist markiert


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
