"""
Tests für Markt-Bereiche (Universen), Anzahl Signale und /settings.

Lauf:  python test_settings.py   oder   pytest test_settings.py
Alle Tests laufen offline.
"""

import sys
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import db
import bot
import config
import analyzer

CHAT = 5151


def fresh_db():
    d = tempfile.mkdtemp(prefix="bottest_")
    db.DB_FILE = Path(d) / "test.db"
    db.init_db()


# ── config: drei Universen ───────────────────────────────────────────────────

def test_three_universes_exist_and_nonempty():
    assert set(config.UNIVERSES.keys()) == {"sp500", "msci_world", "emerging"}
    for key, tickers in config.UNIVERSES.items():
        assert len(tickers) > 0, f"Universum {key} ist leer"
        assert key in config.REGION_LABELS


def test_default_region_valid():
    assert config.DEFAULT_REGION in config.UNIVERSES


# ── db: Region + Anzahl persistieren ─────────────────────────────────────────

def test_new_user_has_defaults():
    fresh_db()
    u = db.get_or_create_user(CHAT, "tester")
    assert u["market_region"] == "sp500"
    assert u["top_n_signals"] == 5


def test_set_market_region_persists():
    fresh_db()
    db.get_or_create_user(CHAT)
    db.set_market_region(CHAT, "emerging")
    assert db.get_user(CHAT)["market_region"] == "emerging"


def test_set_top_n_persists_and_clamps():
    fresh_db()
    db.get_or_create_user(CHAT)
    db.set_top_n(CHAT, 8)
    assert db.get_user(CHAT)["top_n_signals"] == 8
    db.set_top_n(CHAT, 999)          # über Obergrenze
    assert db.get_user(CHAT)["top_n_signals"] == 20
    db.set_top_n(CHAT, 0)            # unter Untergrenze
    assert db.get_user(CHAT)["top_n_signals"] == 1


# ── analyzer: get_top_signals begrenzt auf top_n ─────────────────────────────

def test_get_top_signals_respects_top_n(monkeypatch=None):
    # analyze_universe mocken, damit kein Netzwerk nötig ist
    fake = [{"ticker": f"T{i}", "rsi": 50, "strength": 3} for i in range(10)]
    orig = analyzer.analyze_universe
    analyzer.analyze_universe = lambda tickers: list(fake)
    try:
        assert len(analyzer.get_top_signals(["X"], top_n=3)) == 3
        assert len(analyzer.get_top_signals(["X"], top_n=7)) == 7
    finally:
        analyzer.analyze_universe = orig


# ── /settings: Buttons ändern das Profil ─────────────────────────────────────

def _fake_settings_query(data):
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.data = data
    update = MagicMock()
    update.callback_query = query
    update.effective_chat.id = CHAT
    return update, query


def test_settings_view_marks_current_region_and_count():
    fresh_db()
    user = db.get_or_create_user(CHAT)
    text, keyboard = bot._settings_view(user)
    flat = [b.text for row in keyboard.inline_keyboard for b in row]
    # aktueller Bereich (S&P 500) und aktuelle Anzahl (5) sind markiert
    assert any("✅" in t and "S&P 500" in t for t in flat)
    assert any(t == "✅ 5" for t in flat)


def test_set_region_button_updates_db():
    fresh_db()
    db.get_or_create_user(CHAT)
    update, query = _fake_settings_query("set_region:msci_world")
    asyncio.run(bot.button_handler(update, MagicMock()))
    assert db.get_user(CHAT)["market_region"] == "msci_world"
    query.edit_message_text.assert_awaited()    # Menü wurde neu gezeichnet


def test_set_count_button_updates_db():
    fresh_db()
    db.get_or_create_user(CHAT)
    update, query = _fake_settings_query("set_count:8")
    asyncio.run(bot.button_handler(update, MagicMock()))
    assert db.get_user(CHAT)["top_n_signals"] == 8


def test_invalid_region_ignored():
    fresh_db()
    db.get_or_create_user(CHAT)
    update, query = _fake_settings_query("set_region:does_not_exist")
    asyncio.run(bot.button_handler(update, MagicMock()))
    assert db.get_user(CHAT)["market_region"] == "sp500"   # unverändert


# ── /settings: Voll-Universum-Schalter ───────────────────────────────────────

def test_auto_universe_default_on_and_setter():
    fresh_db()
    u = db.get_or_create_user(CHAT)
    assert u["auto_universe"] is True                       # Default AN
    db.set_auto_universe(CHAT, False)
    assert db.get_user(CHAT)["auto_universe"] is False
    db.set_auto_universe(CHAT, True)
    assert db.get_user(CHAT)["auto_universe"] is True


def test_set_uni_button_updates_db():
    fresh_db()
    db.get_or_create_user(CHAT)
    update, query = _fake_settings_query("set_uni:0")
    asyncio.run(bot.button_handler(update, MagicMock()))
    assert db.get_user(CHAT)["auto_universe"] is False      # ausgeschaltet
    query.edit_message_text.assert_awaited()                # Menü neu gezeichnet
    update, query = _fake_settings_query("set_uni:1")
    asyncio.run(bot.button_handler(update, MagicMock()))
    assert db.get_user(CHAT)["auto_universe"] is True


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
