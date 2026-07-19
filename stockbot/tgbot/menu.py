"""
Persistentes Hauptmenü an der Chat-Leiste (Reply-Keyboard) + Telegram-Befehlsmenü.

Die Button-Labels sind zugleich die Message-Texte, die der Client sendet —
`bot.py` mappt sie in MENU_BUTTON_HANDLERS auf die bestehenden /command-Handler.
Labels hier zentral halten, damit Keyboard und Dispatch nie auseinanderlaufen.
"""

from telegram import BotCommand, ReplyKeyboardMarkup

BTN_SIGNALS = "📈 Signale"
BTN_EVALUATE = "✅ Auswertung"
BTN_DASHBOARD = "📊 Dashboard"
BTN_SETTINGS = "⚙️ Einstellungen"
BTN_PROFILE = "👤 Profil"
BTN_HELP = "❓ Hilfe"

MENU_LAYOUT = [
    [BTN_SIGNALS, BTN_EVALUATE],
    [BTN_DASHBOARD, BTN_SETTINGS],
    [BTN_PROFILE, BTN_HELP],
]


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Das dauerhafte Menü unter dem Eingabefeld (alle Nutzer, private Chats)."""
    return ReplyKeyboardMarkup(
        MENU_LAYOUT,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Befehl wählen oder tippen …",
    )


# Das „Menü"-Button-Verzeichnis links neben dem Eingabefeld (set_my_commands).
# Nur nutzer-relevante Befehle — Admin-Befehle (/killswitch) bewusst nicht gelistet.
BOT_COMMANDS = [
    BotCommand("start", "Setup starten oder Status anzeigen"),
    BotCommand("signals", "Aktuelle Signale jetzt live abrufen"),
    BotCommand("evaluate", "Aktive Demo-Trades jetzt auswerten"),
    BotCommand("dashboard", "Link zum Web-Dashboard"),
    BotCommand("website", "Ein-Klick-Login zur Web-App"),
    BotCommand("settings", "Körbe, Trade-Größe & Signale ändern"),
    BotCommand("profile", "Dein Profil ansehen"),
    BotCommand("watchlist", "Persönliche Watchlist anzeigen"),
    BotCommand("strategies", "Verfügbare Strategien anzeigen"),
    BotCommand("top5trade", "Was große Trader zuletzt gekauft haben"),
    BotCommand("info", "Wie kommen die Signale zustande?"),
    BotCommand("help", "Alle Befehle anzeigen"),
]
