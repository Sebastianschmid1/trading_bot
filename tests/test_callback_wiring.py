"""Tests für die Verdrahtung der Callback-Tokens in die Bot-Handler (W7-Integration)."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from stockbot.core import db
from stockbot.tgbot import bot, callback_security as cbs


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", Path(tmp_path) / "cb.db")
    db.init_db()
    return db


def _minimal_signal(ticker="AAPL"):
    return {
        "ticker": ticker, "direction": "long", "price": 100.0, "strength": 60,
        "raw_score": 60, "rsi": 50.0, "rsi_comment": "-", "macd_comment": "-",
        "trend_comment": "-", "volume_comment": "-", "leverage": 1.0,
    }


def test_signal_card_issues_resolvable_tokens(fresh_db):
    _, keyboard = bot._signal_card(_minimal_signal(), 25.0, market_open=True, user_id=7)
    accept_btn, reject_btn = keyboard.inline_keyboard[0]
    assert accept_btn.callback_data.startswith("t:")
    assert reject_btn.callback_data.startswith("t:")
    assert len(accept_btn.callback_data.encode()) <= 64   # Telegram-Limit

    action, payload = cbs.resolve(accept_btn.callback_data[2:], 7)
    assert action == "accept"
    assert payload == {"ticker": "AAPL"}
    action, payload = cbs.resolve(reject_btn.callback_data[2:], 7)
    assert action == "reject"


def test_signal_card_without_user_id_stays_legacy(fresh_db):
    _, keyboard = bot._signal_card(_minimal_signal(), 25.0, market_open=True)
    accept_btn = keyboard.inline_keyboard[0][0]
    assert accept_btn.callback_data == "accept:AAPL"


def test_trade_card_sell_button_is_tokenized(fresh_db):
    trade = {"ticker": "MSFT", "direction": "long", "entry": 100.0,
             "signal": _minimal_signal("MSFT")}
    _, keyboard = bot._trade_card(trade, 25.0, user_id=9)
    sell_btn = keyboard.inline_keyboard[0][0]
    assert sell_btn.callback_data.startswith("t:")
    action, payload = cbs.resolve(sell_btn.callback_data[2:], 9)
    assert action == "sell"
    assert payload == {"ticker": "MSFT"}


def test_secure_cb_fails_open_to_legacy_on_db_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(cbs, "issue", boom)
    assert bot._secure_cb(7, "accept", "AAPL") == "accept:AAPL"


def _callback_update(data, chat_id=7):
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    return update


def test_button_handler_rejects_replayed_token(fresh_db):
    token = cbs.issue(7, "accept", {"ticker": "AAPL"})
    cbs.resolve(token, 7)   # erster (legitimer) Klick verbraucht das Token

    update = _callback_update(f"t:{token}")
    asyncio.run(bot.button_handler(update, MagicMock()))

    update.callback_query.answer.assert_awaited_once()
    args, kwargs = update.callback_query.answer.await_args
    assert "verarbeitet" in args[0]
    assert kwargs.get("show_alert") is True


def test_button_handler_rejects_foreign_users_token(fresh_db):
    token = cbs.issue(1, "sell", {"ticker": "MSFT"})

    update = _callback_update(f"t:{token}", chat_id=2)
    asyncio.run(bot.button_handler(update, MagicMock()))

    update.callback_query.answer.assert_awaited_once()
    args, kwargs = update.callback_query.answer.await_args
    assert kwargs.get("show_alert") is True
    # Token bleibt beim echten Besitzer weiter einlösbar (wrong_user verbraucht nicht)
    action, _ = cbs.resolve(token, 1)
    assert action == "sell"


# ── Modus-Präfix in der Signalnachricht (§26.1/§26.2, agent/TG-MODEPREFIX) ──────────────────

def test_signal_card_mode_prefix_demo_paper_live(fresh_db, monkeypatch):
    """Deckt alle drei Fälle ab: ohne Broker-Anbindung (demo), Broker an + Paper-Gate (paper),
    Broker an + Live-Gate (live). Würde rot, wenn die erste Zeile das Präfix nicht trägt oder
    ein anderer Modus ermittelt würde."""
    from stockbot.core import glossary

    # Ohne Registrierung/Broker-Anbindung → "demo" (UI-Zustand, siehe glossary.MODE_LABELS.demo)
    text, _ = bot._signal_card(_minimal_signal(), 25.0, market_open=True, user_id=42)
    assert text.split("\n", 1)[0] == glossary.MODE_MESSAGE_PREFIXES["demo"]

    db.get_or_create_user(42, "modeprefix-tester")
    db.save_profile(42, trade_size_eur=25.0)
    db.set_broker_exec(42, True)

    # Broker-Ausführung aktiv + globales Paper-Gate → "paper"
    monkeypatch.setattr(bot, "ALPACA_PAPER", True)
    text, _ = bot._signal_card(_minimal_signal(), 25.0, market_open=True, user_id=42)
    assert text.split("\n", 1)[0] == glossary.MODE_MESSAGE_PREFIXES["paper"]

    # Broker-Ausführung aktiv + Live-Gate scharf → wörtlich dieselbe Warnung wie die Web-App
    monkeypatch.setattr(bot, "ALPACA_PAPER", False)
    text, _ = bot._signal_card(_minimal_signal(), 25.0, market_open=True, user_id=42)
    assert text.split("\n", 1)[0] == glossary.MODE_MESSAGE_PREFIXES["live"]
    assert text.split("\n", 1)[0] == "LIVE – ECHTES GELD"


def test_signal_card_body_unchanged_apart_from_new_prefix_line(fresh_db):
    """AC5: Die Änderung fügt ausschließlich eine neue erste Zeile ein — der gesamte
    restliche Nachrichtentext bleibt Zeichen für Zeichen wie vor agent/TG-MODEPREFIX."""
    expected_body = (
        "📊 *AAPL* — 🟢 LONG\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "💰 Kurs: *$100.00*\n"
        "📈 Strategie-Rohscore (Standard (Multi-Timeframe)): 60 — keine Gewinnwahrscheinlichkeit\n"
        "🔍 Begründung:\n"
        "  • RSI: 50.0 → -\n"
        "  • MACD: -\n"
        "  • Trend (MA50/200): -\n"
        "  • Wochentrend: —\n"
        "  • Volumen: -\n"
        "  • Level: —\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⚡ Hebel: *1×*\n"
        "💵 Demo-Trade: *25$ LONG*\n"
        "✅ Jederzeit annehmbar (kein Zeitlimit)\n"
        "⏱ Auswertung: 22:15 Uhr (oder früher bei SL/TP)"
    )
    text, _ = bot._signal_card(_minimal_signal(), 25.0, market_open=True)
    prefix, _, body = text.partition("\n")
    assert prefix == "DEMO"
    assert body == expected_body
