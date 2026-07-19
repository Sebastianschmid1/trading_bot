"""Tests für das persistente Hauptmenü (Reply-Keyboard) + Befehlsmenü (W7-Follow-up)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from stockbot.tgbot import menu


def test_keyboard_contains_all_buttons_and_is_persistent():
    kb = menu.main_menu_keyboard()
    labels = [btn.text for row in kb.keyboard for btn in row]
    assert labels == [
        menu.BTN_SIGNALS, menu.BTN_EVALUATE,
        menu.BTN_DASHBOARD, menu.BTN_SETTINGS,
        menu.BTN_PROFILE, menu.BTN_HELP,
    ]
    assert kb.is_persistent is True
    assert kb.resize_keyboard is True


def test_bot_commands_are_wellformed():
    names = [c.command for c in menu.BOT_COMMANDS]
    assert len(names) == len(set(names))
    for cmd in menu.BOT_COMMANDS:
        assert cmd.command == cmd.command.lower()
        assert not cmd.command.startswith("/")
        assert cmd.description
    # Admin-Befehle gehören nicht ins öffentliche Menü
    assert "killswitch" not in names


def test_every_menu_button_maps_to_a_registered_command_handler():
    from stockbot.tgbot import bot

    all_labels = {btn for row in menu.MENU_LAYOUT for btn in row}
    assert set(bot.MENU_BUTTON_HANDLERS) == all_labels
    assert bot.MENU_BUTTON_HANDLERS[menu.BTN_DASHBOARD] is bot.cmd_dashboard
    assert bot.MENU_BUTTON_HANDLERS[menu.BTN_SETTINGS] is bot.cmd_settings
    assert bot.MENU_BUTTON_HANDLERS[menu.BTN_SIGNALS] is bot.cmd_signals
    assert bot.MENU_BUTTON_HANDLERS[menu.BTN_EVALUATE] is bot.cmd_evaluate
    assert bot.MENU_BUTTON_HANDLERS[menu.BTN_PROFILE] is bot.cmd_profile
    assert bot.MENU_BUTTON_HANDLERS[menu.BTN_HELP] is bot.cmd_help


def _update_with_text(text):
    update = MagicMock()
    update.message.text = text
    return update


def test_dispatch_calls_mapped_handler(monkeypatch):
    from stockbot.tgbot import bot

    called = AsyncMock()
    monkeypatch.setitem(bot.MENU_BUTTON_HANDLERS, menu.BTN_SETTINGS, called)
    update = _update_with_text(f"  {menu.BTN_SETTINGS} ")  # Whitespace wird toleriert
    context = MagicMock()

    asyncio.run(bot.menu_button_dispatch(update, context))

    called.assert_awaited_once_with(update, context)


def test_dispatch_ignores_unknown_text_and_missing_message():
    from stockbot.tgbot import bot

    context = MagicMock()
    # Unbekannter Text → kein Handler, kein Fehler, keine Antwort
    update = _update_with_text("hallo bot")
    update.message.reply_text = AsyncMock()
    asyncio.run(bot.menu_button_dispatch(update, context))
    update.message.reply_text.assert_not_awaited()

    # Update ganz ohne message (z. B. edited_message) → kein Crash
    update = MagicMock()
    update.message = None
    asyncio.run(bot.menu_button_dispatch(update, context))
