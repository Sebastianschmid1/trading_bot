"""
📈 Stock Signal Telegram Bot
- Täglich 8:45 Uhr: 5 Aktienempfehlungen (S&P 500)
- Analyse via technische Indikatoren (RSI, MACD, MA)
- Demo-Modus: Trades werden NICHT wirklich ausgeführt
- 15:30 Uhr: Automatische Auswertung aller Empfehlungen
"""

import sys

# Windows-Konsolen nutzen standardmäßig cp1252 — kann Emojis in Log-/Print-Ausgaben nicht
# schreiben. Muss vor allen Projekt-Imports laufen, da z.B. config.py beim Import bereits
# Emojis ausgibt.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import os
import json
import logging
import asyncio
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

import db
from onboarding import onboarding_conv_handler
from analyzer import get_top_signals
from evaluator import evaluate_trades
from config import (
    TELEGRAM_TOKEN,
    SIGNAL_TIME_HOUR, SIGNAL_TIME_MIN,
    CLOSE_TIME_HOUR, CLOSE_TIME_MIN,
    BERLIN_TZ
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

TRADE_ACTIVATION_WINDOW_MIN = 15  # Zeitfenster, in dem ein Signal per JA noch gestartet werden kann

# ── Nachrichten senden ──────────────────────────────────────────────────────

async def send_signal(bot: Bot, chat_id: int, signal: dict, trade_size_eur: float,
                       job_queue=None):
    """Sendet eine einzelne Aktienempfehlung mit JA/NEIN Buttons an einen Nutzer.
    Plant (falls job_queue übergeben) das automatische Deaktivieren nach Ablauf des Zeitfensters."""
    direction_emoji = "🟢 LONG" if signal["direction"] == "long" else "🔴 SHORT"
    strength_bar = "█" * signal["strength"] + "░" * (5 - signal["strength"])

    text = (
        f"📊 *{signal['ticker']}* — {direction_emoji}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 Kurs: *${signal['price']:.2f}*\n"
        f"📈 Signal-Stärke: {strength_bar} ({signal['strength']}/5)\n"
        f"🔍 Begründung:\n"
        f"  • RSI: {signal['rsi']:.1f} → {signal['rsi_comment']}\n"
        f"  • MACD: {signal['macd_comment']}\n"
        f"  • Trend (MA50/200): {signal['trend_comment']}\n"
        f"  • Volumen: {signal['volume_comment']}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💶 Demo-Trade: *{trade_size_eur:.0f}€ {signal['direction'].upper()}*\n"
        f"⏰ Start nur innerhalb von {TRADE_ACTIVATION_WINDOW_MIN} Minuten möglich\n"
        f"⏱ Schließung: 15:30 Uhr"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ JA — Demo-Trade starten", callback_data=f"accept:{signal['ticker']}"),
            InlineKeyboardButton("❌ NEIN", callback_data=f"reject:{signal['ticker']}"),
        ]
    ])

    msg = await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )

    # Trade vormerken
    db.add_pending(chat_id, signal, msg.message_id)
    log.info(f"[{chat_id}] Signal gesendet: {signal['ticker']} ({signal['direction']})")

    if job_queue is not None:
        job_queue.run_once(
            expire_pending_trade,
            when=timedelta(minutes=TRADE_ACTIVATION_WINDOW_MIN),
            data={"chat_id": chat_id, "ticker": signal["ticker"], "message_id": msg.message_id},
            name=f"expire_{chat_id}_{signal['ticker']}_{date.today()}"
        )


async def expire_pending_trade(context: ContextTypes.DEFAULT_TYPE):
    """Job: Deaktiviert ein Signal, das nach Ablauf des Zeitfensters noch nicht bestätigt wurde."""
    job_data = context.job.data
    chat_id, ticker, message_id = job_data["chat_id"], job_data["ticker"], job_data["message_id"]

    if not db.expire_trade(chat_id, ticker):
        return  # bereits aktiviert/abgelehnt — nichts zu tun

    try:
        await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
    except Exception:
        pass
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"⏰ Zeitfenster für *{ticker}* abgelaufen — Demo-Trade wurde nicht gestartet.",
        parse_mode="Markdown"
    )
    log.info(f"[{chat_id}] Trade-Zeitfenster abgelaufen: {ticker}")


async def send_daily_signals(context: ContextTypes.DEFAULT_TYPE):
    """Job: Täglich um 8:45 Uhr die 5 besten Signale an alle aktiven Nutzer senden."""
    bot = context.bot
    now = datetime.now(BERLIN_TZ)
    log.info(f"Sende Tagessignale um {now.strftime('%H:%M')}")

    users = db.list_active_users()
    if not users:
        log.info("Keine registrierten Nutzer — überspringe Tagessignale.")
        return

    try:
        signals = get_top_signals()  # Analyse ist für alle Nutzer identisch — nur einmal berechnen
    except Exception as e:
        log.error(f"Analyse fehlgeschlagen: {e}")
        for u in users:
            await bot.send_message(chat_id=u["user_id"], text=f"⚠️ Analyse-Fehler: {e}")
        return

    if not signals:
        for u in users:
            await bot.send_message(chat_id=u["user_id"], text="⚠️ Heute keine klaren Signale gefunden.")
        return

    for u in users:
        chat_id = u["user_id"]
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"🌅 *Guten Morgen! Tagesanalyse {now.strftime('%d.%m.%Y')}*\n"
                f"Analysiere S\\&P 500 Top\\-Aktien\\.\\.\\. ⏳"
            ),
            parse_mode="MarkdownV2"
        )
        for signal in signals:
            await send_signal(bot, chat_id, signal, u["trade_size_eur"], job_queue=context.job_queue)
            await asyncio.sleep(1.5)  # kurze Pause zwischen Nachrichten
        await asyncio.sleep(0.5)  # kurze Pause zwischen Nutzern (Rate-Limit-Schutz)


async def close_and_evaluate(context: ContextTypes.DEFAULT_TYPE):
    """Job: Um 15:30 Uhr alle aktiven Trades je Nutzer schließen & auswerten."""
    bot = context.bot
    log.info("Starte Tagesauswertung...")

    for u in db.list_active_users():
        chat_id = u["user_id"]
        active = db.get_active_trades(chat_id)
        if not active:
            await bot.send_message(chat_id=chat_id, text="📭 Heute keine aktiven Demo-Trades zum Auswerten.")
            await asyncio.sleep(0.5)
            continue

        await bot.send_message(
            chat_id=chat_id,
            text="⏰ *15:30 Uhr — Schließe alle Demo-Trades und werte aus...*",
            parse_mode="Markdown"
        )

        results = evaluate_trades(active, u["trade_size_eur"])
        db.close_all(chat_id, results)

        # Zusammenfassung
        total_pnl = sum(r["pnl_eur"] for r in results)
        winners = sum(1 for r in results if r["pnl_eur"] > 0)
        losers = len(results) - winners
        pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"

        summary = (
            f"📋 *Tagesauswertung Demo-Trades*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"✅ Gewinner: {winners} | ❌ Verlierer: {losers}\n"
            f"{pnl_emoji} Gesamt P&L: *{'+' if total_pnl >= 0 else ''}{total_pnl:.2f}€*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
        )

        for r in results:
            pnl_sign = "+" if r["pnl_eur"] >= 0 else ""
            emoji = "🟢" if r["pnl_eur"] > 0 else ("🔴" if r["pnl_eur"] < 0 else "⚪")
            summary += (
                f"{emoji} *{r['ticker']}*: Einstieg ${r['entry']:.2f} → Ausstieg ${r['exit']:.2f} "
                f"| {pnl_sign}{r['pnl_pct']:.1f}% ({pnl_sign}{r['pnl_eur']:.2f}€)\n"
            )

        summary += f"\n💡 _Alle Trades waren im Demo-Modus (kein echtes Geld)_"

        await bot.send_message(chat_id=chat_id, text=summary, parse_mode="Markdown")
        log.info(f"[{chat_id}] Auswertung abgeschlossen. Gesamt P&L: {total_pnl:.2f}€")
        await asyncio.sleep(0.5)


# ── Manuelle Befehle (für registrierte Nutzer jederzeit verfügbar) ──────────

def _registered_user(chat_id: int) -> dict | None:
    """Gibt das Profil zurück, falls der Nutzer fertig eingerichtet ist, sonst None."""
    user = db.get_user(chat_id)
    if not user or user["onboarding_state"] != "complete":
        return None
    return user


async def cmd_test_telegram(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/test_telegram — prüft, ob der Bot dir Nachrichten senden kann."""
    chat_id = update.effective_chat.id
    if not _registered_user(chat_id):
        await update.message.reply_text("⚠️ Du bist noch nicht eingerichtet. Sende zuerst /start.")
        return
    await update.message.reply_text("🧪 Testnachricht — Telegram-Verbindung funktioniert!")


async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/signals — analysiert jetzt live und sendet dir die aktuellen Signale (mit JA/NEIN-Buttons)."""
    chat_id = update.effective_chat.id
    user = _registered_user(chat_id)
    if not user:
        await update.message.reply_text("⚠️ Du bist noch nicht eingerichtet. Sende zuerst /start.")
        return

    bot = context.bot
    await update.message.reply_text("🔍 Analysiere Aktien... ⏳")
    try:
        signals = get_top_signals()
    except Exception as e:
        await update.message.reply_text(f"⚠️ Analyse-Fehler: {e}")
        return

    if not signals:
        await update.message.reply_text("⚠️ Heute keine klaren Signale gefunden.")
        return

    for signal in signals:
        await send_signal(bot, chat_id, signal, user["trade_size_eur"], job_queue=context.job_queue)
        await asyncio.sleep(1)


async def cmd_evaluate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/evaluate — wertet deine aktuell aktiven Demo-Trades jetzt sofort aus (live, keine Beispieldaten)."""
    chat_id = update.effective_chat.id
    user = _registered_user(chat_id)
    if not user:
        await update.message.reply_text("⚠️ Du bist noch nicht eingerichtet. Sende zuerst /start.")
        return

    active = db.get_active_trades(chat_id)
    if not active:
        await update.message.reply_text("📭 Du hast aktuell keine aktiven Demo-Trades.")
        return

    results = evaluate_trades(active, user["trade_size_eur"])
    db.close_all(chat_id, results)

    total = sum(r["pnl_eur"] for r in results)
    sign = "+" if total >= 0 else ""

    lines = ["🧪 *Test-Auswertung*", "━━━━━━━━━━━━━━━━━━"]
    for r in results:
        rsign = "+" if r["pnl_eur"] >= 0 else ""
        emoji = "🟢" if r["pnl_eur"] > 0 else ("🔴" if r["pnl_eur"] < 0 else "⚪")
        lines.append(
            f"{emoji} *{r['ticker']}*: ${r['entry']:.2f} → ${r['exit']:.2f} "
            f"| {rsign}{r['pnl_pct']:.1f}% ({rsign}{r['pnl_eur']:.2f}€)"
        )
    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append(f"Gesamt P&L: *{sign}{total:.2f}€*")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/profile — zeigt das eigene Profil (Demo-Trade-Größe, Broker-Verbindung, Status)."""
    chat_id = update.effective_chat.id
    user = _registered_user(chat_id)
    if not user:
        await update.message.reply_text("⚠️ Du bist noch nicht eingerichtet. Sende zuerst /start.")
        return

    broker_line = (
        f"🔌 Plattform: *{user['broker_platform']}* (verschlüsselt gespeichert)"
        if user["broker_platform"] else
        "🔌 Keine Plattform verbunden (nur Demo-Modus)"
    )
    await update.message.reply_text(
        "👤 *Dein Profil*\n"
        f"💶 Demo-Trade-Größe: *{user['trade_size_eur']:.0f}€*\n"
        f"{broker_line}\n"
        f"📡 Status: {'aktiv ✅' if user['is_active'] else 'pausiert ⏸'}",
        parse_mode="Markdown"
    )


HELP_TEXT = (
    "🤖 *Verfügbare Befehle*\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "/start — Setup starten oder Status anzeigen\n"
    "/profile — Dein Profil ansehen (Trade-Größe, Broker, Status)\n"
    "/signals — Aktuelle Signale jetzt live abrufen\n"
    "/evaluate — Deine aktiven Demo-Trades jetzt auswerten\n"
    "/test_telegram — Verbindung zum Bot testen\n"
    "/cancel — Laufenden Setup-Dialog abbrechen\n"
    "/help — Diese Übersicht anzeigen\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "📅 *Täglicher Ablauf*\n"
    f"  • {SIGNAL_TIME_HOUR:02d}:{SIGNAL_TIME_MIN:02d} Uhr — Tagessignale mit JA/NEIN-Buttons\n"
    f"  • {CLOSE_TIME_HOUR:02d}:{CLOSE_TIME_MIN:02d} Uhr — automatische Auswertung aller Demo-Trades\n"
    f"  • ⏰ Start eines Trades nur innerhalb von {TRADE_ACTIVATION_WINDOW_MIN} Minuten nach dem Signal möglich"
)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/help — listet alle verfügbaren Befehle und den täglichen Ablauf auf."""
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


# ── Button-Handler ──────────────────────────────────────────────────────────

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verarbeitet JA/NEIN Button-Klicks. Der klickende Nutzer wird über die Chat-ID aufgelöst."""
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id  # == user_id, im privaten Chat eindeutig

    action, ticker = query.data.split(":")

    if action == "accept":
        trade = db.activate_trade(chat_id, ticker)
        if trade:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.edit_message_text(
                query.message.text + f"\n\n✅ *Demo-Trade gestartet!*\nEinstiegskurs: ${trade['entry']:.2f}",
                parse_mode="Markdown"
            )
            log.info(f"[{chat_id}] Trade aktiviert: {ticker} @ ${trade['entry']:.2f}")
        else:
            existing = db.get_trade(chat_id, ticker)
            if existing and existing["status"] == "expired":
                await query.answer(
                    f"⏰ Zeitfenster abgelaufen — Start ist nur innerhalb von "
                    f"{TRADE_ACTIVATION_WINDOW_MIN} Minuten möglich.",
                    show_alert=True
                )
            else:
                await query.answer("⚠️ Trade bereits aktiv oder nicht gefunden.", show_alert=True)

    elif action == "reject":
        if db.reject_trade(chat_id, ticker):
            await query.edit_message_reply_markup(reply_markup=None)
            await query.edit_message_text(
                query.message.text + "\n\n❌ *Abgelehnt*",
                parse_mode="Markdown"
            )
            log.info(f"[{chat_id}] Trade abgelehnt: {ticker}")
        else:
            await query.answer("⚠️ Trade bereits bearbeitet oder nicht gefunden.", show_alert=True)


# ── App starten ─────────────────────────────────────────────────────────────

def main():
    db.init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Onboarding-Dialog (muss vor dem CallbackQueryHandler stehen, fängt /start ab)
    app.add_handler(onboarding_conv_handler)
    # Manuelle Befehle: jederzeit aufrufbar, sobald der Bot läuft
    app.add_handler(CommandHandler("help", cmd_help))                     # Befehlsübersicht
    app.add_handler(CommandHandler("profile", cmd_profile))               # eigenes Profil ansehen
    app.add_handler(CommandHandler("test_telegram", cmd_test_telegram))   # Verbindungstest
    app.add_handler(CommandHandler("signals", cmd_signals))               # echte Live-Analyse jetzt sofort
    app.add_handler(CommandHandler("evaluate", cmd_evaluate))             # aktive Demo-Trades jetzt sofort auswerten
    # Button-Handler registrieren
    app.add_handler(CallbackQueryHandler(button_handler))

    # Jobs planen (täglich zur fixen Uhrzeit, Berliner Zeit)
    job_queue = app.job_queue

    job_queue.run_daily(
        send_daily_signals,
        time=datetime.now(BERLIN_TZ).replace(
            hour=SIGNAL_TIME_HOUR, minute=SIGNAL_TIME_MIN, second=0, microsecond=0
        ).timetz(),
        name="daily_signals"
    )

    job_queue.run_daily(
        close_and_evaluate,
        time=datetime.now(BERLIN_TZ).replace(
            hour=CLOSE_TIME_HOUR, minute=CLOSE_TIME_MIN, second=0, microsecond=0
        ).timetz(),
        name="daily_close"
    )

    log.info("🤖 Bot gestartet. Warte auf Jobs...")
    log.info(f"  → Signale: {SIGNAL_TIME_HOUR:02d}:{SIGNAL_TIME_MIN:02d} Uhr")
    log.info(f"  → Auswertung: {CLOSE_TIME_HOUR:02d}:{CLOSE_TIME_MIN:02d} Uhr")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
