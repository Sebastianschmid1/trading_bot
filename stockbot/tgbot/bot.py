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
from telegram.error import Conflict, BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from stockbot.core import db
from stockbot.market import universes
from stockbot.market import smartmoney
from stockbot.market import strategies
from stockbot.backtest import engine as backtest
from stockbot.core import metrics
from stockbot.ai import llm_ranker
from stockbot.broker import client as broker
from stockbot.tgbot.onboarding import onboarding_conv_handler
from stockbot.broker.setup import connect_alpaca_handler, disconnect as cmd_disconnect_alpaca
from stockbot.market.analyzer import analyze_universe, sl_tp_from_atr
from stockbot.core.evaluator import evaluate_trades, get_current_price, realized_pnl, liquidation_price
from stockbot.config import (
    TELEGRAM_TOKEN,
    SIGNAL_TIME_HOUR, SIGNAL_TIME_MIN,
    CLOSE_TIME_HOUR, CLOSE_TIME_MIN,
    BERLIN_TZ, DASHBOARD_BASE_URL, RUN_DASHBOARD_IN_BOT,
    UNIVERSES, REGION_LABELS, DEFAULT_REGION, DEFAULT_AUTO_UNIVERSE,
    SIGNAL_COUNT_CHOICES, TOP_N_SIGNALS, TRADE_SIZE_EUR, TRADE_SIZE_CHOICES,
    SMARTMONEY_SCAN_HOUR, SMARTMONEY_SCAN_MIN,
    SIGNAL_CLOSE_THRESHOLD, MONITOR_INTERVAL_SEC,
    SL_TP_MODES, DEFAULT_SL_TP_MODE, LEVERAGE_CHOICES, DEFAULT_LEVERAGE,
    LLM_RANK_ENABLED, DEFAULT_EOD_CLOSE, HOLD_MAX_DAYS,
    EXTENDED_HOURS, ALPACA_ENABLED, ALPACA_PAPER
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

# ── Kandidaten-Cache (für das Nachrücken ohne Duplikate) ────────────────────

_candidates_cache: dict[str, dict] = {}   # key -> {"date": date, "ranked": [signal, ...]}


def _auto_uni(user: dict) -> bool:
    """Ob der Nutzer das Voll-Universum (automatisch geladene Vollliste) nutzt."""
    return user.get("auto_universe", DEFAULT_AUTO_UNIVERSE)


def _user_strategies(user: dict) -> list[str]:
    """Aktive Signal-Strategien des Nutzers (Liste von Schlüsseln aus strategies.REGISTRY)."""
    keys = user.get("strategies") or [s.strip() for s in (user.get("strategy") or "").split(",") if s.strip()]
    return [k for k in keys if k in strategies.REGISTRY] or [strategies.DEFAULT_STRATEGY]


def _trade_strategy_key(trade: dict) -> str:
    """Strategie-Schlüssel eines Trades (aus dem gespeicherten Signal; Fallback 'standard')."""
    key = (trade.get("signal") or {}).get("strategy") or "standard"
    return key if key in strategies.REGISTRY else "standard"


def _user_regions(user: dict) -> list[str]:
    """Aktive Markt-Körbe des Nutzers (Liste von Schlüsseln aus UNIVERSES, mind. einer)."""
    keys = user.get("market_regions") or [user.get("market_region") or DEFAULT_REGION]
    return [k for k in keys if k in UNIVERSES] or [DEFAULT_REGION]


def _merge_ranked(lists: list) -> list[dict]:
    """Führt mehrere (region-spezifische) Ranglisten zu einer zusammen: dedupliziert pro Ticker
    und behält jeweils das stärkste Signal. So liefern mehrere Körbe gemeinsam die besten Treffer."""
    best: dict[str, dict] = {}
    for lst in lists:
        for s in (lst or []):
            t = s.get("ticker")
            if not t:
                continue
            if t not in best or (s.get("strength", 0) or 0) > (best[t].get("strength", 0) or 0):
                best[t] = s
    return list(best.values())


def _llm_enabled(user: dict) -> bool:
    """Ob das LLM-Ranking (Claude Haiku) für diesen Nutzer aktiv ist (Schalter + globaler Key)."""
    return LLM_RANK_ENABLED and user.get("llm_rank", True)


def _alpaca_ready(user: dict) -> bool:
    """Ob für diesen Nutzer eine Alpaca-Anbindung verfügbar ist: eigene Keys (über den Bot
    hinterlegt) oder ersatzweise globale .env-Keys."""
    return bool(user and user.get("broker_platform") == "alpaca") or ALPACA_ENABLED


def _alpaca_client(user: dict):
    """Baut den Alpaca-Client für diesen Nutzer: bevorzugt die eigenen, verschlüsselt
    gespeicherten Keys; sonst Fallback auf globale .env-Keys. None, wenn nichts verfügbar."""
    if user and user.get("broker_platform") == "alpaca":
        creds = db.get_decrypted_credentials(user["user_id"])
        if creds:
            return broker.make_client(creds[0], creds[1], paper=ALPACA_PAPER)
    return broker._get_client()   # globale .env-Keys (oder None)


def _universe_key(region: str, auto: bool, strategy: str = strategies.DEFAULT_STRATEGY) -> str:
    """Cache-/Analyse-Schlüssel: Region + Voll-Universum-Schalter + Strategie
    (alle drei können verschiedene Signal-Mengen ergeben)."""
    return f"{region}:{int(bool(auto))}:{strategy}"


def _cache_candidates(key: str, ranked: list[dict]):
    _candidates_cache[key] = {"date": date.today(), "ranked": ranked}


def _get_candidates(key: str) -> list[dict]:
    e = _candidates_cache.get(key)
    return e["ranked"] if e and e["date"] == date.today() else []


# ── Nachrichten senden ──────────────────────────────────────────────────────

def _signal_card(signal: dict, trade_size_eur: float, market_open: bool) -> tuple[str, InlineKeyboardMarkup]:
    """Baut Nachrichtentext + Tastatur eines Signals (inkl. SL/TP, Hebel, Liquidation, Hebel-Buttons)."""
    ticker = signal["ticker"]
    direction = signal["direction"]
    direction_emoji = "🟢 LONG" if direction == "long" else "🔴 SHORT"
    filled = int(round(signal["strength"] / 10))
    strength_bar = "█" * filled + "░" * (10 - filled)
    leverage = signal.get("leverage", 1.0) or 1.0

    if signal.get("stop_loss") and signal.get("take_profit"):
        risk_block = (
            f"🎯 Take-Profit: *${signal['take_profit']:.2f}* (+{signal['tp_pct']:.1f}%)\n"
            f"🛑 Stop-Loss: *${signal['stop_loss']:.2f}* ({signal['sl_pct']:.1f}%)\n"
            f"⚖️ Chance/Risiko: ~1:{signal['risk_reward']:.1f}\n"
        )
    elif signal.get("sl_tp_mode") == "aus":
        risk_block = "🎯 SL/TP: *aus* — kein Auto-Verkauf (nur Liquidation bei Hebel / Tagesende)\n"
    else:
        risk_block = ""

    # Hebel + Liquidation (Liquidationskurs ~ relativ zum aktuellen Kurs)
    lev_block = f"⚡ Hebel: *{leverage:g}×*"
    liq = liquidation_price(signal["price"], leverage, direction)
    if liq is not None:
        lev_block += f"  ·  💥 Liquidation ~${liq:.2f} (−{100.0/leverage:.0f}%)"
    lev_block += "\n"

    sm = signal.get("smart_money")
    sm_line = (f"  • 🐳 Smart-Money: {'★'*sm['stars']}{'☆'*(5-sm['stars'])} (Score {sm['score']})\n"
               if sm else "")

    # Freitext der KI gegen Markdown-Sonderzeichen absichern (sonst „can't parse entities")
    _llm_reason = "".join(c for c in str(signal.get("llm_reason", "")) if c not in "_*`[]")
    llm_line = (f"  • 🤖 KI-Rang: {signal['llm_score']:.0f}/100 — {_llm_reason}\n"
                if isinstance(signal.get("llm_score"), (int, float)) else "")

    if market_open:
        footer = (
            f"⏰ Start nur innerhalb von {TRADE_ACTIVATION_WINDOW_MIN} Minuten möglich\n"
            f"⏱ Auswertung: {CLOSE_TIME_HOUR:02d}:{CLOSE_TIME_MIN:02d} Uhr (oder früher bei SL/TP)"
        )
        # Hebel-Buttons (pro Signal änderbar) + JA/NEIN
        lev_row = [InlineKeyboardButton(("✅ " if float(v) == leverage else "") + f"{v:g}×",
                                        callback_data=f"lev:{ticker}:{v}") for v in LEVERAGE_CHOICES]
        keyboard = InlineKeyboardMarkup([
            lev_row,
            [InlineKeyboardButton("✅ JA — Demo-Trade starten", callback_data=f"accept:{ticker}"),
             InlineKeyboardButton("❌ NEIN", callback_data=f"reject:{ticker}")],
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
        f"{llm_line}"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{risk_block}"
        f"{lev_block}"
        f"💶 Demo-Trade: *{trade_size_eur:.0f}€ {direction.upper()}*\n"
        f"{footer}"
    )
    return text, keyboard


def _personalize_signal(signal: dict, sl_tp_mode: str, leverage: float) -> dict:
    """Erstellt eine Nutzer-Kopie des Signals mit gewünschtem Hebel.

    SL/TP: Bei der Standard-Strategie wird der gewählte SL/TP-Modus angewandt. Andere
    Strategien (z. B. ADX-Trendfolge) definieren ihre SL/TP selbst → diese bleiben erhalten;
    der SL/TP-Modus gehört konzeptionell zur Standard-Strategie."""
    sig = dict(signal)
    if sig.get("strategy", "standard") == "standard":
        sig.update(sl_tp_from_atr(sig["price"], sig.get("atr"), sl_tp_mode))
        sig["sl_tp_mode"] = sl_tp_mode
    sig["leverage"] = float(leverage)
    return sig


async def _await_fill(order_id: str, client, tries: int = 6, delay: float = 2.0) -> dict:
    """Pollt den Order-Status bis 'filled'/Endzustand oder Timeout. Gibt das letzte Status-Dict.
    Market-Orders füllen i. d. R. sofort; Limit-/Ext-Orders evtl. erst später (DAY)."""
    final = {"filled", "canceled", "rejected", "expired", "done_for_day"}
    st = {}
    for _ in range(tries):
        st = await asyncio.to_thread(broker.get_order_status, order_id, client)
        if st.get("status") in final or not st.get("ok"):
            return st
        await asyncio.sleep(delay)
    return st


async def _maybe_broker_order(bot: Bot, chat_id: int, trade: dict):
    """Sendet (falls für den Nutzer aktiviert) eine echte ALPACA-(Paper-)Order zum gerade
    aktivierten Trade und bestätigt erst nach der tatsächlichen Ausführung (Fill).
    Größe = Trade-Größe × Hebel (Demo: € ≈ USD). Best-effort: scheitert nie hart."""
    if not trade:
        return
    user = db.get_user(chat_id)
    if not user or not user.get("broker_exec") or not _alpaca_ready(user):
        return
    client = _alpaca_client(user)
    if client is None:
        return
    sig = trade.get("signal", {})
    entry = trade.get("entry") or sig.get("price")
    if not entry:
        return

    ticker = trade["ticker"]
    leverage = float(sig.get("leverage", 1.0) or 1.0)
    budget = float(user["trade_size_eur"]) * leverage     # Ziel-Positionsgröße in USD (Hebel berücksichtigt)
    extended = EXTENDED_HOURS and not _us_market_open(extended=False)
    mode = "PAPER" if ALPACA_PAPER else "LIVE"

    if extended:
        # Extended Hours: Alpaca erlaubt keine Bruchteile → ganze Aktien als Limit-DAY
        qty = int(budget / float(entry))
        if qty < 1:
            await bot.send_message(
                chat_id=chat_id,
                text=(f"ℹ️ Alpaca-{mode}: Budget {user['trade_size_eur']:.0f}€×{leverage:g} reicht in den "
                      f"erweiterten Handelszeiten nicht für 1 ganze {ticker}-Aktie (${float(entry):.2f}) — "
                      f"hier sind keine Bruchteile möglich. Keine Order gesendet."))
            return
        res = await asyncio.to_thread(
            broker.submit_buy, ticker, qty=qty, limit_price=float(entry),
            extended_hours=True, client=client)
    else:
        # Reguläre Zeit: Notional-Order = exakt fürs Budget (Bruchteile möglich)
        res = await asyncio.to_thread(broker.submit_buy, ticker, notional=round(budget, 2), client=client)

    if not res["ok"]:
        await bot.send_message(chat_id=chat_id, text=f"⚠️ Alpaca-Order nicht angenommen: {res['detail']}")
        return

    await bot.send_message(
        chat_id=chat_id,
        text=f"📨 Alpaca-{mode}-Order angenommen: {res['detail']}. Warte auf Ausführung…")

    fill = await _await_fill(res.get("id", ""), client)
    status = fill.get("status", "unbekannt")
    if status == "filled":
        q = fill.get("filled_qty", 0.0)
        px = fill.get("filled_avg_price", 0.0)
        await bot.send_message(
            chat_id=chat_id,
            text=(f"✅ Alpaca-{mode}-Order ausgeführt: {q:g} {ticker} @ ${px:.2f} (≈ ${q * px:.2f})\n"
                  f"SL/TP überwacht der Bot und schließt die Position automatisch."))
    elif status in ("rejected", "canceled", "expired"):
        await bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ Alpaca-Order nicht ausgeführt (Status: {status}). Es wurde nichts gekauft.")
    else:
        # angenommen, aber (noch) nicht gefüllt — z. B. Markt zu / Limit nicht erreicht
        await bot.send_message(
            chat_id=chat_id,
            text=(f"⏳ Alpaca-Order angenommen, aber noch nicht ausgeführt (Status: {status}).\n"
                  f"Es ist eine DAY-Order — sie füllt sich automatisch, sobald der Markt/Kurs passt, "
                  f"sonst verfällt sie zum Handelsschluss. Du musst nichts tun."))


async def _maybe_broker_close(bot: Bot, user: dict, ticker: str):
    """Schließt (falls Broker-Ausführung an) die echte Alpaca-Position zum Ticker. Best-effort.
    Meldet nur, wenn tatsächlich eine Position geschlossen wurde (oder ein echter Fehler auftrat)."""
    if not user or not user.get("broker_exec") or not _alpaca_ready(user):
        return
    client = _alpaca_client(user)
    if client is None:
        return
    res = await asyncio.to_thread(broker.close_position, ticker, client=client)
    mode = "PAPER" if ALPACA_PAPER else "LIVE"
    if res.get("closed"):
        await bot.send_message(chat_id=user["user_id"],
                               text=f"📉 Alpaca-{mode}: Position {ticker} geschlossen.")
    elif not res.get("ok"):
        await bot.send_message(chat_id=user["user_id"],
                               text=f"⚠️ Alpaca: {ticker} konnte nicht geschlossen werden: {res['detail']}")


async def send_signal(bot: Bot, chat_id: int, signal: dict, trade_size_eur: float,
                      job_queue=None, market_open: bool = True,
                      sl_tp_mode: str = DEFAULT_SL_TP_MODE, leverage: float = DEFAULT_LEVERAGE,
                      auto_accept: bool = False) -> bool:
    """Sendet eine Aktienempfehlung an einen Nutzer (mit dessen SL/TP-Modus + Hebel).

    - Offene Börse: legt einen handelbaren Demo-Trade an, Hebel pro Signal änderbar, JA/NEIN
      (oder automatische Annahme bei auto_accept). Plant das Ablaufen/Löschen.
    - Geschlossene Börse: nur Info mit deaktiviertem Button.
    Duplikat-Schutz: pro Aktie/Tag nur ein Trade. Rückgabe: True wenn gesendet, sonst False.
    """
    ticker = signal["ticker"]
    if market_open and db.has_open_position(chat_id, ticker):
        log.info(f"[{chat_id}] Signal übersprungen (heute schon vorhanden): {ticker}")
        return False

    sig = _personalize_signal(signal, sl_tp_mode, leverage) if market_open else dict(signal)

    # Auto-Accept: sofort starten, keine Buttons
    if market_open and auto_accept:
        msg = await bot.send_message(chat_id=chat_id,
                                     text=_signal_card(sig, trade_size_eur, market_open)[0],
                                     parse_mode="Markdown")
        db.add_pending(chat_id, sig, msg.message_id)
        trade = db.activate_trade(chat_id, ticker)
        if trade:
            await bot.send_message(
                chat_id=chat_id,
                text=f"⚡ *{ticker}* automatisch gestartet (Auto-Accept) — Einstieg ${trade['entry']:.2f}, Hebel {leverage:g}×",
                parse_mode="Markdown",
            )
            await _maybe_broker_order(bot, chat_id, trade)
        log.info(f"[{chat_id}] Auto-Accept Signal gestartet: {ticker} ({leverage:g}×)")
        return True

    text, keyboard = _signal_card(sig, trade_size_eur, market_open)
    msg = await bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=keyboard)

    if market_open:
        db.add_pending(chat_id, sig, msg.message_id)
        log.info(f"[{chat_id}] Signal gesendet: {ticker} ({sig['direction']}, {leverage:g}×)")
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

    # Analyse pro benötigter (Bereich, Voll-Universum, Strategie)-Kombination einmal berechnen
    ranked_by_key: dict[str, list] = {}
    size_by_key: dict[str, int] = {}
    needed = {(r, _auto_uni(u), s)
              for u in users for r in _user_regions(u) for s in _user_strategies(u)}
    for region, auto, strat_key in needed:
        key = _universe_key(region, auto, strat_key)
        tickers = universes.get_tickers(region, auto=auto)
        size_by_key[key] = len(tickers)
        try:
            ranked = analyze_universe(tickers, generate=strategies.get(strat_key).generate)
            ranked_by_key[key] = ranked
            _cache_candidates(key, ranked)   # Kandidaten fürs Nachrücken merken
        except Exception as e:
            log.error(f"Analyse fehlgeschlagen ({key}): {e}")
            ranked_by_key[key] = None  # Fehler-Marker

    market_open = _us_market_open()
    for u in users:
        chat_id = u["user_id"]
        regions = _user_regions(u)
        user_strats = _user_strategies(u)
        top_n = u.get("top_n_signals") or TOP_N_SIGNALS
        region_label = " + ".join(REGION_LABELS.get(r, r) for r in regions)

        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"🌅 *Guten Morgen! Tagesanalyse {now.strftime('%d.%m.%Y')}*\n"
                f"Körbe: {region_label} · "
                f"{len(user_strats)} Strategie(n) ⏳"
            ),
            parse_mode="Markdown"
        )

        # Pro gewählter Strategie eigene top_n Signale — über alle gewählten Körbe gebündelt
        any_sent = False
        for strat_key in user_strats:
            strat = strategies.get(strat_key)
            ranked_lists = [ranked_by_key.get(_universe_key(r, _auto_uni(u), strat_key)) for r in regions]
            if all(rl is None for rl in ranked_lists):
                await bot.send_message(chat_id=chat_id,
                                       text=f"⚠️ Analyse-Fehler ({strat.label}).")
                continue
            ranked = _merge_ranked([rl for rl in ranked_lists if rl is not None])
            signals = smartmoney.rank(ranked, top_n)
            if not signals:
                continue
            if _llm_enabled(u):
                signals = await asyncio.to_thread(llm_ranker.rank_signals, signals)
            await bot.send_message(chat_id=chat_id,
                                   text=f"🧠 *Strategie: {strat.label}*", parse_mode="Markdown")
            for signal in signals:
                if await send_signal(bot, chat_id, signal, u["trade_size_eur"],
                                     job_queue=context.job_queue, market_open=market_open,
                                     sl_tp_mode=u.get("sl_tp_mode", DEFAULT_SL_TP_MODE),
                                     leverage=u.get("leverage", DEFAULT_LEVERAGE),
                                     auto_accept=u.get("auto_accept", False)):
                    any_sent = True
                await asyncio.sleep(1.5)  # kurze Pause zwischen Nachrichten
        if not any_sent:
            await bot.send_message(chat_id=chat_id, text="⚠️ Heute keine klaren Signale gefunden.")
        await asyncio.sleep(0.5)  # kurze Pause zwischen Nutzern (Rate-Limit-Schutz)


def _trade_age_days(trade: dict) -> int:
    """Alter eines Trades in Kalendertagen seit Eröffnung (trade_date)."""
    try:
        return (date.today() - date.fromisoformat(trade["trade_date"])).days
    except Exception:
        return 0


async def close_and_evaluate(context: ContextTypes.DEFAULT_TYPE):
    """Job: Nach US-Börsenschluss Trades je Nutzer auswerten.
    eod_close an → alle schließen. Aus → nur Trades über der Höchsthaltedauer; Rest hält über Nacht."""
    bot = context.bot
    log.info("Starte Tagesauswertung...")

    for u in db.list_active_users():
        chat_id = u["user_id"]
        active = db.get_active_trades(chat_id)
        eod = u.get("eod_close", DEFAULT_EOD_CLOSE)

        if eod:
            to_close, kept = active, []
        else:
            to_close = [t for t in active if _trade_age_days(t) >= HOLD_MAX_DAYS]
            kept = [t for t in active if t not in to_close]

        if not to_close:
            if kept:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"🌙 {len(kept)} Demo-Trade(s) werden über Nacht gehalten (Tagesende-Schließung aus).")
            else:
                await bot.send_message(chat_id=chat_id, text="📭 Heute keine aktiven Demo-Trades zum Auswerten.")
            await asyncio.sleep(0.5)
            continue

        head = (f"⏰ *{CLOSE_TIME_HOUR:02d}:{CLOSE_TIME_MIN:02d} Uhr — Schließe alle Demo-Trades und werte aus...*"
                if eod else
                f"⏰ Schließe {len(to_close)} Trade(s) über der Höchsthaltedauer ({HOLD_MAX_DAYS} Tage); "
                f"{len(kept)} bleiben über Nacht offen.")
        await bot.send_message(chat_id=chat_id, text=head, parse_mode="Markdown")

        results = evaluate_trades(to_close, u["trade_size_eur"])
        db.close_all(chat_id, results)
        # echte Alpaca-Positionen (falls vorhanden) mitschließen
        for r in results:
            await _maybe_broker_close(bot, u, r["ticker"])

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

def _fmt_strength(v) -> str:
    """Signalstärke (0–100) für Meldungen formatieren; '—' wenn unbekannt."""
    return f"{v:.0f}" if v is not None else "—"


# Einmalige „SL/TP aus"-Warnung pro Trade & Tag: (uid, ticker, datum)
_weak_warned: set = set()


async def _maybe_warn_sltp_off(bot: Bot, uid: int, trade: dict, price: float, strength):
    """Bei SL/TP-Modus 'aus': der Trade wird NICHT automatisch geschlossen. Damit man nicht
    blind fährt, gibt es eine *einmalige* Heads-up-Meldung (mit Verkaufen-Button), sobald die
    Signalstärke unter die Schwelle fällt."""
    sig = trade.get("signal", {})
    if sig.get("sl_tp_mode") != "aus":
        return
    if strength is None or strength >= SIGNAL_CLOSE_THRESHOLD:
        return
    key = (uid, trade["ticker"], date.today().isoformat())
    if key in _weak_warned:
        return
    _weak_warned.add(key)
    entry = trade.get("entry") or price
    await bot.send_message(
        chat_id=uid,
        text=(f"⚠️ *{trade['ticker']}* — Signal verschlechtert "
              f"(Einstieg-Signal: {_fmt_strength(sig.get('strength'))} → jetzt: {_fmt_strength(strength)}).\n"
              f"Dein SL/TP-Modus ist *aus* → der Trade wird *nicht* automatisch geschlossen.\n"
              f"Einstieg ${entry:.2f} → aktuell ${price:.2f}. Bei Bedarf manuell schließen:"),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("💰 Verkaufen", callback_data=f"sell:{trade['ticker']}")]]),
    )


def evaluate_active_trade(trade: dict, price: float | None, strength: float | None) -> str | None:
    """Entscheidet, ob ein aktiver Trade geschlossen werden soll.
    Gibt den Grund zurück (oder None, wenn er offen bleibt)."""
    sig = trade.get("signal", {})
    sl, tp = sig.get("stop_loss"), sig.get("take_profit")
    leverage = sig.get("leverage", 1.0) or 1.0
    entry = trade.get("entry")
    # SL/TP-Modus "aus": kein automatisches Schließen (weder SL/TP noch Signal-Verfall).
    # Nur eine echte Liquidation (Hebel > 1) ist unvermeidbar; sonst hält der Trade
    # bis zum manuellen Verkauf bzw. der Tagesend-Auswertung.
    aus = sig.get("sl_tp_mode") == "aus"
    if price is not None and trade.get("direction", "long") == "long":
        liq = liquidation_price(entry, leverage, "long") if entry else None
        if liq is not None and price <= liq:        # zuerst: Liquidation (Totalverlust)
            return "Liquidation 💥"
        if sl is not None and price <= sl:
            return "Stop-Loss 🛑"
        if tp is not None and price >= tp:
            return "Take-Profit 🎯"
    if not aus and strength is not None and strength < SIGNAL_CLOSE_THRESHOLD:
        return "Signal verschlechtert 📉"
    return None


async def monitor_trades(context: ContextTypes.DEFAULT_TYPE):
    """Job (alle 60s): aktive Trades prüfen, Verlauf aufzeichnen, bei SL/TP oder
    Signal-Verfall automatisch schließen. Läuft nur bei offenem Markt & aktiven Trades."""
    if not _us_market_open():
        return

    # aktive Trades aller Nutzer sammeln + eindeutige (Ticker, Strategie)-Paare
    active_by_user: dict[int, tuple[dict, list]] = {}
    pairs: set[tuple[str, str]] = set()
    for u in db.list_active_users():
        act = db.get_active_trades(u["user_id"])
        if act:
            active_by_user[u["user_id"]] = (u, act)
            pairs.update((t["ticker"], _trade_strategy_key(t)) for t in act)
    if not pairs:
        return

    # Live-Kurs + Score je (Ticker, Strategie): jeder Trade wird mit dem Score SEINER
    # Strategie bewertet (blockierende yfinance-Aufrufe → Thread)
    data = await asyncio.to_thread(strategies.live_scores, pairs)

    for uid, (user, act) in active_by_user.items():
        for trade in act:
            info = data.get((trade["ticker"], _trade_strategy_key(trade)))
            if not info:
                continue
            price, strength = info["price"], info["strength"]
            db.add_tick(uid, trade["ticker"], price, strength)   # Verlauf für die Charts

            reason = evaluate_active_trade(trade, price, strength)
            if price is None:
                continue
            if not reason:
                # SL/TP-Modus „aus": kein Auto-Close — aber einmalig warnen, wenn das Signal kippt
                await _maybe_warn_sltp_off(context.bot, uid, trade, price, strength)
                continue

            entry = trade.get("entry") or price
            leverage = trade.get("signal", {}).get("leverage", 1.0) or 1.0
            # Bei Liquidation zum Liquidationskurs schließen (Totalverlust), sonst zum aktuellen Kurs
            exit_price = price
            if reason.startswith("Liquidation"):
                liq = liquidation_price(entry, leverage, trade["direction"])
                if liq is not None:
                    exit_price = liq
            pnl_pct, pnl_eur = realized_pnl(entry, exit_price, trade["direction"],
                                            user["trade_size_eur"], leverage)
            db.close_all(uid, [{"ticker": trade["ticker"], "exit": exit_price,
                                "pnl_eur": pnl_eur, "pnl_pct": pnl_pct}])

            sign = "+" if pnl_eur >= 0 else ""
            emoji = "🟢" if pnl_eur > 0 else ("🔴" if pnl_eur < 0 else "⚪")
            await context.bot.send_message(
                chat_id=uid,
                text=(f"{emoji} *{trade['ticker']} automatisch geschlossen* — {reason}\n"
                      f"Einstieg ${entry:.2f} → Ausstieg ${exit_price:.2f} (Hebel {leverage:g}×)\n"
                      f"Einstieg-Signal: {_fmt_strength(trade.get('signal', {}).get('strength'))} → "
                      f"Ausstieg-Signal: {_fmt_strength(strength)}  ·  "
                      f"Realisiert: {sign}{pnl_pct:.1f}% ({sign}{pnl_eur:.2f}€)"),
                parse_mode="Markdown",
            )
            # echte Alpaca-Position (falls vorhanden) mitschließen
            await _maybe_broker_close(context.bot, user, trade["ticker"])
            # nach Auto-Close auch nachrücken (sofern manueller Modus)
            await refill_pending(context.bot, uid, user, context.job_queue)
            log.info(f"[{uid}] Auto-Close {trade['ticker']} ({reason}) {sign}{pnl_eur:.2f}€")


# ── Smart-Money: nächtlicher Scan (was große Trader handeln) ─────────────────

def _universes_in_use() -> set[tuple[str, bool]]:
    """(Bereich, Voll-Universum)-Kombinationen aller aktiven Nutzer (für den Scan-Umfang)."""
    combos = {(r, _auto_uni(u)) for u in db.list_active_users() for r in _user_regions(u)}
    return combos or {(DEFAULT_REGION, DEFAULT_AUTO_UNIVERSE)}


async def scan_smart_money(context: ContextTypes.DEFAULT_TYPE):
    """Job: Scannt nachts die genutzten Universen auf Insider-/Institutionen-Aktivität
    und cacht die Smart-Money-Scores (langsam → läuft im Hintergrund, blockiert nichts)."""
    tickers: set[str] = set()
    for region, auto in _universes_in_use():
        tickers.update(universes.get_tickers(region, auto=auto))
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


def _us_market_open(extended: bool | None = None) -> bool:
    """Grobe Prüfung, ob die US-Börse gerade „offen" ist (Wochentag, ET-Zeitfenster).
    Regulär 9:30–16:00 ET; mit Extended Hours (Pre-/After-Market) 4:00–20:00 ET.
    Feiertage werden nicht berücksichtigt."""
    if extended is None:
        extended = EXTENDED_HOURS
    now = datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    if extended:
        return 4 * 60 <= minutes <= 20 * 60
    return 9 * 60 + 30 <= minutes <= 16 * 60


async def refill_pending(bot: Bot, chat_id: int, user: dict, job_queue):
    """Füllt nach einer Entscheidung die offenen (pending) Signale wieder auf — *pro Strategie*
    auf top_n, mit neuen Tickern aus der jeweiligen Kandidatenliste, ohne Duplikate (Punkt 6).
    Bei Auto-Accept oder geschlossener Börse passiert nichts."""
    if user.get("auto_accept") or not _us_market_open():
        return
    regions = _user_regions(user)
    top_n = user.get("top_n_signals") or TOP_N_SIGNALS

    # offene (pending) Trades je Strategie zählen
    pending_by_strat: dict[str, int] = {}
    for t in db.get_pending_trades(chat_id):
        s = t.get("signal", {}).get("strategy", "standard")
        pending_by_strat[s] = pending_by_strat.get(s, 0) + 1

    for strat_key in _user_strategies(user):
        need = top_n - pending_by_strat.get(strat_key, 0)
        if need <= 0:
            continue
        candidates = _merge_ranked([_get_candidates(_universe_key(r, _auto_uni(user), strat_key))
                                    for r in regions])
        for signal in candidates:
            if need <= 0:
                break
            if db.has_open_position(chat_id, signal["ticker"]):
                continue
            sent = await send_signal(bot, chat_id, signal, user["trade_size_eur"],
                                     job_queue=job_queue, market_open=True,
                                     sl_tp_mode=user.get("sl_tp_mode", DEFAULT_SL_TP_MODE),
                                     leverage=user.get("leverage", DEFAULT_LEVERAGE),
                                     auto_accept=False)
            if sent:
                need -= 1


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
    regions = _user_regions(user)
    auto = _auto_uni(user)
    user_strats = _user_strategies(user)
    top_n = user.get("top_n_signals") or TOP_N_SIGNALS
    tickers_by_region = {r: universes.get_tickers(r, auto=auto) for r in regions}
    n_tickers = len({t for ts in tickers_by_region.values() for t in ts})
    region_label = " + ".join(REGION_LABELS.get(r, r) for r in regions)
    market_open = _us_market_open()
    strat_labels = ", ".join(strategies.get(k).label for k in user_strats)

    await update.message.reply_text(
        f"🔍 Analysiere *{region_label}* ({n_tickers} Aktien) · Strategien: *{strat_labels}*… ⏳",
        parse_mode="Markdown")

    if not market_open:
        await update.message.reply_text(
            "🔒 US-Börse geschlossen — es sind echte Kurse vom letzten Handelstag, *keine Testdaten*. "
            "Bis zur nächsten Session ändern sich die Signale nicht.",
            parse_mode="Markdown")

    # Pro gewählter Strategie eigene top_n Signale (Dedup über has_trade_today)
    total_sent = 0
    for strat_key in user_strats:
        strat = strategies.get(strat_key)
        try:
            per_region = []
            for r in regions:
                rl = analyze_universe(tickers_by_region[r], generate=strat.generate)
                _cache_candidates(_universe_key(r, auto, strat.key), rl)   # Kandidaten fürs Nachrücken (je Korb)
                per_region.append(rl)
            ranked = _merge_ranked(per_region)
            signals = smartmoney.rank(ranked, top_n)
            if _llm_enabled(user):
                signals = await asyncio.to_thread(llm_ranker.rank_signals, signals)
        except Exception as e:
            await update.message.reply_text(f"⚠️ Analyse-Fehler ({strat.label}): {e}")
            continue
        if not signals:
            await update.message.reply_text(f"🧠 *{strat.label}*: heute keine klaren Signale.",
                                            parse_mode="Markdown")
            continue
        as_of = signals[0].get("as_of", "?")
        await update.message.reply_text(
            f"🧠 *Strategie: {strat.label}* — {len(signals)} Signale (Datenstand: {as_of})",
            parse_mode="Markdown")
        for signal in signals:
            if await send_signal(bot, chat_id, signal, user["trade_size_eur"],
                                 job_queue=context.job_queue, market_open=market_open,
                                 sl_tp_mode=user.get("sl_tp_mode", DEFAULT_SL_TP_MODE),
                                 leverage=user.get("leverage", DEFAULT_LEVERAGE),
                                 auto_accept=user.get("auto_accept", False)):
                total_sent += 1
            await asyncio.sleep(1)

    if market_open and total_sent == 0:
        await update.message.reply_text(
            "ℹ️ Alle heutigen Signale hattest du bereits — pro Aktie gibt es nur ein Signal pro Tag."
        )


def _unrealized_pnl(trade: dict, trade_size_eur: float):
    """Aktuellen (unrealisierten) Stand eines aktiven Trades berechnen — echte Kurse, mit Hebel."""
    entry = trade["entry"]
    leverage = trade.get("signal", {}).get("leverage", 1.0) or 1.0
    current = get_current_price(trade["ticker"], entry)
    pnl_pct, pnl_eur = realized_pnl(entry, current, trade["direction"], trade_size_eur, leverage)
    return current, pnl_pct, pnl_eur


def _trade_card(trade: dict, trade_size_eur: float, current_strength=None):
    """Baut Nachrichtentext + Verkaufen-Button für einen aktiven Demo-Trade.
    `current_strength` = aktuelle Signalstärke (z. B. letzter 60s-Tick); None → unbekannt."""
    ticker = trade["ticker"]
    leverage = trade.get("signal", {}).get("leverage", 1.0) or 1.0
    entry_strength = trade.get("signal", {}).get("strength")
    current, pnl_pct, pnl_eur = _unrealized_pnl(trade, trade_size_eur)
    emoji = "🟢" if pnl_eur > 0 else ("🔴" if pnl_eur < 0 else "⚪")
    sign = "+" if pnl_eur >= 0 else ""
    text = (
        f"📊 *{ticker}* — aktiver Demo-Trade ({trade['direction'].upper()}, {leverage:g}×)\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 Einstieg: ${trade['entry']:.2f}\n"
        f"📈 Aktuell: ${current:.2f}\n"
        f"📶 Signal: Einstieg {_fmt_strength(entry_strength)} → jetzt {_fmt_strength(current_strength)}\n"
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
    ticks = db.get_today_ticks(chat_id)   # letzte 60s-Stärke je Ticker (für „jetziges Signal")
    for trade in active:
        pts = ticks.get(trade["ticker"], [])
        cur_strength = pts[-1].get("strength") if pts else None
        text, keyboard = _trade_card(trade, user["trade_size_eur"], current_strength=cur_strength)
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
    if _alpaca_ready(user):
        _mode = "PAPER" if ALPACA_PAPER else "LIVE"
        _conn = "eigene Keys" if user.get("broker_platform") == "alpaca" else "globale Keys"
        broker_line += (f"\n📈 Alpaca ({_mode}, {_conn}) — Ausführung: "
                        f"*{'an' if user.get('broker_exec') else 'aus'}*")
    regions = _user_regions(user)
    await update.message.reply_text(
        "👤 *Dein Profil*\n"
        f"💶 Demo-Trade-Größe: *{user['trade_size_eur']:.0f}€*\n"
        f"🌍 Markt-Körbe: *{' + '.join(REGION_LABELS.get(r, r) for r in regions)}*\n"
        f"🔢 Signale pro Tag: *{user.get('top_n_signals') or TOP_N_SIGNALS}*\n"
        f"🎯 SL/TP-Modus: *{user.get('sl_tp_mode') or DEFAULT_SL_TP_MODE}*\n"
        f"⚡ Hebel: *{(user.get('leverage') or DEFAULT_LEVERAGE):g}×*\n"
        f"🤖 Auto-Accept: *{'an' if user.get('auto_accept') else 'aus'}*\n"
        f"🌐 Voll-Universum: *{'an' if _auto_uni(user) else 'aus'}*\n"
        f"🧠 Strategien: *{', '.join(strategies.get(k).label for k in _user_strategies(user))}*\n"
        f"🤖 KI-Ranking (Haiku): *{'an' if _llm_enabled(user) else 'aus'}*\n"
        f"🌙 Tagesende-Schließung: *{'an' if user.get('eod_close', DEFAULT_EOD_CLOSE) else 'aus (über Nacht halten)'}*\n"
        f"{broker_line}\n"
        f"📡 Status: {'aktiv ✅' if user['is_active'] else 'pausiert ⏸'}\n\n"
        "⚙️ Alles ändern: /settings",
        parse_mode="Markdown"
    )


def _settings_view(user: dict):
    """Baut Text + Inline-Tastatur für /settings (Markt, Anzahl, SL/TP-Modus, Hebel, Auto-Accept, Voll-Universum)."""
    region_keys = _user_regions(user)
    size = user.get("trade_size_eur") or TRADE_SIZE_EUR
    top_n = user.get("top_n_signals") or TOP_N_SIGNALS
    mode = user.get("sl_tp_mode") or DEFAULT_SL_TP_MODE
    lev = user.get("leverage") or DEFAULT_LEVERAGE
    auto = user.get("auto_accept")
    auto_uni = _auto_uni(user)
    strat_keys = _user_strategies(user)
    llm_on = _llm_enabled(user)
    llm_state = ("an" if llm_on else "aus") if LLM_RANK_ENABLED else "aus (kein API-Key)"
    eod = user.get("eod_close", DEFAULT_EOD_CLOSE)
    broker_on = user.get("broker_exec")
    broker_ready = _alpaca_ready(user)
    broker_mode = "PAPER" if ALPACA_PAPER else "LIVE"

    text = (
        "⚙️ *Einstellungen*\n"
        f"💶 Demo-Trade-Größe: *{size:.0f}€*\n"
        f"🌍 Markt-Körbe: *{' + '.join(REGION_LABELS.get(k, k) for k in region_keys)}*\n"
        f"🔢 Signale pro Tag: *{top_n}* (pro Strategie)\n"
        f"🎯 SL/TP-Modus: *{mode}*\n"
        f"⚡ Hebel: *{lev:g}×*\n"
        f"🤖 Auto-Accept: *{'an' if auto else 'aus'}*\n"
        f"🌐 Voll-Universum: *{'an' if auto_uni else 'aus'}*\n"
        f"🧠 Strategien: *{', '.join(strategies.get(k).label for k in strat_keys)}*\n"
        f"🤖 KI-Ranking (Haiku): *{llm_state}*\n"
        f"🌙 Tagesende-Schließung: *{'an' if eod else f'aus (halten bis SL/TP, max {HOLD_MAX_DAYS}T)'}*\n"
        + (f"📈 Broker-Ausführung ({broker_mode}): *{'an' if broker_on else 'aus'}*\n" if broker_ready else "")
        + "\nTippe unten, um zu ändern (Körbe & Strategien: mehrere möglich):"
    )

    size_row = [InlineKeyboardButton(("✅ " if float(v) == float(size) else "") + f"{v:g}€",
                                     callback_data=f"set_size:{v}")
                for v in TRADE_SIZE_CHOICES]
    region_row = [InlineKeyboardButton(("✅ " if k in region_keys else "") + lbl, callback_data=f"set_region:{k}")
                  for k, lbl in REGION_LABELS.items()]
    count_row = [InlineKeyboardButton(("✅ " if n == top_n else "") + str(n), callback_data=f"set_count:{n}")
                 for n in SIGNAL_COUNT_CHOICES]
    mode_row = [InlineKeyboardButton(("✅ " if k == mode else "") + k, callback_data=f"set_mode:{k}")
                for k in SL_TP_MODES]
    lev_row = [InlineKeyboardButton(("✅ " if float(v) == lev else "") + f"{v:g}×", callback_data=f"set_lev:{v}")
               for v in LEVERAGE_CHOICES]
    auto_row = [InlineKeyboardButton(("✅ " if auto else "") + "Auto-Accept an", callback_data="set_auto:1"),
                InlineKeyboardButton(("✅ " if not auto else "") + "aus", callback_data="set_auto:0")]
    uni_row = [InlineKeyboardButton(("✅ " if auto_uni else "") + "Voll-Universum an", callback_data="set_uni:1"),
               InlineKeyboardButton(("✅ " if not auto_uni else "") + "aus", callback_data="set_uni:0")]
    strat_row = [InlineKeyboardButton(("✅ " if s.key in strat_keys else "") + s.label,
                                      callback_data=f"set_strat:{s.key}")
                 for s in strategies.all_strategies()]
    llm_row = [InlineKeyboardButton(("✅ " if llm_on else "") + "KI-Ranking an", callback_data="set_llm:1"),
               InlineKeyboardButton(("✅ " if not llm_on else "") + "aus", callback_data="set_llm:0")]
    eod_row = [InlineKeyboardButton(("✅ " if eod else "") + "Tagesende-Schließung an", callback_data="set_eod:1"),
               InlineKeyboardButton(("✅ " if not eod else "") + "aus (halten)", callback_data="set_eod:0")]
    rows = [size_row, region_row, count_row, mode_row, lev_row, auto_row, uni_row, strat_row, llm_row, eod_row]
    if broker_ready:
        rows.append([
            InlineKeyboardButton(("✅ " if broker_on else "") + f"Broker-Order ({broker_mode}) an",
                                 callback_data="set_broker:1"),
            InlineKeyboardButton(("✅ " if not broker_on else "") + "aus", callback_data="set_broker:0")])
    keyboard = InlineKeyboardMarkup(rows)
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


async def cmd_tradesize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/tradesize <betrag> — Demo-Trade-Größe in € setzen (beliebiger Betrag)."""
    chat_id = update.effective_chat.id
    user = _registered_user(chat_id)
    if not user:
        await update.message.reply_text("⚠️ Du bist noch nicht eingerichtet. Sende zuerst /start.")
        return
    if not context.args:
        await update.message.reply_text(
            f"💶 Aktuelle Demo-Trade-Größe: *{user['trade_size_eur']:.0f}€*\n"
            "Ändern mit z. B. `/tradesize 250` — oder Schnellauswahl in /settings.",
            parse_mode="Markdown")
        return
    raw = context.args[0].strip().replace(",", ".").replace("€", "")
    try:
        val = float(raw)
        if val <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Bitte eine positive Zahl angeben, z. B. `/tradesize 250`.",
                                        parse_mode="Markdown")
        return
    saved = db.set_trade_size(chat_id, val)
    await update.message.reply_text(f"✅ Demo-Trade-Größe gesetzt: *{saved:.0f}€*", parse_mode="Markdown")


async def cmd_strategies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/strategies — listet die verfügbaren Signal-Strategien (aktive markiert)."""
    chat_id = update.effective_chat.id
    user = _registered_user(chat_id)
    if not user:
        await update.message.reply_text("⚠️ Du bist noch nicht eingerichtet. Sende zuerst /start.")
        return
    cur = _user_strategies(user)
    lines = ["🧠 *Verfügbare Strategien* (mehrere gleichzeitig möglich)", ""]
    for s in strategies.all_strategies():
        mark = "✅" if s.key in cur else "▫️"
        lines.append(f"{mark} *{s.label}*  (`{s.key}`)\n   {s.description}")
    lines.append("\nHinzufügen/Entfernen: `/addstrat <name>` oder über /settings.\nKennzahlen: /teststrat")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_addstrat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/addstrat <name> — fügt eine Strategie zur Auswahl hinzu (bzw. entfernt sie, wenn schon aktiv)."""
    chat_id = update.effective_chat.id
    user = _registered_user(chat_id)
    if not user:
        await update.message.reply_text("⚠️ Du bist noch nicht eingerichtet. Sende zuerst /start.")
        return
    keys = ", ".join(f"`{s.key}`" for s in strategies.all_strategies())
    if not context.args:
        await update.message.reply_text(
            f"Nutzung: `/addstrat <name>`\nVerfügbar: {keys}\nÜbersicht: /strategies",
            parse_mode="Markdown")
        return
    key = context.args[0].strip()
    if key not in strategies.REGISTRY:
        await update.message.reply_text(
            f"⚠️ Unbekannte Strategie `{key}`.\nVerfügbar: {keys}", parse_mode="Markdown")
        return
    active = db.toggle_strategy(chat_id, key)
    log.info(f"[{chat_id}] Strategie-Auswahl geändert: {key} → {active}")
    labels = ", ".join(strategies.get(k).label for k in active)
    await update.message.reply_text(
        f"✅ Aktive Strategien: *{labels}*\nKennzahlen mit /teststrat.",
        parse_mode="Markdown")


async def cmd_teststrat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/teststrat [name] — Backtest je gewählter Strategie (kuratierter Korb, 2 J. Tages-TF).
    Ohne Argument werden alle aktiven Strategien getestet, sonst die genannte."""
    chat_id = update.effective_chat.id
    user = _registered_user(chat_id)
    if not user:
        await update.message.reply_text("⚠️ Du bist noch nicht eingerichtet. Sende zuerst /start.")
        return

    if context.args and context.args[0].strip() in strategies.REGISTRY:
        strat_keys = [context.args[0].strip()]
    else:
        strat_keys = _user_strategies(user)

    region = user.get("market_region") or DEFAULT_REGION
    tickers = universes.get_tickers(region, auto=False)   # kuratierter Korb (schnell)
    labels = ", ".join(strategies.get(k).label for k in strat_keys)
    await update.message.reply_text(
        f"🧪 Backtest läuft: *{labels}* — {len(tickers)} Aktien ({REGION_LABELS.get(region, region)}), "
        f"2 Jahre Tages-TF.\nDas dauert ein bis zwei Minuten je Strategie, ich melde mich. ⏳",
        parse_mode="Markdown")

    async def _run():
        for key in strat_keys:
            try:
                res = await asyncio.to_thread(backtest.run_backtest, key, tickers, 2)
                txt = metrics.format_metrics(res["metrics"], title=f"Backtest: {res['label']}")
                txt += (f"\n_{res['n_tickers']} Aktien · {res['years']} Jahre · Tages-TF · "
                        f"long-only Demo (ohne Gebühren/Slippage)._")
                await context.bot.send_message(chat_id=chat_id, text=txt, parse_mode="Markdown")
            except Exception as e:
                log.error(f"[{chat_id}] /teststrat ({key}) fehlgeschlagen: {e}")
                await context.bot.send_message(chat_id=chat_id,
                                               text=f"⚠️ Backtest-Fehler ({key}): {e}")

    context.application.create_task(_run())


async def cmd_kicheck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/kicheck — Selbsttest: prüft, ob das KI-Ranking (Claude Haiku) funktioniert."""
    chat_id = update.effective_chat.id
    user = _registered_user(chat_id)
    if not user:
        await update.message.reply_text("⚠️ Du bist noch nicht eingerichtet. Sende zuerst /start.")
        return

    await update.message.reply_text("🔌 Prüfe KI-Ranking (Claude Haiku)… ⏳")
    res = await asyncio.to_thread(llm_ranker.health_check)

    if res["ok"]:
        top = (res.get("ranking") or [{}])[0]
        sample = (f"\nBeispiel: {top.get('ticker')} → {top.get('score')}/100"
                  if top.get("ticker") else "")
        note = "" if user.get("llm_rank", True) else "\n⚠️ In deinem Profil ist KI-Ranking AUS (/settings)."
        text = f"✅ KI-Ranking funktioniert.\n{res['detail']}{sample}{note}"
    else:
        text = (f"❌ KI-Ranking nicht aktiv/fehlerhaft.\n{res['detail']}\n\n"
                "Setze ANTHROPIC_API_KEY in der .env und starte den Bot neu.")
    # bewusst ohne parse_mode — Detailtext kann Sonderzeichen enthalten
    await update.message.reply_text(text)


async def cmd_brokercheck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/brokercheck — Selbsttest: prüft die Alpaca-Anbindung (Konto + Marktstatus)."""
    chat_id = update.effective_chat.id
    user = _registered_user(chat_id)
    if not user:
        await update.message.reply_text("⚠️ Du bist noch nicht eingerichtet. Sende zuerst /start.")
        return

    if not _alpaca_ready(user):
        await update.message.reply_text(
            "❌ Alpaca ist nicht verbunden.\nVerbinde dein Konto mit /connectalpaca "
            "(deine Keys werden verschlüsselt gespeichert).")
        return

    await update.message.reply_text("🔌 Prüfe Alpaca-Anbindung… ⏳")
    client = _alpaca_client(user)
    res = await asyncio.to_thread(broker.health_check, client=client)

    if res["ok"]:
        mode = "PAPER (Demo)" if res.get("paper") else "LIVE (echtes Geld!)"
        markt = "offen ✅" if res.get("market_open") else "geschlossen 🌙"
        exec_on = user.get("broker_exec")
        note = ("\n✅ Broker-Ausführung ist in deinem Profil AN."
                if exec_on else
                "\nℹ️ Broker-Ausführung ist AUS — es werden keine echten Orders gesendet (/settings).")
        text = (f"✅ Alpaca verbunden — {mode}\n"
                f"Konto: {res.get('status')} · Cash ${res.get('cash'):,.2f} · "
                f"Buying Power ${res.get('buying_power'):,.2f}\n"
                f"Markt: {markt}{note}")
    else:
        text = f"❌ Alpaca nicht verbunden.\n{res['detail']}"
    # bewusst ohne parse_mode — Detailtext kann Sonderzeichen enthalten
    await update.message.reply_text(text)


INFO_TEXT = (
    "📖 *So entstehen die Signale*\n"
    "Jede Aktie wird mit mehreren technischen Indikatoren über mehrere Zeiträume geprüft. "
    "Stimmen sie überein, entsteht ein Long-Signal mit einer Stärke von 0–100.\n"
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
    "Relatives Volumen (RVOL) vs. Schnitt. Im Intraday-Handel besonders wichtig: hohes "
    "Volumen (>1,5×) bestätigt Ausbrüche, zeigt Liquidität und institutionelles Interesse — "
    "ein Kursimpuls ohne Volumen ist oft ein Fehlausbruch. Daher stark gewichtet.\n\n"
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
    "🛑 *Stop-Loss / 🎯 Take-Profit*: aus der Schwankungsbreite (ATR) — je nach SL/TP-Modus "
    "(*aus* keine Grenzen / *passiv* eng / *normal* / *aggressiv* weit), einstellbar in /settings.\n"
    "⚡ *Hebel*: 1–10× (Profil-Default in /settings, pro Signal per Button änderbar). Höherer Hebel = "
    "größerer Gewinn/Verlust UND schnellere *Liquidation* (Long bei −1/Hebel, z. B. 10× → −10 %).\n"
    "🤖 *Auto-Accept*: Signale werden sofort gestartet (ohne JA/NEIN).\n"
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
    tickers = universes.get_tickers(region, auto=_auto_uni(user))
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
    "/settings — Körbe, Trade-Größe & Anzahl Signale ändern\n"
    "/tradesize <betrag> — Demo-Trade-Größe in € setzen (z. B. /tradesize 250)\n"
    "/dashboard — Link zu deinem Web-Dashboard\n"
    "/signals — Aktuelle Signale jetzt live abrufen\n"
    "/top5trade — Was große Trader (Insider + Institutionen) zuletzt gekauft haben\n"
    "/evaluate — Deine aktiven Demo-Trades jetzt auswerten\n"
    "/strategies — Verfügbare Signal-Strategien anzeigen\n"
    "/addstrat <name> — Strategie per Namen wählen (z. B. `adx_trend`)\n"
    "/teststrat — Backtest-Kennzahlen (Profitfaktor) der aktiven Strategie\n"
    "/kicheck — Prüfen, ob das KI-Ranking (Claude Haiku) funktioniert\n"
    "/connectalpaca — Eigenes Alpaca-Konto verbinden (Keys verschlüsselt speichern)\n"
    "/disconnectalpaca — Alpaca-Zugangsdaten wieder entfernen\n"
    "/brokercheck — Alpaca-Anbindung prüfen (Konto + Marktstatus)\n"
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

    parts = query.data.split(":")
    action = parts[0]
    ticker = parts[1] if len(parts) > 1 else ""

    # Deaktivierter "Börse geschlossen"-Button: nur Hinweis, keine Aktion
    if action == "noop":
        await query.answer("🔒 Börse geschlossen — Trade-Start erst bei geöffnetem Markt.", show_alert=True)
        return

    await query.answer()

    # Hebel pro Signal ändern (vor dem Start)
    if action == "lev":
        value = float(parts[2])
        trade = db.get_trade(chat_id, ticker)
        if not trade or trade["status"] != "pending":
            await query.answer("⚠️ Hebel nicht mehr änderbar (Trade bereits bearbeitet).", show_alert=True)
            return
        db.set_trade_leverage(chat_id, ticker, value)
        user = db.get_user(chat_id)
        updated = db.get_trade(chat_id, ticker)
        text, keyboard = _signal_card(updated["signal"], user["trade_size_eur"], market_open=True)
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
        except Exception:
            pass
        log.info(f"[{chat_id}] Hebel geändert: {ticker} → {value:g}×")
        return

    if action == "accept":
        trade = db.activate_trade(chat_id, ticker)
        if trade:
            lev = trade.get("signal", {}).get("leverage", 1.0) or 1.0
            await query.edit_message_reply_markup(reply_markup=None)
            await query.edit_message_text(
                query.message.text + f"\n\n✅ *Demo-Trade gestartet!*\nEinstiegskurs: ${trade['entry']:.2f} · Hebel {lev:g}×",
                parse_mode="Markdown"
            )
            log.info(f"[{chat_id}] Trade aktiviert: {ticker} @ ${trade['entry']:.2f} ({lev:g}×)")
            await _maybe_broker_order(context.bot, chat_id, trade)
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
        leverage = trade.get("signal", {}).get("leverage", 1.0) or 1.0
        current = get_current_price(ticker, entry)
        pnl_pct, pnl_eur = realized_pnl(entry, current, trade["direction"], user["trade_size_eur"], leverage)

        db.close_all(chat_id, [{
            "ticker": ticker, "exit": current, "pnl_eur": pnl_eur, "pnl_pct": pnl_pct,
        }])

        sign = "+" if pnl_eur >= 0 else ""
        emoji = "🟢" if pnl_eur > 0 else ("🔴" if pnl_eur < 0 else "⚪")
        # Ausstiegs-Signalstärke = letzter aufgezeichneter 60s-Tick (sonst unbekannt)
        _pts = db.get_today_ticks(chat_id).get(ticker, [])
        exit_strength = _pts[-1].get("strength") if _pts else None
        await query.edit_message_reply_markup(reply_markup=None)
        await query.edit_message_text(
            query.message.text +
            f"\n\n{emoji} *Verkauft* — Einstieg ${entry:.2f} → Ausstieg ${current:.2f} (Hebel {leverage:g}×)\n"
            f"Einstieg-Signal: {_fmt_strength(trade.get('signal', {}).get('strength'))} → "
            f"Ausstieg-Signal: {_fmt_strength(exit_strength)}  ·  "
            f"Realisiert: {sign}{pnl_pct:.1f}% ({sign}{pnl_eur:.2f}€)",
            parse_mode="Markdown"
        )
        # echte Alpaca-Position (falls vorhanden) mitschließen
        await _maybe_broker_close(context.bot, user, ticker)
        log.info(f"[{chat_id}] Trade verkauft: {ticker} @ ${current:.2f} ({sign}{pnl_eur:.2f}€)")

    elif action == "set_region":
        # 'ticker' enthält hier den Korb-Schlüssel (z. B. 'sp500') — Mehrfachauswahl (Toggle)
        if ticker in UNIVERSES:
            keys = db.toggle_region(chat_id, ticker)
            log.info(f"[{chat_id}] Markt-Körbe geändert: {keys}")
        user = db.get_user(chat_id)
        if user:
            text, keyboard = _settings_view(user)
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)

    elif action == "set_size":
        # 'ticker' enthält hier die Trade-Größe in € als String
        try:
            saved = db.set_trade_size(chat_id, float(ticker))
            log.info(f"[{chat_id}] Demo-Trade-Größe geändert: {saved:.0f}€")
        except ValueError:
            pass
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

    elif action in ("set_mode", "set_lev", "set_auto", "set_uni", "set_strat", "set_llm", "set_eod", "set_broker"):
        if action == "set_mode" and ticker in SL_TP_MODES:
            db.set_sl_tp_mode(chat_id, ticker)
        elif action == "set_lev":
            try:
                db.set_leverage(chat_id, float(ticker))
            except ValueError:
                pass
        elif action == "set_auto":
            db.set_auto_accept(chat_id, ticker == "1")
        elif action == "set_uni":
            db.set_auto_universe(chat_id, ticker == "1")
        elif action == "set_strat" and ticker in strategies.REGISTRY:
            db.toggle_strategy(chat_id, ticker)
        elif action == "set_llm":
            db.set_llm_rank(chat_id, ticker == "1")
        elif action == "set_eod":
            db.set_eod_close(chat_id, ticker == "1")
        elif action == "set_broker" and _alpaca_ready(db.get_user(chat_id) or {}):
            db.set_broker_exec(chat_id, ticker == "1")
        log.info(f"[{chat_id}] Einstellung geändert: {action}={ticker}")
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

    # Harmlos: ein Settings-Button wurde auf den bereits aktiven Zustand getippt →
    # editMessageText mit identischem Inhalt. Kein Nutzer-Hinweis nötig.
    if isinstance(err, BadRequest) and "not modified" in str(err).lower():
        log.debug(f"Ignoriere 'Message is not modified': {err}")
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
    from stockbot.web.dashboard import run as run_dashboard

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
    # Alpaca-Zugangsdaten-Dialog (eigener ConversationHandler für /connectalpaca)
    app.add_handler(connect_alpaca_handler)
    # Manuelle Befehle: jederzeit aufrufbar, sobald der Bot läuft
    app.add_handler(CommandHandler("help", cmd_help))                     # Befehlsübersicht
    app.add_handler(CommandHandler("profile", cmd_profile))               # eigenes Profil ansehen
    app.add_handler(CommandHandler("settings", cmd_settings))             # Markt-Bereich + Anzahl ändern
    app.add_handler(CommandHandler("tradesize", cmd_tradesize))           # Demo-Trade-Größe ändern
    app.add_handler(CommandHandler("info", cmd_info))                     # Metriken erklärt
    app.add_handler(CommandHandler("dashboard", cmd_dashboard))           # Link zum Web-Dashboard
    app.add_handler(CommandHandler("ping", cmd_ping))                     # Verbindungstest
    app.add_handler(CommandHandler("signals", cmd_signals))               # echte Live-Analyse jetzt sofort
    app.add_handler(CommandHandler("top5trade", cmd_top5trade))           # was große Trader handeln
    app.add_handler(CommandHandler("evaluate", cmd_evaluate))             # aktive Demo-Trades jetzt sofort auswerten
    app.add_handler(CommandHandler("strategies", cmd_strategies))         # verfügbare Strategien
    app.add_handler(CommandHandler("addstrat", cmd_addstrat))             # Strategie per Namen wählen
    app.add_handler(CommandHandler("teststrat", cmd_teststrat))           # Backtest-Kennzahlen der aktiven Strategie
    app.add_handler(CommandHandler("kicheck", cmd_kicheck))               # Selbsttest des KI-Rankings (Claude Haiku)
    app.add_handler(CommandHandler("brokercheck", cmd_brokercheck))       # Selbsttest der Alpaca-Anbindung
    app.add_handler(CommandHandler("disconnectalpaca", cmd_disconnect_alpaca))   # Alpaca-Keys löschen
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
