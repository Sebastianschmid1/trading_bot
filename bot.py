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
from telegram.error import Conflict
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

import db
import universes
import smartmoney
from onboarding import onboarding_conv_handler
from analyzer import analyze_universe, scan_strengths
from evaluator import evaluate_trades, get_current_price
from config import (
    TELEGRAM_TOKEN,
    SIGNAL_TIME_HOUR, SIGNAL_TIME_MIN,
    CLOSE_TIME_HOUR, CLOSE_TIME_MIN,
    BERLIN_TZ, DASHBOARD_BASE_URL, RUN_DASHBOARD_IN_BOT,
    UNIVERSES, REGION_LABELS, DEFAULT_REGION, SIGNAL_COUNT_CHOICES, TOP_N_SIGNALS,
    SMARTMONEY_SCAN_HOUR, SMARTMONEY_SCAN_MIN,
    SIGNAL_CLOSE_THRESHOLD, MONITOR_INTERVAL_SEC
)

os.makedirs("logs", exist_ok=True)   # Log-Ordner sicherstellen (fehlt bei frischem Klon)
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
                       job_queue=None, market_open: bool = True) -> bool:
    """Sendet eine einzelne Aktienempfehlung an einen Nutzer.

    - Bei offener Börse: JA/NEIN-Buttons, legt einen handelbaren Demo-Trade an und plant
      (falls job_queue übergeben) das automatische Ablaufen/Löschen nach dem Zeitfenster.
    - Bei geschlossener Börse: nur ein deaktivierter "Börse geschlossen"-Button, kein Trade.

    Duplikat-Schutz: pro Aktie/Tag wird nur EIN handelbares Signal angelegt.
    Rückgabe: True, wenn gesendet; False, wenn als Duplikat übersprungen.
    """
    ticker = signal["ticker"]

    if market_open and db.has_trade_today(chat_id, ticker):
        log.info(f"[{chat_id}] Signal übersprungen (heute schon vorhanden): {ticker}")
        return False

    direction_emoji = "🟢 LONG" if signal["direction"] == "long" else "🔴 SHORT"
    filled = int(round(signal["strength"] / 10))   # 0–100 → 10er-Balken
    strength_bar = "█" * filled + "░" * (10 - filled)

    # ATR-basierte Risiko-Level (falls vorhanden)
    if signal.get("stop_loss") and signal.get("take_profit"):
        risk_block = (
            f"🎯 Take-Profit: *${signal['take_profit']:.2f}* (+{signal['tp_pct']:.1f}%)\n"
            f"🛑 Stop-Loss: *${signal['stop_loss']:.2f}* ({signal['sl_pct']:.1f}%)\n"
            f"⚖️ Chance/Risiko: ~1:{signal['risk_reward']:.1f}\n"
        )
    else:
        risk_block = ""

    # Smart-Money-Zeile (falls Score aus dem nächtlichen Scan vorliegt)
    sm = signal.get("smart_money")
    if sm:
        sm_stars = "★" * sm["stars"] + "☆" * (5 - sm["stars"])
        sm_line = f"  • 🐳 Smart-Money: {sm_stars} (Score {sm['score']})\n"
    else:
        sm_line = ""

    if market_open:
        footer = (
            f"⏰ Start nur innerhalb von {TRADE_ACTIVATION_WINDOW_MIN} Minuten möglich\n"
            f"⏱ Auswertung: {CLOSE_TIME_HOUR:02d}:{CLOSE_TIME_MIN:02d} Uhr (oder früher bei SL/TP)"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ JA — Demo-Trade starten", callback_data=f"accept:{ticker}"),
                InlineKeyboardButton("❌ NEIN", callback_data=f"reject:{ticker}"),
            ]
        ])
    else:
        footer = "🔒 US-Börse geschlossen — Start möglich, sobald der Markt wieder öffnet."
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔒 Börse geschlossen", callback_data=f"noop:{ticker}")]
        ])

    text = (
        f"📊 *{ticker}* — {direction_emoji}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 Kurs: *${signal['price']:.2f}*\n"
        f"📈 Signal-Stärke: {strength_bar} ({signal['strength']:.0f}/100)\n"
        f"🔍 Begründung:\n"
        f"  • RSI: {signal['rsi']:.1f} → {signal['rsi_comment']}\n"
        f"  • MACD: {signal['macd_comment']}\n"
        f"  • Trend (MA50/200): {signal['trend_comment']}\n"
        f"  • Wochentrend: {signal.get('weekly_comment', '—')}\n"
        f"  • Volumen: {signal['volume_comment']}\n"
        f"  • Level: {signal.get('sr_comment', '—')}\n"
        f"{sm_line}"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{risk_block}"
        f"💶 Demo-Trade: *{trade_size_eur:.0f}€ {signal['direction'].upper()}*\n"
        f"{footer}"
    )

    msg = await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )

    if market_open:
        # Handelbaren Trade vormerken + Ablauf planen
        db.add_pending(chat_id, signal, msg.message_id)
        log.info(f"[{chat_id}] Signal gesendet: {ticker} ({signal['direction']})")
        if job_queue is not None:
            job_queue.run_once(
                expire_pending_trade,
                when=timedelta(minutes=TRADE_ACTIVATION_WINDOW_MIN),
                data={"chat_id": chat_id, "ticker": ticker, "message_id": msg.message_id},
                name=f"expire_{chat_id}_{ticker}_{date.today()}"
            )
    else:
        log.info(f"[{chat_id}] Info-Signal (Börse geschlossen): {ticker}")

    return True


async def expire_pending_trade(context: ContextTypes.DEFAULT_TYPE):
    """Job: Läuft ein Signal nach dem Zeitfenster ab, wird die Telegram-Nachricht gelöscht."""
    job_data = context.job.data
    chat_id, ticker, message_id = job_data["chat_id"], job_data["ticker"], job_data["message_id"]

    if not db.expire_trade(chat_id, ticker):
        return  # bereits aktiviert/abgelehnt — Nachricht bleibt bestehen

    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass
    log.info(f"[{chat_id}] Signal abgelaufen & Nachricht gelöscht: {ticker}")


async def send_daily_signals(context: ContextTypes.DEFAULT_TYPE):
    """Job: Täglich um 8:45 Uhr die 5 besten Signale an alle aktiven Nutzer senden."""
    bot = context.bot
    now = datetime.now(BERLIN_TZ)
    log.info(f"Sende Tagessignale um {now.strftime('%H:%M')}")

    users = db.list_active_users()
    if not users:
        log.info("Keine registrierten Nutzer — überspringe Tagessignale.")
        return

    # Analyse pro benötigtem Markt-Bereich einmal berechnen (für alle Nutzer dieses Bereichs)
    ranked_by_region: dict[str, list] = {}
    for region in {u.get("market_region") or DEFAULT_REGION for u in users}:
        tickers = universes.get_tickers(region)
        try:
            ranked_by_region[region] = analyze_universe(tickers)
        except Exception as e:
            log.error(f"Analyse fehlgeschlagen ({region}): {e}")
            ranked_by_region[region] = None  # Fehler-Marker

    market_open = _us_market_open()
    for u in users:
        chat_id = u["user_id"]
        region = u.get("market_region") or DEFAULT_REGION
        ranked = ranked_by_region.get(region)

        if ranked is None:
            await bot.send_message(chat_id=chat_id, text="⚠️ Analyse-Fehler — bitte später erneut versuchen.")
            continue

        # Smart-Money-Score einblenden + danach neu reihen, dann auf Wunsch-Anzahl kürzen
        signals = smartmoney.rank(ranked, u.get("top_n_signals") or TOP_N_SIGNALS)
        if not signals:
            await bot.send_message(chat_id=chat_id, text="⚠️ Heute keine klaren Signale gefunden.")
            continue

        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"🌅 *Guten Morgen! Tagesanalyse {now.strftime('%d.%m.%Y')}*\n"
                f"Bereich: {REGION_LABELS.get(region, region)} ⏳"
            ),
            parse_mode="Markdown"
        )
        for signal in signals:
            await send_signal(bot, chat_id, signal, u["trade_size_eur"],
                              job_queue=context.job_queue, market_open=market_open)
            await asyncio.sleep(1.5)  # kurze Pause zwischen Nachrichten
        await asyncio.sleep(0.5)  # kurze Pause zwischen Nutzern (Rate-Limit-Schutz)


async def close_and_evaluate(context: ContextTypes.DEFAULT_TYPE):
    """Job: Nach US-Börsenschluss alle aktiven Trades je Nutzer schließen & auswerten."""
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
            text=f"⏰ *{CLOSE_TIME_HOUR:02d}:{CLOSE_TIME_MIN:02d} Uhr — Schließe alle Demo-Trades und werte aus...*",
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
                f"    └ {r.get('exit_reason', 'Schlusskurs')}\n"
            )

        summary += f"\n💡 _Alle Trades waren im Demo-Modus (kein echtes Geld)_"

        await bot.send_message(chat_id=chat_id, text=summary, parse_mode="Markdown")
        log.info(f"[{chat_id}] Auswertung abgeschlossen. Gesamt P&L: {total_pnl:.2f}€")
        await asyncio.sleep(0.5)


# ── 60s-Monitoring aktiver Trades (Auto-Close) ──────────────────────────────

def evaluate_active_trade(trade: dict, price: float | None, strength: float | None) -> str | None:
    """Entscheidet, ob ein aktiver Trade geschlossen werden soll.
    Gibt den Grund zurück (oder None, wenn er offen bleibt)."""
    sig = trade.get("signal", {})
    sl, tp = sig.get("stop_loss"), sig.get("take_profit")
    if price is not None and trade.get("direction", "long") == "long":
        if sl is not None and price <= sl:
            return "Stop-Loss 🛑"
        if tp is not None and price >= tp:
            return "Take-Profit 🎯"
    if strength is not None and strength < SIGNAL_CLOSE_THRESHOLD:
        return "Signal verschlechtert 📉"
    return None


async def monitor_trades(context: ContextTypes.DEFAULT_TYPE):
    """Job (alle 60s): aktive Trades prüfen, Verlauf aufzeichnen, bei SL/TP oder
    Signal-Verfall automatisch schließen. Läuft nur bei offenem Markt & aktiven Trades."""
    if not _us_market_open():
        return

    # aktive Trades aller Nutzer sammeln + eindeutige Ticker
    active_by_user: dict[int, tuple[dict, list]] = {}
    tickers: set[str] = set()
    for u in db.list_active_users():
        act = db.get_active_trades(u["user_id"])
        if act:
            active_by_user[u["user_id"]] = (u, act)
            tickers.update(t["ticker"] for t in act)
    if not tickers:
        return

    # Live-Kurs + Stärke je Ticker (blockierende yfinance-Aufrufe → Thread)
    data = await asyncio.to_thread(scan_strengths, sorted(tickers))

    for uid, (user, act) in active_by_user.items():
        for trade in act:
            info = data.get(trade["ticker"])
            if not info:
                continue
            price, strength = info["price"], info["strength"]
            db.add_tick(uid, trade["ticker"], price, strength)   # Verlauf für die Charts

            reason = evaluate_active_trade(trade, price, strength)
            if not reason or price is None:
                continue

            entry = trade.get("entry") or price
            pnl_pct = (price - entry) / entry * 100 if entry else 0.0
            pnl_eur = user["trade_size_eur"] * (pnl_pct / 100)
            db.close_all(uid, [{"ticker": trade["ticker"], "exit": price,
                                "pnl_eur": pnl_eur, "pnl_pct": pnl_pct}])

            sign = "+" if pnl_eur >= 0 else ""
            emoji = "🟢" if pnl_eur > 0 else ("🔴" if pnl_eur < 0 else "⚪")
            await context.bot.send_message(
                chat_id=uid,
                text=(f"{emoji} *{trade['ticker']} automatisch geschlossen* — {reason}\n"
                      f"Verkauf zu ${price:.2f} · Realisiert: {sign}{pnl_pct:.1f}% ({sign}{pnl_eur:.2f}€)"),
                parse_mode="Markdown",
            )
            log.info(f"[{uid}] Auto-Close {trade['ticker']} ({reason}) {sign}{pnl_eur:.2f}€")


# ── Smart-Money: nächtlicher Scan (was große Trader handeln) ─────────────────

def _regions_in_use() -> set[str]:
    """Markt-Bereiche aller aktiven Nutzer (für den Scan-Umfang)."""
    return {u.get("market_region") or DEFAULT_REGION for u in db.list_active_users()} or {DEFAULT_REGION}


async def scan_smart_money(context: ContextTypes.DEFAULT_TYPE):
    """Job: Scannt nachts die genutzten Universen auf Insider-/Institutionen-Aktivität
    und cacht die Smart-Money-Scores (langsam → läuft im Hintergrund, blockiert nichts)."""
    tickers: set[str] = set()
    for region in _regions_in_use():
        tickers.update(universes.get_tickers(region))
    log.info(f"Starte Smart-Money-Scan über {len(tickers)} Aktien…")
    # in einen Thread auslagern: die yfinance-Aufrufe sind blockierend
    await asyncio.to_thread(smartmoney.scan_universe, sorted(tickers), 0.2)
    log.info("Smart-Money-Scan abgeschlossen.")


# ── Manuelle Befehle (für registrierte Nutzer jederzeit verfügbar) ──────────

def _registered_user(chat_id: int) -> dict | None:
    """Gibt das Profil zurück, falls der Nutzer fertig eingerichtet ist, sonst None."""
    user = db.get_user(chat_id)
    if not user or user["onboarding_state"] != "complete":
        return None
    return user


def _us_market_open() -> bool:
    """Grobe Prüfung, ob die US-Börse gerade offen ist (Wochentag + 9:30–16:00 ET).
    Feiertage werden nicht berücksichtigt — reicht, um Wochenend-/Nachtdaten zu erkennen."""
    now = datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= minutes <= 16 * 60


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/ping — prüft, ob der Bot dir Nachrichten senden kann."""
    chat_id = update.effective_chat.id
    if not _registered_user(chat_id):
        await update.message.reply_text("⚠️ Du bist noch nicht eingerichtet. Sende zuerst /start.")
        return
    await update.message.reply_text("🧪 Pong — Telegram-Verbindung funktioniert!")


async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/signals — analysiert jetzt live und sendet dir die aktuellen Signale (mit JA/NEIN-Buttons)."""
    chat_id = update.effective_chat.id
    user = _registered_user(chat_id)
    if not user:
        await update.message.reply_text("⚠️ Du bist noch nicht eingerichtet. Sende zuerst /start.")
        return

    bot = context.bot
    region = user.get("market_region") or DEFAULT_REGION
    top_n = user.get("top_n_signals") or TOP_N_SIGNALS
    tickers = universes.get_tickers(region)
    region_label = REGION_LABELS.get(region, region)

    await update.message.reply_text(f"🔍 Analysiere *{region_label}*… ⏳", parse_mode="Markdown")
    try:
        ranked = analyze_universe(tickers)
        signals = smartmoney.rank(ranked, top_n)   # Smart-Money fließt ins Ranking ein
    except Exception as e:
        await update.message.reply_text(f"⚠️ Analyse-Fehler: {e}")
        return

    if not signals:
        await update.message.reply_text("⚠️ Heute keine klaren Signale gefunden.")
        return

    as_of = signals[0].get("as_of", "?")
    market_open = _us_market_open()
    if market_open:
        intro = f"✅ *{len(signals)} Live-Signale* — {region_label} (Datenstand: {as_of})"
    else:
        intro = (
            f"📅 *{len(signals)} Signale* — {region_label}, Datenstand: *{as_of}* (letzter Handelstag)\n"
            "Die US-Börse ist gerade geschlossen, daher ändern sich die Signale bis zur "
            "nächsten Handelssession nicht. Es sind echte Kurse, *keine Testdaten*."
        )
    await update.message.reply_text(intro, parse_mode="Markdown")

    sent = 0
    for signal in signals:
        if await send_signal(bot, chat_id, signal, user["trade_size_eur"],
                             job_queue=context.job_queue, market_open=market_open):
            sent += 1
        await asyncio.sleep(1)

    if market_open and sent == 0:
        await update.message.reply_text(
            "ℹ️ Alle heutigen Signale hattest du bereits — pro Aktie gibt es nur ein Signal pro Tag."
        )


def _unrealized_pnl(trade: dict, trade_size_eur: float):
    """Aktuellen (unrealisierten) Stand eines aktiven Trades berechnen — echte Kurse, kein Schließen."""
    entry = trade["entry"]
    direction = trade["direction"]
    current = get_current_price(trade["ticker"], entry)
    if direction == "long":
        pnl_pct = (current - entry) / entry * 100
    else:
        pnl_pct = (entry - current) / entry * 100
    pnl_eur = trade_size_eur * (pnl_pct / 100)
    return current, pnl_pct, pnl_eur


def _trade_card(trade: dict, trade_size_eur: float):
    """Baut Nachrichtentext + Verkaufen-Button für einen aktiven Demo-Trade."""
    ticker = trade["ticker"]
    current, pnl_pct, pnl_eur = _unrealized_pnl(trade, trade_size_eur)
    emoji = "🟢" if pnl_eur > 0 else ("🔴" if pnl_eur < 0 else "⚪")
    sign = "+" if pnl_eur >= 0 else ""
    text = (
        f"📊 *{ticker}* — aktiver Demo-Trade ({trade['direction'].upper()})\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 Einstieg: ${trade['entry']:.2f}\n"
        f"📈 Aktuell: ${current:.2f}\n"
        f"{emoji} Unrealisiert: {sign}{pnl_pct:.1f}% ({sign}{pnl_eur:.2f}€)"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Verkaufen", callback_data=f"sell:{ticker}")]
    ])
    return text, keyboard


async def cmd_evaluate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/evaluate — zeigt deine aktiven Demo-Trades mit aktuellem P&L; jeder lässt sich per Button verkaufen."""
    chat_id = update.effective_chat.id
    user = _registered_user(chat_id)
    if not user:
        await update.message.reply_text("⚠️ Du bist noch nicht eingerichtet. Sende zuerst /start.")
        return

    active = db.get_active_trades(chat_id)
    if not active:
        await update.message.reply_text("📭 Du hast aktuell keine aktiven Demo-Trades.")
        return

    await update.message.reply_text(
        "📊 *Deine aktiven Demo-Trades*\nDu kannst jeden einzeln verkaufen oder bis zur "
        f"automatischen Auswertung um {CLOSE_TIME_HOUR:02d}:{CLOSE_TIME_MIN:02d} Uhr laufen lassen.",
        parse_mode="Markdown"
    )
    for trade in active:
        text, keyboard = _trade_card(trade, user["trade_size_eur"])
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=keyboard)
        await asyncio.sleep(0.3)


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
    region = user.get("market_region") or DEFAULT_REGION
    await update.message.reply_text(
        "👤 *Dein Profil*\n"
        f"💶 Demo-Trade-Größe: *{user['trade_size_eur']:.0f}€*\n"
        f"🌍 Markt-Bereich: *{REGION_LABELS.get(region, region)}*\n"
        f"🔢 Signale pro Tag: *{user.get('top_n_signals') or TOP_N_SIGNALS}*\n"
        f"{broker_line}\n"
        f"📡 Status: {'aktiv ✅' if user['is_active'] else 'pausiert ⏸'}\n\n"
        "⚙️ Markt-Bereich & Anzahl ändern: /settings",
        parse_mode="Markdown"
    )


def _settings_view(user: dict):
    """Baut Text + Inline-Tastatur für /settings (Markt-Bereich + Anzahl Signale)."""
    region = user.get("market_region") or DEFAULT_REGION
    top_n = user.get("top_n_signals") or TOP_N_SIGNALS

    text = (
        "⚙️ *Einstellungen*\n"
        f"🌍 Markt-Bereich: *{REGION_LABELS.get(region, region)}*\n"
        f"🔢 Signale pro Tag: *{top_n}*\n\n"
        "Tippe unten, um zu ändern:"
    )

    region_row = [
        InlineKeyboardButton(
            ("✅ " if key == region else "") + label,
            callback_data=f"set_region:{key}",
        )
        for key, label in REGION_LABELS.items()
    ]
    count_row = [
        InlineKeyboardButton(
            ("✅ " if n == top_n else "") + str(n),
            callback_data=f"set_count:{n}",
        )
        for n in SIGNAL_COUNT_CHOICES
    ]
    keyboard = InlineKeyboardMarkup([region_row, count_row])
    return text, keyboard


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/settings — Markt-Bereich (S&P 500 / MSCI World / Emerging Markets) und Signal-Anzahl wählen."""
    chat_id = update.effective_chat.id
    user = _registered_user(chat_id)
    if not user:
        await update.message.reply_text("⚠️ Du bist noch nicht eingerichtet. Sende zuerst /start.")
        return
    text, keyboard = _settings_view(user)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


INFO_TEXT = (
    "📖 *So entstehen die Signale*\n"
    "Jede Aktie wird mit mehreren technischen Indikatoren geprüft. Stimmen sie überein, "
    "entsteht ein Long-Signal mit einer Stärke von 1–5.\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "📉 *RSI (Relative Strength Index)*\n"
    "Misst, ob eine Aktie über- oder unterverkauft ist (0–100). Unter ~35 = überverkauft "
    "(Erholung wahrscheinlich, bullish), über ~65 = überkauft.\n\n"
    "📈 *MACD*\n"
    "Vergleicht zwei gleitende Durchschnitte und zeigt Momentum. Ein bullishes Crossover "
    "(MACD über Signallinie) deutet auf steigendes Aufwärts-Momentum.\n\n"
    "📊 *Trend (MA50/200)*\n"
    "Lage des Kurses zu den 50- und 200-Tage-Durchschnitten. Kurs über beiden = "
    "Aufwärtstrend; MA50 über MA200 = übergeordnet bullish.\n\n"
    "🗓 *Wochentrend*\n"
    "Höheres Zeitfenster: Trend auf Wochenbasis. Signale gegen einen klaren Wochen-"
    "Abwärtstrend werden herausgefiltert (weniger Fehlsignale).\n\n"
    "🔊 *Volumen*\n"
    "Handelsvolumen vs. 20-Tage-Schnitt. Hohes Volumen (>1,5×) bestätigt das Interesse "
    "hinter einer Bewegung.\n\n"
    "🎯 *Level (Support/Widerstand)*\n"
    "Wichtige Kursmarken aus vergangenen Hoch-/Tiefpunkten, inkl. wie oft sie getestet "
    "wurden. Nähe zur Unterstützung = günstigerer Einstieg.\n\n"
    "🐳 *Smart-Money*\n"
    "Was große/informierte Trader tun: *Insider* (Vorstände/Direktoren, SEC Form 4, ~2 Tage "
    "Verzug) und *Institutionen* (Fonds wie BlackRock/Vanguard, SEC 13F, quartalsweise). "
    "Netto-Käufe & aufstockende Fonds → hoher Score (0–100). Fließt ins Signal-Ranking ein; "
    "Details siehe /top5trade.\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "⭐ *Signal-Stärke 0–100*: gewichtetes Mittel mehrerer Zeiträume (5m/15m/1h/1d) — "
    "aktuellere Zeiträume zählen mehr. Aktive Trades werden alle 60s neu bewertet und bei "
    "Stark-Verfall, Stop-Loss oder Take-Profit automatisch geschlossen.\n"
    "🛑 *Stop-Loss / 🎯 Take-Profit*: automatisch aus der Schwankungsbreite (ATR) berechnet.\n"
    "_Hinweis: Alles Demo-Modus — kein echtes Geld. Smart-Money-Daten haben Verzug, keine Garantie._"
)


async def cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/info — erklärt, wie die Signale zustande kommen (RSI, MACD, Trend, Wochentrend, Volumen, Level, Smart-Money)."""
    await update.message.reply_text(INFO_TEXT, parse_mode="Markdown")


async def cmd_top5trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/top5trade — zeigt, was große Trader (Insider + Institutionen) zuletzt am stärksten gekauft haben."""
    chat_id = update.effective_chat.id
    user = _registered_user(chat_id)
    if not user:
        await update.message.reply_text("⚠️ Du bist noch nicht eingerichtet. Sende zuerst /start.")
        return

    region = user.get("market_region") or DEFAULT_REGION
    region_label = REGION_LABELS.get(region, region)
    tickers = universes.get_tickers(region)
    top = smartmoney.get_top(tickers, 5)

    if not top:
        # Cache leer → einmaligen Hintergrund-Scan anstoßen (kein Blockieren)
        if context.job_queue is not None:
            context.job_queue.run_once(scan_smart_money, when=1, name="smartmoney_manual")
        await update.message.reply_text(
            "🐳 Die Smart-Money-Daten werden gerade berechnet (kann ein paar Minuten dauern, "
            "da SEC-Daten je Aktie geladen werden). Sende danach erneut /top5trade."
        )
        return

    scanned = smartmoney.last_scanned()
    stand = datetime.fromtimestamp(scanned, BERLIN_TZ).strftime("%d.%m.%Y %H:%M") if scanned else "?"
    lines = [f"🐳 *Top 5 — was große Trader handeln* ({region_label})", f"_Stand: {stand}_", ""]

    for s in top:
        stars = "★" * s["stars"] + "☆" * (5 - s["stars"])
        lines.append(f"📊 *{s['ticker']}* — {stars} (Score {s['score']})")

        net = s.get("insider_net")
        if net is not None:
            buys, sells = s.get("insider_buys") or 0, s.get("insider_sells") or 0
            sign = "+" if net >= 0 else ""
            lines.append(f"   👤 Insider: Netto {sign}{net:,.0f} Aktien ({buys:.0f} Käufe / {sells:.0f} Verkäufe, 6M)")

        if s.get("inst_total"):
            avg = (s.get("inst_avg_change") or 0) * 100
            lines.append(f"   🏛 Institutionen: Ø {avg:+.1f}% Bestand ({s.get('inst_added') or 0}/{s['inst_total']} Top-Halter aufgestockt)")

        lb = s.get("largest_buy")
        if lb and lb.get("value"):
            lines.append(f"   🔎 Größter Insider-Kauf: ${lb['value']:,.0f} ({lb.get('date', '')[:10]})")
        lines.append("")

    lines.append("_Quelle: SEC-Pflichtmeldungen via Yahoo (Insider ~2 Tage, Institutionen quartalsweise) — keine Garantie._")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/dashboard — sendet den persönlichen Link zum Web-Dashboard."""
    chat_id = update.effective_chat.id
    if not _registered_user(chat_id):
        await update.message.reply_text("⚠️ Du bist noch nicht eingerichtet. Sende zuerst /start.")
        return

    token = db.get_or_create_dashboard_token(chat_id)
    url = f"{DASHBOARD_BASE_URL.rstrip('/')}/dashboard/{token}"
    # Kein Markdown: der Token enthält oft _ und -, was Markdown-Parsing bricht.
    await update.message.reply_text(
        "📊 Dein persönliches Dashboard\n"
        f"{url}\n\n"
        "🔒 Der Link ist privat — teile ihn nicht. "
        "Er zeigt deine Equity-Kurve, Trefferquote, P&L pro Ticker und aktive Trades.",
        disable_web_page_preview=True,
    )


HELP_TEXT = (
    "🤖 *Verfügbare Befehle*\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "/start — Setup starten oder Status anzeigen\n"
    "/profile — Dein Profil ansehen (Trade-Größe, Markt, Broker, Status)\n"
    "/settings — Markt-Bereich & Anzahl Signale ändern\n"
    "/dashboard — Link zu deinem Web-Dashboard\n"
    "/signals — Aktuelle Signale jetzt live abrufen\n"
    "/top5trade — Was große Trader (Insider + Institutionen) zuletzt gekauft haben\n"
    "/evaluate — Deine aktiven Demo-Trades jetzt auswerten\n"
    "/info — Wie kommen die Signale zustande? (Metriken erklärt)\n"
    "/ping — Verbindung zum Bot testen\n"
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
    """Verarbeitet JA/NEIN/Verkaufen-Button-Klicks. Der klickende Nutzer wird über die Chat-ID aufgelöst."""
    query = update.callback_query
    chat_id = update.effective_chat.id  # == user_id, im privaten Chat eindeutig

    action, ticker = query.data.split(":")

    # Deaktivierter "Börse geschlossen"-Button: nur Hinweis, keine Aktion
    if action == "noop":
        await query.answer("🔒 Börse geschlossen — Trade-Start erst bei geöffnetem Markt.", show_alert=True)
        return

    await query.answer()

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

    elif action == "sell":
        user = db.get_user(chat_id)
        trade = db.get_trade(chat_id, ticker)
        if not user or not trade or trade["status"] != "active":
            await query.answer("⚠️ Trade ist nicht mehr aktiv.", show_alert=True)
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            return

        entry = trade["entry"]
        current = get_current_price(ticker, entry)
        if trade["direction"] == "long":
            pnl_pct = (current - entry) / entry * 100
        else:
            pnl_pct = (entry - current) / entry * 100
        pnl_eur = user["trade_size_eur"] * (pnl_pct / 100)

        db.close_all(chat_id, [{
            "ticker": ticker, "exit": current, "pnl_eur": pnl_eur, "pnl_pct": pnl_pct,
        }])

        sign = "+" if pnl_eur >= 0 else ""
        emoji = "🟢" if pnl_eur > 0 else ("🔴" if pnl_eur < 0 else "⚪")
        await query.edit_message_reply_markup(reply_markup=None)
        await query.edit_message_text(
            query.message.text +
            f"\n\n{emoji} *Verkauft zu ${current:.2f}*\n"
            f"Realisiert: {sign}{pnl_pct:.1f}% ({sign}{pnl_eur:.2f}€)",
            parse_mode="Markdown"
        )
        log.info(f"[{chat_id}] Trade verkauft: {ticker} @ ${current:.2f} ({sign}{pnl_eur:.2f}€)")

    elif action == "set_region":
        # 'ticker' enthält hier den Region-Schlüssel (z. B. 'sp500')
        if ticker in UNIVERSES:
            db.set_market_region(chat_id, ticker)
            log.info(f"[{chat_id}] Markt-Bereich geändert: {ticker}")
        user = db.get_user(chat_id)
        if user:
            text, keyboard = _settings_view(user)
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)

    elif action == "set_count":
        # 'ticker' enthält hier die Zahl als String
        try:
            db.set_top_n(chat_id, int(ticker))
            log.info(f"[{chat_id}] Anzahl Signale geändert: {ticker}")
        except ValueError:
            pass
        user = db.get_user(chat_id)
        if user:
            text, keyboard = _settings_view(user)
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)


# ── Fehlerbehandlung ────────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Globaler Error-Handler: loggt Fehler kompakt statt als rohen Traceback."""
    err = context.error

    if isinstance(err, Conflict):
        # Tritt auf, wenn mehr als eine Bot-Instanz gleichzeitig pollt.
        log.error(
            "⚠️ Conflict: Es läuft bereits eine andere Bot-Instanz (getUpdates). "
            "Es darf nur EINE Instanz laufen — beende die übrigen Prozesse."
        )
        return

    log.error(f"Fehler bei Update-Verarbeitung: {err.__class__.__name__}: {err}")

    # Falls möglich, dem Nutzer eine kurze Rückmeldung geben (z. B. bei Befehl-Fehlern)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ Es ist ein Fehler aufgetreten. Bitte versuche es erneut.")
        except Exception:
            pass


# ── App starten ─────────────────────────────────────────────────────────────

def _start_dashboard_thread():
    """Startet das Web-Dashboard im Hintergrund-Thread, damit `python bot.py` alles bereitstellt."""
    import threading
    from dashboard import run as run_dashboard

    def _runner():
        try:
            run_dashboard()
        except Exception as e:
            log.warning(f"Dashboard konnte nicht gestartet werden ({e}). Bot läuft ohne Dashboard weiter.")

    threading.Thread(target=_runner, daemon=True, name="dashboard").start()
    log.info(f"📊 Dashboard aktiv — Link: {DASHBOARD_BASE_URL}")


def main():
    db.init_db()

    if RUN_DASHBOARD_IN_BOT:
        _start_dashboard_thread()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Onboarding-Dialog (muss vor dem CallbackQueryHandler stehen, fängt /start ab)
    app.add_handler(onboarding_conv_handler)
    # Manuelle Befehle: jederzeit aufrufbar, sobald der Bot läuft
    app.add_handler(CommandHandler("help", cmd_help))                     # Befehlsübersicht
    app.add_handler(CommandHandler("profile", cmd_profile))               # eigenes Profil ansehen
    app.add_handler(CommandHandler("settings", cmd_settings))             # Markt-Bereich + Anzahl ändern
    app.add_handler(CommandHandler("info", cmd_info))                     # Metriken erklärt
    app.add_handler(CommandHandler("dashboard", cmd_dashboard))           # Link zum Web-Dashboard
    app.add_handler(CommandHandler("ping", cmd_ping))                     # Verbindungstest
    app.add_handler(CommandHandler("signals", cmd_signals))               # echte Live-Analyse jetzt sofort
    app.add_handler(CommandHandler("top5trade", cmd_top5trade))           # was große Trader handeln
    app.add_handler(CommandHandler("evaluate", cmd_evaluate))             # aktive Demo-Trades jetzt sofort auswerten
    # Button-Handler registrieren
    app.add_handler(CallbackQueryHandler(button_handler))
    # Globaler Error-Handler (saubere Logs statt Tracebacks)
    app.add_error_handler(error_handler)

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

    job_queue.run_daily(
        scan_smart_money,
        time=datetime.now(BERLIN_TZ).replace(
            hour=SMARTMONEY_SCAN_HOUR, minute=SMARTMONEY_SCAN_MIN, second=0, microsecond=0
        ).timetz(),
        name="smartmoney_scan"
    )

    # Aktive Trades laufend überwachen (Auto-Close bei SL/TP oder Signal-Verfall)
    job_queue.run_repeating(monitor_trades, interval=MONITOR_INTERVAL_SEC, first=30, name="monitor_trades")

    log.info("🤖 Bot gestartet. Warte auf Jobs...")
    log.info(f"  → Signale: {SIGNAL_TIME_HOUR:02d}:{SIGNAL_TIME_MIN:02d} Uhr")
    log.info(f"  → Auswertung: {CLOSE_TIME_HOUR:02d}:{CLOSE_TIME_MIN:02d} Uhr")
    log.info(f"  → Smart-Money-Scan: {SMARTMONEY_SCAN_HOUR:02d}:{SMARTMONEY_SCAN_MIN:02d} Uhr")
    log.info(f"  → Trade-Monitor: alle {MONITOR_INTERVAL_SEC}s")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
