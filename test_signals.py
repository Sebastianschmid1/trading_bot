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
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import db
import bot

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
    assert any(c.startswith("accept:") for c in callbacks)
    assert any(c.startswith("reject:") for c in callbacks)
    assert db.has_trade_today(CHAT, "NVDA") is True   # handelbarer Trade angelegt
    assert jq.run_once.called                          # Ablauf-Job geplant


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
