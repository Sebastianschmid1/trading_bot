"""
Tests für Smart-Money-Score, Cache, Re-Ranking und /top5trade.
Laufen offline — yfinance & Telegram werden gemockt.

Lauf:  python test_smartmoney.py   oder   pytest test_smartmoney.py
"""

import sys
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import db
import smartmoney

CHAT = 6262


def fresh_cache():
    smartmoney.SCAN_CACHE = Path(tempfile.mkdtemp(prefix="smtest_")) / "cache.json"


def fresh_db():
    d = tempfile.mkdtemp(prefix="smtest_")
    db.DB_FILE = Path(d) / "test.db"
    db.init_db()


# Roh-Datensätze (erfundene Testdaten — hier explizit erlaubt)
RAW_STRONG_BUY = {
    "insider_net": 15060, "insider_buys": 16, "insider_sells": 6,
    "insider_buy_shares": 412819, "insider_sell_shares": 397759,
    "inst_avg_change": 0.05, "inst_added": 7, "inst_reduced": 3, "inst_total": 10,
    "inst_pct": 0.65, "largest_buy": {"value": 1000000.0, "date": "2026-05-01"},
}
RAW_STRONG_SELL = {
    "insider_net": -499000, "insider_buys": 2, "insider_sells": 12,
    "insider_buy_shares": 1000, "insider_sell_shares": 500000,
    "inst_avg_change": -0.10, "inst_added": 1, "inst_reduced": 8, "inst_total": 10,
    "inst_pct": 0.40, "largest_buy": None,
}
RAW_EMPTY = {
    "insider_net": None, "insider_buys": None, "insider_sells": None,
    "insider_buy_shares": None, "insider_sell_shares": None,
    "inst_avg_change": None, "inst_added": None, "inst_reduced": None, "inst_total": None,
    "inst_pct": None, "largest_buy": None,
}
RAW_ONLY_INST = {
    **RAW_EMPTY,
    "inst_avg_change": 0.10, "inst_added": 9, "inst_reduced": 1, "inst_total": 10, "inst_pct": 0.70,
}


# ── Score-Logik ──────────────────────────────────────────────────────────────

def test_strong_buy_scores_higher_than_sell():
    buy = smartmoney.compute_score(RAW_STRONG_BUY)
    sell = smartmoney.compute_score(RAW_STRONG_SELL)
    assert buy is not None and sell is not None
    assert buy["score"] > sell["score"]
    assert buy["score"] >= 55
    assert sell["score"] <= 30
    assert 1 <= buy["stars"] <= 5


def test_missing_data_returns_none():
    assert smartmoney.compute_score(RAW_EMPTY) is None


def test_partial_only_institutions_still_scores():
    s = smartmoney.compute_score(RAW_ONLY_INST)
    assert s is not None
    assert s["score"] > 50          # starke Institutionen-Aufstockung, Insider neutral


# ── Scan + Cache + get_top ───────────────────────────────────────────────────

def _patch_fetch(mapping):
    """Ersetzt smartmoney.fetch_raw durch eine Lookup-Funktion (kein Netz)."""
    def fake(ticker):
        if mapping[ticker] == "RAISE":
            raise RuntimeError("yfinance kaputt")
        return mapping[ticker]
    return fake


def test_scan_writes_cache_and_get_top_sorts():
    fresh_cache()
    orig = smartmoney.fetch_raw
    smartmoney.fetch_raw = _patch_fetch({
        "AAA": RAW_STRONG_BUY, "BBB": RAW_STRONG_SELL, "CCC": RAW_ONLY_INST,
    })
    try:
        smartmoney.scan_universe(["AAA", "BBB", "CCC"])
        assert smartmoney.SCAN_CACHE.exists()
        assert smartmoney.get_score("AAA") is not None
        top = smartmoney.get_top(["AAA", "BBB", "CCC"], 2)
        assert len(top) == 2
        assert top[0]["ticker"] == "AAA"           # stärkster Käufer oben
        assert top[0]["score"] >= top[1]["score"]
        assert smartmoney.last_scanned() is not None
    finally:
        smartmoney.fetch_raw = orig


def test_scan_skips_failing_ticker():
    fresh_cache()
    orig = smartmoney.fetch_raw
    smartmoney.fetch_raw = _patch_fetch({"AAA": RAW_STRONG_BUY, "BAD": "RAISE"})
    try:
        smartmoney.scan_universe(["AAA", "BAD"])
        assert smartmoney.get_score("AAA") is not None
        assert smartmoney.get_score("BAD") is None     # übersprungen, kein Crash
    finally:
        smartmoney.fetch_raw = orig


def test_empty_cache_get_top_returns_empty():
    fresh_cache()
    assert smartmoney.get_top(["AAA"], 5) == []


# ── Re-Ranking ───────────────────────────────────────────────────────────────

def test_rank_promotes_high_smart_money():
    fresh_cache()
    orig = smartmoney.fetch_raw
    # HIGH: schwächeres technisches Signal, aber Top-Smart-Money; LOW: stark technisch, mies SM
    smartmoney.fetch_raw = _patch_fetch({"HIGH": RAW_STRONG_BUY, "LOW": RAW_STRONG_SELL})
    try:
        smartmoney.scan_universe(["HIGH", "LOW"])
        # gleiche technische Stärke → Smart-Money entscheidet die Reihenfolge
        signals = [
            {"ticker": "LOW", "strength": 4, "rsi": 50},
            {"ticker": "HIGH", "strength": 4, "rsi": 50},
        ]
        ranked = smartmoney.rank(signals, top_n=2)
        assert ranked[0]["ticker"] == "HIGH"          # höherer Smart-Money-Score → oben
        assert ranked[0]["smart_money"] is not None
    finally:
        smartmoney.fetch_raw = orig


def test_rank_without_cache_keeps_technical_order():
    fresh_cache()                                      # leerer Cache → kein Blend
    signals = [
        {"ticker": "AAA", "strength": 5, "rsi": 50},
        {"ticker": "BBB", "strength": 3, "rsi": 50},
    ]
    ranked = smartmoney.rank(signals, top_n=2)
    assert [s["ticker"] for s in ranked] == ["AAA", "BBB"]
    assert ranked[0]["smart_money"] is None


# ── /top5trade Befehl ────────────────────────────────────────────────────────

def _registered():
    fresh_db()
    db.get_or_create_user(CHAT, "tester")
    db.save_profile(CHAT, trade_size_eur=25.0)


def _fake_update():
    update = MagicMock()
    update.effective_chat.id = CHAT
    update.message.reply_text = AsyncMock()
    return update


def test_top5trade_empty_cache_triggers_scan():
    import bot
    _registered()
    fresh_cache()
    ctx = MagicMock()
    ctx.job_queue = MagicMock()
    orig_get = bot.universes.get_tickers
    bot.universes.get_tickers = lambda region: ["AAPL", "MSFT"]
    try:
        update = _fake_update()
        asyncio.run(bot.cmd_top5trade(update, ctx))
        ctx.job_queue.run_once.assert_called()         # Hintergrund-Scan angestoßen
        update.message.reply_text.assert_awaited()     # Hinweis gesendet
    finally:
        bot.universes.get_tickers = orig_get


def test_top5trade_with_cache_lists_tickers():
    import bot
    _registered()
    fresh_cache()
    orig_fetch = smartmoney.fetch_raw
    smartmoney.fetch_raw = _patch_fetch({"AAPL": RAW_STRONG_BUY, "MSFT": RAW_ONLY_INST})
    orig_get = bot.universes.get_tickers
    bot.universes.get_tickers = lambda region: ["AAPL", "MSFT"]
    try:
        smartmoney.scan_universe(["AAPL", "MSFT"])
        update = _fake_update()
        ctx = MagicMock()
        asyncio.run(bot.cmd_top5trade(update, ctx))
        sent = " ".join(str(c.args[0]) for c in update.message.reply_text.await_args_list)
        assert "AAPL" in sent
    finally:
        smartmoney.fetch_raw = orig_fetch
        bot.universes.get_tickers = orig_get


# ── Runner ───────────────────────────────────────────────────────────────────

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
