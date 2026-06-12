"""
Geführter Dialog, mit dem ein Nutzer seine eigenen Alpaca-API-Zugangsdaten
über den Bot hinterlegt (/connectalpaca) oder entfernt (/disconnectalpaca).

Die Keys werden verschlüsselt gespeichert (db.set_alpaca_credentials), und die
Nachricht mit dem Geheimnis wird sofort aus dem Chat gelöscht. Vor dem Speichern
prüft der Bot die Zugangsdaten mit einem echten Alpaca-Health-Check.
"""

import asyncio
import logging

from telegram import Update
from telegram.ext import (
    ConversationHandler, CommandHandler, MessageHandler, ContextTypes, filters,
)

from stockbot.core import db
from stockbot.broker import client as broker
from stockbot import config

log = logging.getLogger(__name__)

ASK_ALPACA_KEY, ASK_ALPACA_SECRET = range(2)


async def _delete_message(update: Update):
    """Löscht die Nachricht mit dem Geheimnis aus dem Chatverlauf (best effort)."""
    try:
        await update.message.delete()
    except Exception:
        pass


def _registered(user_id: int):
    u = db.get_user(user_id)
    return u if u and u["onboarding_state"] == "complete" else None


# ── /connectalpaca ───────────────────────────────────────────────────────────

async def connect_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _registered(update.effective_user.id):
        await update.message.reply_text("⚠️ Du bist noch nicht eingerichtet. Sende zuerst /start.")
        return ConversationHandler.END

    mode = "PAPER (Demo, kein echtes Geld)" if config.ALPACA_PAPER else "LIVE (echtes Geld!)"
    await update.message.reply_text(
        "🔐 *Alpaca verbinden*\n\n"
        f"Modus: *{mode}*\n"
        "Hol dir deine API-Keys im Alpaca-Dashboard (für Paper-Trading: 'View API Keys'). "
        "Sende mir jetzt deinen *API-Key*.\n\n"
        "⚠️ Er wird verschlüsselt gespeichert und deine Nachricht sofort gelöscht. "
        "Mit /cancel abbrechen.",
        parse_mode="Markdown",
    )
    return ASK_ALPACA_KEY


async def ask_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["alpaca_key"] = update.message.text.strip()
    await _delete_message(update)
    await update.message.reply_text(
        "Jetzt bitte dein *API-Secret* senden.", parse_mode="Markdown")
    return ASK_ALPACA_SECRET


async def ask_secret(update: Update, context: ContextTypes.DEFAULT_TYPE):
    secret = update.message.text.strip()
    await _delete_message(update)
    key = context.user_data.get("alpaca_key")
    context.user_data.clear()

    if not key:
        await update.message.reply_text("⚠️ Etwas ist schiefgelaufen. Starte /connectalpaca erneut.")
        return ConversationHandler.END

    await update.message.reply_text("🔌 Prüfe die Zugangsdaten bei Alpaca… ⏳")
    client = broker.make_client(key, secret, paper=config.ALPACA_PAPER)
    res = await asyncio.to_thread(broker.health_check, client=client)

    if not res.get("ok"):
        await update.message.reply_text(
            "❌ Verbindung fehlgeschlagen — die Zugangsdaten wurden *nicht* gespeichert.\n"
            f"{res.get('detail')}\n\n"
            "Prüfe Key/Secret (und ob sie zum Paper-/Live-Modus passen) und versuche /connectalpaca erneut.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    db.set_alpaca_credentials(update.effective_user.id, key, secret)
    mode = "PAPER" if res.get("paper") else "LIVE"
    await update.message.reply_text(
        f"✅ *Alpaca verbunden!* ({mode})\n"
        f"Konto: {res.get('status')} · Buying Power ${res.get('buying_power'):,.2f}\n\n"
        "Die echte Order-Ausführung ist weiterhin *aus* — schalte sie bei Bedarf in /settings "
        "('Broker-Order') ein. Prüfen kannst du jederzeit mit /brokercheck.",
        parse_mode="Markdown",
    )
    log.info(f"Alpaca verbunden: user_id={update.effective_user.id} ({mode})")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Abgebrochen. Es wurde nichts gespeichert.")
    return ConversationHandler.END


# ── /disconnectalpaca ────────────────────────────────────────────────────────

async def disconnect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _registered(update.effective_user.id)
    if not user:
        await update.message.reply_text("⚠️ Du bist noch nicht eingerichtet. Sende zuerst /start.")
        return
    if not db.has_alpaca_credentials(update.effective_user.id):
        await update.message.reply_text("ℹ️ Es sind keine Alpaca-Zugangsdaten hinterlegt.")
        return
    db.clear_alpaca_credentials(update.effective_user.id)
    await update.message.reply_text(
        "🗑️ Alpaca getrennt — Zugangsdaten gelöscht und Broker-Ausführung ausgeschaltet.")


# ── Handler-Aufbau ──────────────────────────────────────────────────────────

connect_alpaca_handler = ConversationHandler(
    entry_points=[CommandHandler("connectalpaca", connect_start)],
    states={
        ASK_ALPACA_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_key)],
        ASK_ALPACA_SECRET: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_secret)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
    name="connect_alpaca_conversation",
)
