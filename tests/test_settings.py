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

from stockbot.core import db
from stockbot.tgbot import bot
from stockbot import config
from stockbot.market import analyzer

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


def test_set_region_button_toggles_multiselect():
    fresh_db()
    db.get_or_create_user(CHAT)                  # Default: nur sp500
    update, query = _fake_settings_query("set_region:msci_world")
    asyncio.run(bot.button_handler(update, MagicMock()))
    assert set(db.get_user(CHAT)["market_regions"]) == {"sp500", "msci_world"}   # beide aktiv
    query.edit_message_text.assert_awaited()    # Menü wurde neu gezeichnet
    # erneut tippen → wieder entfernt
    update, query = _fake_settings_query("set_region:msci_world")
    asyncio.run(bot.button_handler(update, MagicMock()))
    assert db.get_user(CHAT)["market_regions"] == ["sp500"]


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


# ── Strategie-Auswahl ────────────────────────────────────────────────────────

def test_strategy_default_and_toggle():
    fresh_db()
    u = db.get_or_create_user(CHAT)
    assert u["strategies"] == ["standard"]                  # Default
    db.toggle_strategy(CHAT, "adx_trend")                   # hinzufügen
    assert db.get_user(CHAT)["strategies"] == ["standard", "adx_trend"]
    db.toggle_strategy(CHAT, "standard")                    # entfernen
    assert db.get_user(CHAT)["strategies"] == ["adx_trend"]
    db.toggle_strategy(CHAT, "adx_trend")                   # letzte bleibt erhalten
    assert db.get_user(CHAT)["strategies"] == ["adx_trend"]


def test_set_strat_button_toggles_multiselect():
    fresh_db()
    db.get_or_create_user(CHAT)
    update, query = _fake_settings_query("set_strat:bb_revert")
    asyncio.run(bot.button_handler(update, MagicMock()))
    assert set(db.get_user(CHAT)["strategies"]) == {"standard", "bb_revert"}  # beide aktiv
    query.edit_message_text.assert_awaited()
    _, keyboard = bot._settings_view(db.get_user(CHAT))
    strategy_callbacks = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data.startswith("set_strat:")
    }
    assert strategy_callbacks == {
        "set_strat:standard", "set_strat:ai_adaptive", "set_strat:bb_revert",
    }


def test_set_strat_button_ignores_unknown():
    fresh_db()
    db.get_or_create_user(CHAT)
    update, query = _fake_settings_query("set_strat:does_not_exist")
    asyncio.run(bot.button_handler(update, MagicMock()))
    assert db.get_user(CHAT)["strategies"] == ["standard"]   # unverändert


def _fake_cmd(args):
    update = MagicMock()
    update.effective_chat.id = CHAT
    update.message.reply_text = AsyncMock()
    ctx = MagicMock()
    ctx.args = args
    return update, ctx


def test_llm_rank_default_and_setter():
    fresh_db()
    u = db.get_or_create_user(CHAT)
    assert u["llm_rank"] is True                 # Default an
    db.set_llm_rank(CHAT, False)
    assert db.get_user(CHAT)["llm_rank"] is False
    db.set_llm_rank(CHAT, True)
    assert db.get_user(CHAT)["llm_rank"] is True


def test_set_llm_button_updates_db():
    fresh_db()
    db.get_or_create_user(CHAT)
    update, query = _fake_settings_query("set_llm:0")
    asyncio.run(bot.button_handler(update, MagicMock()))
    assert db.get_user(CHAT)["llm_rank"] is False
    query.edit_message_text.assert_awaited()
    update, query = _fake_settings_query("set_llm:1")
    asyncio.run(bot.button_handler(update, MagicMock()))
    assert db.get_user(CHAT)["llm_rank"] is True


def test_eod_close_default_and_button():
    fresh_db()
    u = db.get_or_create_user(CHAT)
    assert u["eod_close"] is True                       # Default: am Tagesende schließen
    update, query = _fake_settings_query("set_eod:0")
    asyncio.run(bot.button_handler(update, MagicMock()))
    assert db.get_user(CHAT)["eod_close"] is False      # über Nacht halten
    query.edit_message_text.assert_awaited()
    db.set_eod_close(CHAT, True)
    assert db.get_user(CHAT)["eod_close"] is True


def test_broker_exec_default_and_setter():
    fresh_db()
    u = db.get_or_create_user(CHAT)
    assert u["broker_exec"] is False                    # Default AUS (keine echten Orders)
    db.set_broker_exec(CHAT, True)
    assert db.get_user(CHAT)["broker_exec"] is True
    db.set_broker_exec(CHAT, False)
    assert db.get_user(CHAT)["broker_exec"] is False


def test_set_broker_button_updates_db_when_enabled():
    fresh_db()
    db.get_or_create_user(CHAT)
    orig = bot.ALPACA_ENABLED
    bot.ALPACA_ENABLED = True                           # Schalter ist nur bei aktivem Alpaca sichtbar
    try:
        update, query = _fake_settings_query("set_broker:1")
        asyncio.run(bot.button_handler(update, MagicMock()))
        assert db.get_user(CHAT)["broker_exec"] is True
        query.edit_message_text.assert_awaited()
        update, query = _fake_settings_query("set_broker:0")
        asyncio.run(bot.button_handler(update, MagicMock()))
        assert db.get_user(CHAT)["broker_exec"] is False
    finally:
        bot.ALPACA_ENABLED = orig


def test_set_broker_button_ignored_when_disabled():
    fresh_db()
    db.get_or_create_user(CHAT)
    orig = bot.ALPACA_ENABLED
    bot.ALPACA_ENABLED = False
    try:
        update, query = _fake_settings_query("set_broker:1")
        asyncio.run(bot.button_handler(update, MagicMock()))
        assert db.get_user(CHAT)["broker_exec"] is False   # unverändert
    finally:
        bot.ALPACA_ENABLED = orig


def test_toggle_region_keeps_at_least_one():
    fresh_db()
    db.get_or_create_user(CHAT)
    assert db.get_user(CHAT)["market_regions"] == ["sp500"]
    db.toggle_region(CHAT, "emerging")
    assert set(db.get_user(CHAT)["market_regions"]) == {"sp500", "emerging"}
    db.toggle_region(CHAT, "sp500")
    assert db.get_user(CHAT)["market_regions"] == ["emerging"]
    db.toggle_region(CHAT, "emerging")          # letzter Korb bleibt erhalten
    assert db.get_user(CHAT)["market_regions"] == ["emerging"]


def test_user_regions_filters_unknown_keys():
    fresh_db()
    db.get_or_create_user(CHAT)
    db.set_market_region(CHAT, "sp500,quatsch,emerging")   # ungültiger Korb dazwischen
    assert bot._user_regions(db.get_user(CHAT)) == ["sp500", "emerging"]


def test_set_trade_size_persists_and_clamps():
    fresh_db()
    db.get_or_create_user(CHAT)
    assert db.set_trade_size(CHAT, 250) == 250.0
    assert db.get_user(CHAT)["trade_size_eur"] == 250.0
    assert db.set_trade_size(CHAT, 0) == 1.0                # Untergrenze
    assert db.set_trade_size(CHAT, 9_999_999) == 1_000_000.0  # Obergrenze


def test_set_size_button_updates_db():
    fresh_db()
    db.get_or_create_user(CHAT)
    update, query = _fake_settings_query("set_size:100")
    asyncio.run(bot.button_handler(update, MagicMock()))
    assert db.get_user(CHAT)["trade_size_eur"] == 100.0
    query.edit_message_text.assert_awaited()


def test_tradesize_command_sets_and_validates():
    fresh_db()
    db.get_or_create_user(CHAT, "tester")
    db.save_profile(CHAT, trade_size_eur=25.0)
    update, ctx = _fake_cmd(["250"])
    asyncio.run(bot.cmd_tradesize(update, ctx))
    assert db.get_user(CHAT)["trade_size_eur"] == 250.0
    update, ctx = _fake_cmd(["quatsch"])                   # ungültig → unverändert
    asyncio.run(bot.cmd_tradesize(update, ctx))
    assert db.get_user(CHAT)["trade_size_eur"] == 250.0


def test_settings_view_shows_size_and_multiple_regions():
    fresh_db()
    db.get_or_create_user(CHAT)
    db.toggle_region(CHAT, "msci_world")
    db.set_trade_size(CHAT, 100)
    text, keyboard = bot._settings_view(db.get_user(CHAT))
    flat = [b.text for row in keyboard.inline_keyboard for b in row]
    assert any(t == "✅ 100€" for t in flat)                # gewählte Größe markiert
    # zwei Körbe markiert
    assert sum(1 for t in flat if "✅" in t and ("S&P 500" in t or "MSCI" in t)) == 2


def test_alpaca_credentials_roundtrip_and_clear():
    fresh_db()
    db.get_or_create_user(CHAT)
    assert db.has_alpaca_credentials(CHAT) is False
    db.set_alpaca_credentials(CHAT, "AK123", "SECRET456")
    assert db.has_alpaca_credentials(CHAT) is True
    assert db.get_user(CHAT)["broker_platform"] == "alpaca"
    assert db.get_decrypted_credentials(CHAT) == ("AK123", "SECRET456")   # entschlüsselt zurück
    db.clear_alpaca_credentials(CHAT)
    assert db.has_alpaca_credentials(CHAT) is False
    assert db.get_user(CHAT)["broker_platform"] is None
    assert db.get_decrypted_credentials(CHAT) is None


def test_alpaca_ready_uses_per_user_creds_even_without_global_keys():
    fresh_db()
    db.get_or_create_user(CHAT)
    orig = bot.ALPACA_ENABLED
    bot.ALPACA_ENABLED = False                     # keine globalen .env-Keys
    try:
        assert bot._alpaca_ready(db.get_user(CHAT)) is False
        db.set_alpaca_credentials(CHAT, "AK", "SK")
        assert bot._alpaca_ready(db.get_user(CHAT)) is True    # eigene Keys reichen
        # Broker-Schalter ist nun sichtbar
        text, keyboard = bot._settings_view(db.get_user(CHAT))
        flat = [b.text for row in keyboard.inline_keyboard for b in row]
        assert any("Broker-Order" in t for t in flat)
    finally:
        bot.ALPACA_ENABLED = orig


def test_clear_alpaca_also_disables_broker_exec():
    fresh_db()
    db.get_or_create_user(CHAT)
    db.set_alpaca_credentials(CHAT, "AK", "SK")
    db.set_broker_exec(CHAT, True)
    assert db.get_user(CHAT)["broker_exec"] is True
    db.clear_alpaca_credentials(CHAT)
    assert db.get_user(CHAT)["broker_exec"] is False     # mit den Keys wird auch die Ausführung aus


def test_addstrat_command_adds_and_validates():
    fresh_db()
    db.get_or_create_user(CHAT, "tester")
    db.save_profile(CHAT, trade_size_eur=25.0)   # Onboarding abschließen (sonst greift /addstrat nicht)
    # gültige Strategie hinzufügen
    update, ctx = _fake_cmd(["ai_adaptive"])
    asyncio.run(bot.cmd_addstrat(update, ctx))
    assert set(db.get_user(CHAT)["strategies"]) == {"standard", "ai_adaptive"}
    # Research-only-Strategie darf nicht neu hinzukommen.
    update, ctx = _fake_cmd(["adx_trend"])
    asyncio.run(bot.cmd_addstrat(update, ctx))
    assert set(db.get_user(CHAT)["strategies"]) == {"standard", "ai_adaptive"}
    # ungültige Strategie → unverändert
    update, ctx = _fake_cmd(["quatsch"])
    asyncio.run(bot.cmd_addstrat(update, ctx))
    assert set(db.get_user(CHAT)["strategies"]) == {"standard", "ai_adaptive"}


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
