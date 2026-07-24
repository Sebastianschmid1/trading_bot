"""
Automatische Tests für die Signal-Logik (TDD — erst geschrieben, dann implementiert).

Deckt ab:
  #1 Abgelaufene Signale → Telegram-Nachricht wird gelöscht
  #2 Lifecycle-Verkettung: Ankunft (pending) → Kauf (active) → Verkauf (closed) ist EIN Datensatz
  #3 Duplikat-Schutz: pro Aktie/Tag nur ein handelbares Signal
  #4 Geschlossene Börse → deaktivierter "Börse geschlossen"-Button (noop) statt JA/NEIN, kein Trade

Lauf:  python test_signals.py     (eigener Runner)
  oder: pytest test_signals.py
Alle Tests laufen offline (yfinance + Telegram werden gemockt).
"""

import sys
import asyncio
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from stockbot.core import db
from stockbot.core import exchange_calendar
from stockbot.tgbot import bot

CHAT = 4242
SOLD = None  # placeholder


# ── Test-Hilfen (erfundene Testdaten sind hier ausdrücklich erlaubt) ─────────

def fresh_db():
    """Frische, isolierte Test-DB pro Test."""
    d = tempfile.mkdtemp(prefix="bottest_")
    db.DB_FILE = Path(d) / "test.db"
    db.init_db()


class _FakeFastInfo:
    def __init__(self, price): self.last_price = price


class _FakeTicker:
    def __init__(self, price): self._price = price
    @property
    def fast_info(self): return _FakeFastInfo(self._price)


class _FakeYF:
    """Ersetzt yfinance, damit Tests offline laufen."""
    def __init__(self, price): self._price = price
    def Ticker(self, ticker): return _FakeTicker(self._price)


def make_signal(ticker="NVDA", price=100.0):
    return {
        "ticker": ticker, "price": price, "as_of": "2026-06-05",
        "direction": "long", "strength": 4, "rsi": 32.0,
        "rsi_comment": "Überverkauft", "macd_comment": "Bullish",
        "trend_comment": "Auf", "weekly_comment": "Auf", "volume_comment": "Hoch",
        "sr_comment": "Support X · Widerstand Y",
        "stop_loss": price * 0.97, "take_profit": price * 1.05,
        "sl_pct": -3.0, "tp_pct": 5.0, "risk_reward": 1.67,
    }


def fake_bot():
    b = MagicMock()
    b.send_message = AsyncMock(return_value=MagicMock(message_id=111))
    b.delete_message = AsyncMock()
    return b


# ── #3 Duplikat-Schutz pro Aktie ─────────────────────────────────────────────

def test_has_trade_today_false_initially():
    fresh_db()
    assert db.has_trade_today(CHAT, "NVDA") is False


def test_add_pending_creates_once_and_dedups():
    fresh_db()
    assert db.add_pending(CHAT, make_signal("NVDA"), 111) is True
    assert db.has_trade_today(CHAT, "NVDA") is True
    # zweiter Versuch für dieselbe Aktie am selben Tag → kein neuer Datensatz
    assert db.add_pending(CHAT, make_signal("NVDA"), 222) is False
    assert len(db.get_active_trades(CHAT)) == 0  # noch nichts aktiv
    # es existiert genau ein Datensatz, message_id des ersten bleibt erhalten
    t = db.get_trade(CHAT, "NVDA")
    assert t["message_id"] == 111


def test_add_pending_does_not_downgrade_active():
    fresh_db()
    db.yf = _FakeYF(150.0)
    db.add_pending(CHAT, make_signal("NVDA", 100.0), 111)
    db.activate_trade(CHAT, "NVDA")
    assert db.get_trade(CHAT, "NVDA")["status"] == "active"
    # erneutes add_pending darf einen aktiven Trade NICHT auf pending zurücksetzen
    assert db.add_pending(CHAT, make_signal("NVDA"), 222) is False
    assert db.get_trade(CHAT, "NVDA")["status"] == "active"


def test_send_signal_skips_duplicate_ticker():
    fresh_db()
    b = fake_bot()
    s = make_signal("NVDA")

    sent1 = asyncio.run(bot.send_signal(b, CHAT, s, 25.0, market_open=True))
    sent2 = asyncio.run(bot.send_signal(b, CHAT, s, 25.0, market_open=True))

    assert sent1 is True
    assert sent2 is False                       # zweites Signal für NVDA übersprungen
    assert b.send_message.await_count == 1      # nur einmal gesendet


# ── #2 Lifecycle-Verkettung: Ankunft → Kauf → Verkauf (nur prüfen) ───────────

def test_lifecycle_pending_active_closed_is_one_record():
    fresh_db()
    db.yf = _FakeYF(150.0)
    sig = make_signal("NVDA", 100.0)

    # Ankunft
    db.add_pending(CHAT, sig, message_id=111)
    t = db.get_trade(CHAT, "NVDA")
    assert t["status"] == "pending"
    assert t["message_id"] == 111
    assert t["signal"]["ticker"] == "NVDA"
    assert t["entry"] is None

    # Kauf
    activated = db.activate_trade(CHAT, "NVDA")
    assert activated["status"] == "active"
    assert activated["entry"] == 150.0          # echter (gemockter) Kurs beim Kauf
    assert db.get_trade(CHAT, "NVDA")["message_id"] == 111   # gleiche Nachricht/Datensatz

    # Verkauf
    db.close_all(CHAT, [{"ticker": "NVDA", "exit": 165.0, "pnl_eur": 16.25, "pnl_pct": 10.0}])
    closed = db.get_trade(CHAT, "NVDA")
    assert closed["status"] == "closed"
    assert closed["exit"] == 165.0
    assert closed["message_id"] == 111          # über den ganzen Lebenszyklus dieselbe ID
    assert closed["signal"]["ticker"] == "NVDA" # Signal bleibt verknüpft


# ── #1 Abgelaufene Signale → Nachricht wird gelöscht ─────────────────────────

def test_expire_trade_marks_expired_only_when_pending():
    fresh_db()
    db.yf = _FakeYF(150.0)
    db.add_pending(CHAT, make_signal("NVDA"), 111)
    assert db.expire_trade(CHAT, "NVDA") is True
    assert db.get_trade(CHAT, "NVDA")["status"] == "expired"
    # ein bereits aktiver Trade darf nicht "ablaufen"
    db.add_pending(CHAT, make_signal("TSLA"), 222)
    db.activate_trade(CHAT, "TSLA")
    assert db.expire_trade(CHAT, "TSLA") is False
    assert db.get_trade(CHAT, "TSLA")["status"] == "active"


def test_expire_pending_trade_deletes_message():
    fresh_db()
    db.add_pending(CHAT, make_signal("NVDA"), message_id=111)

    ctx = MagicMock()
    ctx.bot = MagicMock()
    ctx.bot.delete_message = AsyncMock()
    ctx.job = MagicMock()
    ctx.job.data = {"chat_id": CHAT, "ticker": "NVDA", "message_id": 111}

    asyncio.run(bot.expire_pending_trade(ctx))

    ctx.bot.delete_message.assert_awaited_once()
    kwargs = ctx.bot.delete_message.await_args.kwargs
    assert kwargs.get("chat_id") == CHAT
    assert kwargs.get("message_id") == 111
    assert db.get_trade(CHAT, "NVDA")["status"] == "expired"


def test_expire_pending_trade_does_not_delete_if_already_active():
    fresh_db()
    db.yf = _FakeYF(150.0)
    db.add_pending(CHAT, make_signal("NVDA"), message_id=111)
    db.activate_trade(CHAT, "NVDA")

    ctx = MagicMock()
    ctx.bot = MagicMock()
    ctx.bot.delete_message = AsyncMock()
    ctx.job = MagicMock()
    ctx.job.data = {"chat_id": CHAT, "ticker": "NVDA", "message_id": 111}

    asyncio.run(bot.expire_pending_trade(ctx))

    ctx.bot.delete_message.assert_not_awaited()  # aktiver Trade bleibt erhalten
    assert db.get_trade(CHAT, "NVDA")["status"] == "active"


# ── #4 Geschlossene Börse → deaktivierter Button, kein Trade ─────────────────

def test_open_market_has_trade_buttons_and_creates_pending():
    fresh_db()
    b = fake_bot()
    jq = MagicMock()
    sent = asyncio.run(bot.send_signal(b, CHAT, make_signal("NVDA"), 25.0, job_queue=jq, market_open=True))

    assert sent is True
    markup = b.send_message.await_args.kwargs["reply_markup"]
    callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    # Seit W7 sind die Buttons opake, serverseitig auflösbare Tokens (t:<token>)
    from stockbot.tgbot import callback_security as cbs
    actions = sorted(cbs.resolve(c[2:], CHAT)[0] for c in callbacks if c.startswith("t:"))
    assert actions == ["accept", "reject"]
    assert db.has_trade_today(CHAT, "NVDA") is True   # handelbarer Trade angelegt
    assert not jq.run_once.called                      # Default: KEIN Ablauf-Job (dauerhaft annehmbar)

    # Nur mit aktiviertem 15-Min-Fenster (expiry_min) wird der Ablauf-Job geplant
    jq2 = MagicMock()
    asyncio.run(bot.send_signal(b, CHAT, make_signal("AAPL"), 25.0,
                                job_queue=jq2, market_open=True, expiry_min=15))
    assert jq2.run_once.called


def test_closed_market_has_disabled_noop_button_and_no_trade():
    fresh_db()
    b = fake_bot()
    jq = MagicMock()
    sent = asyncio.run(bot.send_signal(b, CHAT, make_signal("NVDA"), 25.0, job_queue=jq, market_open=False))

    assert sent is True
    markup = b.send_message.await_args.kwargs["reply_markup"]
    buttons = [btn for row in markup.inline_keyboard for btn in row]
    assert len(buttons) == 1
    assert buttons[0].callback_data.startswith("noop:")
    assert "geschlossen" in buttons[0].text.lower()
    # kein handelbarer Trade angelegt, kein Ablauf-Job
    assert db.has_trade_today(CHAT, "NVDA") is False
    assert not jq.run_once.called


def test_noop_button_does_nothing():
    fresh_db()
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.data = "noop:NVDA"
    update = MagicMock()
    update.callback_query = query
    update.effective_chat.id = CHAT
    ctx = MagicMock()

    asyncio.run(bot.button_handler(update, ctx))

    query.answer.assert_awaited_once()
    # nur ein Alert, keine Nachrichtenänderung
    query.edit_message_text.assert_not_awaited()
    assert db.has_trade_today(CHAT, "NVDA") is False


# ── Über-Nacht-Halten (eod_close aus): datumsübergreifende Trades ────────────

def test_overnight_trade_is_managed_across_days():
    fresh_db()
    db.get_or_create_user(CHAT, "tester")
    db.save_profile(CHAT, trade_size_eur=25.0)
    # Aktiven Trade von GESTERN direkt einfügen (simuliert über Nacht gehalten)
    sig = {"ticker": "AAPL", "direction": "long", "price": 100.0,
           "stop_loss": 95.0, "take_profit": 110.0, "leverage": 1.0}
    with db._connect() as conn:
        conn.execute(
            "INSERT INTO trades (user_id, trade_date, ticker, direction, signal_json, status, entry) "
            "VALUES (?, date('now','-1 day'), ?, ?, ?, 'active', ?)",
            (CHAT, "AAPL", "long", __import__("json").dumps(sig), 100.0),
        )
    # get_active_trades findet ihn datumsunabhängig
    act = db.get_active_trades(CHAT)
    assert len(act) == 1 and act[0]["ticker"] == "AAPL"
    # Duplikat-Schutz greift (kein neues Signal heute), obwohl 'heute' kein Datensatz existiert
    assert db.has_open_position(CHAT, "AAPL") is True
    assert db.has_trade_today(CHAT, "AAPL") is False
    # Schließen funktioniert datumsunabhängig
    db.close_all(CHAT, [{"ticker": "AAPL", "exit": 110.0, "pnl_eur": 21.25, "pnl_pct": 10.0}])
    assert db.get_active_trades(CHAT) == []
    with db._connect() as conn:
        row = conn.execute("SELECT status, pnl_eur FROM trades WHERE user_id = ? AND ticker = ?",
                           (CHAT, "AAPL")).fetchone()
    assert row["status"] == "closed" and row["pnl_eur"] == 21.25


# ── Ranglisten zusammenführen ────────────────────────────────────────────────

def test_merge_ranked_same_strategy_dedups_keeping_highest_raw_score():
    a = [{"ticker": "NVDA", "strength": 60}, {"ticker": "AAPL", "strength": 50}]
    b = [{"ticker": "NVDA", "strength": 80}, {"ticker": "MSFT", "strength": 40}]
    merged = bot._merge_ranked([a, b])
    by_ticker = {s["ticker"]: s["strength"] for s in merged}
    # Regionslisten derselben Strategie dürfen intern nach deren Rohscore zusammengeführt werden.
    assert by_ticker == {"NVDA": 80, "AAPL": 50, "MSFT": 40}


def test_merge_ranked_ignores_none_lists_and_tickerless_entries():
    merged = bot._merge_ranked([None, [{"strength": 99}], [{"ticker": "T", "strength": 10}]])
    # None-Liste und Einträge ohne Ticker werden übersprungen (kein Absturz)
    assert [s["ticker"] for s in merged] == ["T"]


def test_interleave_strategy_rankings_is_deterministic_without_score_comparison():
    rankings = {
        "standard": [
            {"ticker": "STD1", "strategy": "standard", "raw_score": 99},
            {"ticker": "STD2", "strategy": "standard", "raw_score": 98},
        ],
        "bb_revert": [
            {"ticker": "BB1", "strategy": "bb_revert", "raw_score": 5},
            {"ticker": "BB2", "strategy": "bb_revert", "raw_score": 4},
        ],
    }
    # Strategie-Keys laufen alphabetisch; die 5 wird nicht hinter die 98 sortiert.
    merged = bot._interleave_strategy_rankings(rankings)
    assert [s["ticker"] for s in merged] == ["BB1", "STD1", "BB2", "STD2"]
    assert [s["ticker"] for s in bot._interleave_strategy_rankings(rankings, limit=3)] == [
        "BB1", "STD1", "BB2",
    ]


def test_interleave_duplicate_uses_own_rank_then_alphabetical_strategy():
    rankings = {
        "standard": [{"ticker": "S1"}, {"ticker": "DUP"}, {"ticker": "S3"}],
        "bb_revert": [{"ticker": "B1"}, {"ticker": "B2"}, {"ticker": "DUP"}],
        "ai_adaptive": [{"ticker": "DUP"}, {"ticker": "A2"}],
    }
    merged = bot._interleave_strategy_rankings(rankings)
    dup = [s for s in merged if s["ticker"] == "DUP"]
    assert len(dup) == 1 and dup[0] is rankings["ai_adaptive"][0]  # Rang 1 gewinnt

    bb_dup = {"ticker": "DUP", "strategy": "bb_revert", "raw_score": 1}
    tied = bot._interleave_strategy_rankings({
        "standard": [{"ticker": "DUP", "strategy": "standard", "raw_score": 100}],
        "bb_revert": [bb_dup],
    })
    assert tied == [bb_dup]  # gleicher Rang: alphabetisch bb_revert vor standard


def test_trade_card_labels_strategy_raw_score_without_probability_scale():
    trade = {
        "ticker": "NVDA", "direction": "long", "entry": 100.0,
        "signal": {"strategy": "bb_revert", "raw_score": 61.0, "strength": 61.0,
                   "leverage": 1.0},
    }
    original = bot._unrealized_pnl
    bot._unrealized_pnl = lambda trade, size: (101.0, 1.0, 1.0)
    try:
        text, _ = bot._trade_card(trade, 100.0, current_strength=55.0)
    finally:
        bot._unrealized_pnl = original
    assert "Strategie-Rohscore (Bollinger %B Mean-Reversion)" in text
    assert "61/100" not in text and "keine Gewinnwahrscheinlichkeit" in text


# ── Auto-Accept: still kaufen + EIN Tagesreport nach Börsenschluss ───────────

def test_auto_accept_is_silent_and_sends_one_daily_report():
    fresh_db()
    db.get_or_create_user(CHAT, "u")
    db.save_profile(CHAT, trade_size_eur=25.0)
    db.set_auto_accept(CHAT, True)

    o_open, o_eval = bot._us_market_open, bot.evaluate_trades
    o_cutoff = exchange_calendar.is_past_entry_cutoff
    bot._us_market_open = lambda extended=None: True
    exchange_calendar.is_past_entry_cutoff = lambda *a, **k: False  # weit vor Handelsschluss
    try:
        # 1) Auto-Accept-Kauf: KEINE Einzelmeldung, Trade wird aber aktiv angelegt
        b = fake_bot()
        sent = asyncio.run(bot.send_signal(b, CHAT, make_signal("NVDA", 100.0), 25.0,
                                           market_open=True, auto_accept=True))
        assert sent is True
        assert b.send_message.await_count == 0
        assert db.get_trade(CHAT, "NVDA")["status"] == "active"

        # 2) Börsenschluss: GENAU eine gebündelte Report-Nachricht (gekauft + verkauft)
        bot.evaluate_trades = lambda trades, size: [
            {"ticker": t["ticker"], "entry": t["entry"], "exit": 105.0,
             "pnl_eur": 1.25, "pnl_pct": 5.0, "exit_reason": "Schlusskurs"} for t in trades]
        ctx = MagicMock()
        ctx.bot = fake_bot()
        asyncio.run(bot.close_and_evaluate(ctx))
        assert ctx.bot.send_message.await_count == 1
        text = ctx.bot.send_message.await_args.kwargs["text"]
        assert "Tagesreport" in text
        assert "Gekauft (1)" in text and "Verkauft (1)" in text
        assert "NVDA" in text
    finally:
        bot._us_market_open, bot.evaluate_trades = o_open, o_eval
        exchange_calendar.is_past_entry_cutoff = o_cutoff


class _LocalDateAhead(date):
    """`date` eines Servers, dessen **lokales** Datum dem UTC-Datum einen Tag voraus ist.

    Genau die Lage, die auf einer Maschine mit positivem Zeitzonen-Offset (CEST = UTC+2,
    Pacific/Auckland = UTC+12) zwischen Mitternacht und dem Offset herrscht.
    """
    @classmethod
    def today(cls):
        return db.today_utc_date() + timedelta(days=1)


def test_auto_accept_daily_report_follows_utc_day_not_server_local_day():
    """Der Tagesreport muss den Handelstag in UTC bilden (`db.today_utc()`).

    `trade_date` und die Event-`ts` sind UTC-gestempelt; der Report fragte den Tag früher
    mit `date.today()` (Server-Lokalzeit) ab. Sobald beide auseinanderlaufen, fand
    `get_all_trades_between()` die heutigen Käufe nicht — der Report meldete „Gekauft (0)",
    obwohl gekauft wurde.

    Deterministisch: die lokale Datumsquelle des Prozesses wird fest einen Tag vor das
    UTC-Datum gesetzt, statt sich auf die echte Uhrzeit des Testlaufs zu verlassen (genau
    diese Zeitabhängigkeit hat den Bug so lange verdeckt).
    """
    fresh_db()
    db.get_or_create_user(CHAT, "u")
    db.save_profile(CHAT, trade_size_eur=25.0)
    db.set_auto_accept(CHAT, True)

    o_open, o_eval = bot._us_market_open, bot.evaluate_trades
    o_cutoff, o_date = exchange_calendar.is_past_entry_cutoff, bot.date
    bot._us_market_open = lambda extended=None: True
    exchange_calendar.is_past_entry_cutoff = lambda *a, **k: False
    bot.date = _LocalDateAhead              # Server-Lokaldatum ≠ UTC-Datum
    try:
        asyncio.run(bot.send_signal(fake_bot(), CHAT, make_signal("NVDA", 100.0), 25.0,
                                    market_open=True, auto_accept=True))
        assert db.get_trade(CHAT, "NVDA")["status"] == "active"
        assert db.get_trade(CHAT, "NVDA")["trade_date"] == db.today_utc()

        bot.evaluate_trades = lambda trades, size: [
            {"ticker": t["ticker"], "entry": t["entry"], "exit": 105.0,
             "pnl_eur": 1.25, "pnl_pct": 5.0, "exit_reason": "Schlusskurs"} for t in trades]
        ctx = MagicMock()
        ctx.bot = fake_bot()
        asyncio.run(bot.close_and_evaluate(ctx))

        text = ctx.bot.send_message.await_args.kwargs["text"]
        assert "Gekauft (1)" in text and "NVDA" in text
    finally:
        bot._us_market_open, bot.evaluate_trades = o_open, o_eval
        exchange_calendar.is_past_entry_cutoff = o_cutoff
        bot.date = o_date


def test_auto_accept_skips_activation_within_entry_cutoff():
    """DATA-002 / Plan.md §10.1 „Entry-Sperre relativ zum Close": Auto-Accept legt kurz vor
    Handelsschluss KEINE neue Position mehr an (send_signal läuft trotzdem durch, Duplikat-
    Schutz für den Tag bleibt aber aus, da nie aktiviert wurde)."""
    fresh_db()
    db.get_or_create_user(CHAT, "u")
    db.save_profile(CHAT, trade_size_eur=25.0)
    db.set_auto_accept(CHAT, True)

    o_open, o_cutoff = bot._us_market_open, exchange_calendar.is_past_entry_cutoff
    bot._us_market_open = lambda extended=None: True
    exchange_calendar.is_past_entry_cutoff = lambda *a, **k: True   # kurz vor Handelsschluss
    try:
        b = fake_bot()
        sent = asyncio.run(bot.send_signal(b, CHAT, make_signal("NVDA", 100.0), 25.0,
                                           market_open=True, auto_accept=True))
        assert sent is True
        assert b.send_message.await_count == 0
        assert db.get_trade(CHAT, "NVDA")["status"] == "pending"    # NICHT aktiviert
    finally:
        bot._us_market_open = o_open
        exchange_calendar.is_past_entry_cutoff = o_cutoff


def test_close_and_evaluate_report_header_shows_actual_berlin_time():
    """DATA-002 / Plan.md §10.1 „Reports separat in Europe/Berlin": der Job feuert seit
    _session_scheduler_tick relativ zum echten Handelsschluss, daher zeigt der Report die
    tatsächliche Berlin-Uhrzeit statt eines evtl. nicht mehr zutreffenden CLOSE_TIME_HOUR/MIN."""
    fresh_db()
    db.yf = _FakeYF(100.0)
    db.get_or_create_user(CHAT, "u")
    db.save_profile(CHAT, trade_size_eur=25.0)
    db.set_eod_close(CHAT, True)
    db.add_pending(CHAT, make_signal("NVDA"), 111)
    db.activate_trade(CHAT, "NVDA")

    from stockbot import config as cfg
    fixed_berlin_now = datetime(2026, 6, 10, 23, 0, tzinfo=bot.BERLIN_TZ)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_berlin_now if tz is not None else fixed_berlin_now.replace(tzinfo=None)

    o_datetime, o_eval = bot.datetime, bot.evaluate_trades
    bot.datetime = _FixedDatetime
    bot.evaluate_trades = lambda trades, size: [
        {"ticker": t["ticker"], "entry": t["entry"], "exit": 105.0,
         "pnl_eur": 1.25, "pnl_pct": 5.0, "exit_reason": "Schlusskurs"} for t in trades]
    try:
        ctx = MagicMock()
        ctx.bot = fake_bot()
        asyncio.run(bot.close_and_evaluate(ctx))
        head_call = ctx.bot.send_message.await_args_list[0]
        text = head_call.kwargs["text"]
        assert "23:00 Uhr" in text
        assert f"{cfg.CLOSE_TIME_HOUR:02d}:{cfg.CLOSE_TIME_MIN:02d} Uhr" not in text
    finally:
        bot.datetime, bot.evaluate_trades = o_datetime, o_eval


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
