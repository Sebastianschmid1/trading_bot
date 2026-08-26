"""
Geführter Setup-Dialog: legt beim ersten /start ein Nutzerprofil an
(Demo-Trade-Größe, optional verschlüsselte Broker-Zugangsdaten).
"""

import logging

from telegram import Update
from telegram.ext import (
    ConversationHandler, CommandHandler, MessageHandler,
    ContextTypes, filters
)

from stockbot.core import db
from stockbot.core import glossary
from stockbot.config import TRADE_SIZE_EUR, SIGNAL_TIME_HOUR, SIGNAL_TIME_MIN
from stockbot.tgbot.menu import main_menu_keyboard

log = logging.getLogger(__name__)

ASK_TRADE_SIZE, ASK_BROKER_YESNO, ASK_BROKER_PLATFORM, ASK_BROKER_KEY, ASK_BROKER_SECRET = range(5)


# ── Einstieg ────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    profile = db.get_or_create_user(user.id, user.username)

    if profile["onboarding_state"] == "complete":
        await update.message.reply_text(
            "👋 Du bist bereits eingerichtet und erhältst täglich Signale.\n"
            "Nutze /profile, um deine Einstellungen zu sehen.",
            reply_markup=main_menu_keyboard(),
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "👋 *Willkommen beim Stock Signal Bot!*\n\n"
        f"1/3: Wie groß soll dein Demo-Trade in $ sein? (Standard: {TRADE_SIZE_EUR:.0f}$)\n"
        f"_{glossary.BROKER_CURRENCY_NOTE}_\n"
        "Antworte einfach mit einer Zahl, z. B. `25`.\n"
        "Mit /cancel kannst du jederzeit abbrechen.",
        parse_mode="Markdown"
    )
    return ASK_TRADE_SIZE


# ── 1. Demo-Trade-Größe ─────────────────────────────────────────────────────

async def ask_trade_size(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # agent/CURRENCY-HONEST: Prompt zeigt jetzt "$" — Nutzer tippen aus Gewohnheit aber
    # weiter "€" mit (Alttext/Copy-Paste), deshalb wird es neben "$" aus der Eingabe entfernt.
    # Das Zeichen steht hier bewusst literal: es ist Eingabe-Sanitization, keine Anzeige.
    text = update.message.text.strip().replace(",", ".").replace("€", "").replace("$", "")
    try:
        size = float(text)
        if size <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Bitte sende eine positive Zahl, z. B. `25`.", parse_mode="Markdown")
        return ASK_TRADE_SIZE

    context.user_data["trade_size_eur"] = size
    await update.message.reply_text(
        "2/3: Möchtest du eine echte Trading-Plattform verbinden (z. B. Alpaca) "
        "für künftiges Live-Trading?\n"
        "Antworte mit *ja* oder *nein* — oder /skip.",
        parse_mode="Markdown"
    )
    return ASK_BROKER_YESNO


# ── 2. Broker verbinden? ────────────────────────────────────────────────────

async def skip_broker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await finish_onboarding(update, context, broker_platform=None, api_key=None, api_secret=None)


async def ask_broker_yesno(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.message.text.strip().lower()

    if answer in ("nein", "no", "n"):
        return await finish_onboarding(update, context, broker_platform=None, api_key=None, api_secret=None)

    if answer in ("ja", "yes", "j", "y"):
        await update.message.reply_text("Welche Plattform nutzt du? (z. B. `alpaca`)", parse_mode="Markdown")
        return ASK_BROKER_PLATFORM

    await update.message.reply_text("⚠️ Bitte antworte mit *ja* oder *nein* — oder /skip.", parse_mode="Markdown")
    return ASK_BROKER_YESNO


async def ask_broker_platform(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["broker_platform"] = update.message.text.strip().lower()
    await update.message.reply_text(
        "3/3: Sende jetzt deinen *API-Key*.\n"
        "⚠️ Er wird verschlüsselt gespeichert und deine Nachricht danach sofort gelöscht. "
        "Mit /cancel abbrechen.",
        parse_mode="Markdown"
    )
    return ASK_BROKER_KEY


async def ask_broker_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["broker_api_key"] = update.message.text.strip()
    await _delete_message(update)
    await update.message.reply_text("Jetzt bitte dein *API-Secret* senden.", parse_mode="Markdown")
    return ASK_BROKER_SECRET


async def ask_broker_secret(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api_secret = update.message.text.strip()
    await _delete_message(update)
    return await finish_onboarding(
        update, context,
        broker_platform=context.user_data.get("broker_platform"),
        api_key=context.user_data.get("broker_api_key"),
        api_secret=api_secret,
    )


async def _delete_message(update: Update):
    """Löscht die Nachricht mit dem Geheimnis aus dem Chatverlauf (best effort)."""
    try:
        await update.message.delete()
    except Exception:
        pass


# ── Abschluss ───────────────────────────────────────────────────────────────

async def finish_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE,
                            broker_platform: str | None, api_key: str | None, api_secret: str | None):
    user_id = update.effective_user.id
    trade_size = context.user_data.get("trade_size_eur", TRADE_SIZE_EUR)

    db.save_profile(
        user_id,
        trade_size_eur=trade_size,
        broker_platform=broker_platform,
        broker_api_key=api_key,
        broker_api_secret=api_secret,
    )

    broker_line = f"🔌 Plattform: *{broker_platform}* (verschlüsselt gespeichert)" if broker_platform else "🔌 Keine Plattform verbunden (nur Demo-Modus)"
    await update.message.reply_text(
        "✅ *Setup abgeschlossen!*\n\n"
        f"💵 Demo-Trade-Größe: *{trade_size:.0f}$*\n"
        f"{broker_line}\n\n"
        f"Du erhältst ab jetzt täglich um {SIGNAL_TIME_HOUR:02d}:{SIGNAL_TIME_MIN:02d} Uhr Signale.",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    log.info(f"Onboarding abgeschlossen: user_id={user_id}")
    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Setup abgebrochen. Sende /start, um es erneut zu versuchen.")
    return ConversationHandler.END


# ── Handler-Aufbau ──────────────────────────────────────────────────────────

onboarding_conv_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        ASK_TRADE_SIZE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, ask_trade_size),
        ],
        ASK_BROKER_YESNO: [
            CommandHandler("skip", skip_broker),
            MessageHandler(filters.TEXT & ~filters.COMMAND, ask_broker_yesno),
        ],
        ASK_BROKER_PLATFORM: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, ask_broker_platform),
        ],
        ASK_BROKER_KEY: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, ask_broker_key),
        ],
        ASK_BROKER_SECRET: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, ask_broker_secret),
        ],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
    name="onboarding_conversation",
)
