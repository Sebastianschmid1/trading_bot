"""
📈 Stock Signal Telegram Bot
- Täglich 8:45 Uhr: 5 Aktienempfehlungen (S&P 500)
- Analyse via technische Indikatoren (RSI, MACD, MA)
- Demo-Modus: Trades werden NICHT wirklich ausgeführt
- 15:30 Uhr: Automatische Auswertung aller Empfehlungen
"""

import os
import json
import logging
import asyncio
from datetime import datetime, date
from zoneinfo import ZoneInfo

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from analyzer import get_top_signals
from tracker import TradeTracker
from evaluator import evaluate_trades
from config import (
    TELEGRAM_TOKEN, CHAT_ID,
    SIGNAL_TIME_HOUR, SIGNAL_TIME_MIN,
    CLOSE_TIME_HOUR, CLOSE_TIME_MIN,
    BERLIN_TZ
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/bot.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

tracker = TradeTracker()


# ── Nachrichten senden ──────────────────────────────────────────────────────

async def send_signal(bot: Bot, signal: dict):
    """Sendet eine einzelne Aktienempfehlung mit JA/NEIN Buttons."""
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
        f"💶 Demo-Trade: *25€ {signal['direction'].upper()}*\n"
        f"⏱ Schließung: 15:30 Uhr"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ JA — Demo-Trade starten", callback_data=f"accept:{signal['ticker']}"),
            InlineKeyboardButton("❌ NEIN", callback_data=f"reject:{signal['ticker']}"),
        ]
    ])

    msg = await bot.send_message(
        chat_id=CHAT_ID,
        text=text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )

    # Trade vormerken
    tracker.add_pending(signal, msg.message_id)
    log.info(f"Signal gesendet: {signal['ticker']} ({signal['direction']})")


async def send_daily_signals(context: ContextTypes.DEFAULT_TYPE):
    """Job: Täglich um 8:45 Uhr die 5 besten Signale senden."""
    bot = context.bot
    now = datetime.now(BERLIN_TZ)
    log.info(f"Sende Tagessignale um {now.strftime('%H:%M')}")

    await bot.send_message(
        chat_id=CHAT_ID,
        text=(
            f"🌅 *Guten Morgen! Tagesanalyse {now.strftime('%d.%m.%Y')}*\n"
            f"Analysiere S\\&P 500 Top\\-Aktien\\.\\.\\. ⏳"
        ),
        parse_mode="MarkdownV2"
    )

    try:
        signals = get_top_signals()
    except Exception as e:
        log.error(f"Analyse fehlgeschlagen: {e}")
        await bot.send_message(chat_id=CHAT_ID, text=f"⚠️ Analyse-Fehler: {e}")
        return

    if not signals:
        await bot.send_message(chat_id=CHAT_ID, text="⚠️ Heute keine klaren Signale gefunden.")
        return

    for signal in signals:
        await send_signal(bot, signal)
        await asyncio.sleep(1.5)  # kurze Pause zwischen Nachrichten


async def close_and_evaluate(context: ContextTypes.DEFAULT_TYPE):
    """Job: Um 15:30 Uhr alle aktiven Trades schließen & auswerten."""
    bot = context.bot
    log.info("Starte Tagesauswertung...")

    active = tracker.get_active_trades()
    if not active:
        await bot.send_message(chat_id=CHAT_ID, text="📭 Heute keine aktiven Demo-Trades zum Auswerten.")
        return

    await bot.send_message(
        chat_id=CHAT_ID,
        text="⏰ *15:30 Uhr — Schließe alle Demo-Trades und werte aus...*",
        parse_mode="Markdown"
    )

    results = evaluate_trades(active)
    tracker.close_all(results)

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

    await bot.send_message(chat_id=CHAT_ID, text=summary, parse_mode="Markdown")
    log.info(f"Auswertung abgeschlossen. Gesamt P&L: {total_pnl:.2f}€")


# ── Button-Handler ──────────────────────────────────────────────────────────

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verarbeitet JA/NEIN Button-Klicks."""
    query = update.callback_query
    await query.answer()

    action, ticker = query.data.split(":")

    if action == "accept":
        success = tracker.activate_trade(ticker)
        if success:
            trade = tracker.get_trade(ticker)
            await query.edit_message_reply_markup(reply_markup=None)
            await query.edit_message_text(
                query.message.text + f"\n\n✅ *Demo-Trade gestartet!*\nEinstiegskurs: ${trade['entry']:.2f}",
                parse_mode="Markdown"
            )
            log.info(f"Trade aktiviert: {ticker} @ ${trade['entry']:.2f}")
        else:
            await query.answer("⚠️ Trade bereits aktiv oder nicht gefunden.", show_alert=True)

    elif action == "reject":
        tracker.reject_trade(ticker)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.edit_message_text(
            query.message.text + "\n\n❌ *Abgelehnt*",
            parse_mode="Markdown"
        )
        log.info(f"Trade abgelehnt: {ticker}")


# ── App starten ─────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

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
