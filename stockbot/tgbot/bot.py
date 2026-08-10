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
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import Conflict, BadRequest
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, filters,
)

from stockbot.core import db
from stockbot.core import exchange_calendar
from stockbot.core.glossary import broker_status_label  # §32.9: geteiltes Web↔Telegram-Glossar
from stockbot.market import universes
from stockbot.market import smartmoney
from stockbot.market import strategies
from stockbot.market import analyzer
from stockbot.market import exit_policies
from stockbot.market import provider_factory
from stockbot.research import shadow_scheduler
from stockbot.backtest import engine as backtest
from stockbot.core import metrics
from stockbot.core import outbox
from stockbot.core.event_consumers import ObservabilityConsumer
from stockbot.core.settings import validate_config, assert_postgres_backend
from stockbot.core.logging_setup import configure_logging
from stockbot.core.domain import Mode, Order, OrderStatus, Signal, SignalStatus, TradeIntent
from stockbot.execution.oms import OrderManagementSystem, _as_order
from stockbot.execution.partial_fill_orchestrator import orchestrate_partial_fill
from stockbot.execution import risk_context
from stockbot.execution.post_trade_scan import run_post_trade_scan
from stockbot.execution.broker_poll import poll_broker_orders
from stockbot.execution.reconcile_scheduler import (
    finding_keys as reconciliation_finding_keys,
    format_admin_alarm as format_reconciliation_admin_alarm,
    reconcile_user_oms,
)
from stockbot.ai import llm_ranker
from stockbot.broker import client as broker
from stockbot.broker import sizing
from stockbot.broker import reconcile as reconcile_mod
from stockbot.tgbot.onboarding import onboarding_conv_handler
from stockbot.tgbot import menu
from stockbot.tgbot import callback_security
from stockbot.broker.setup import connect_alpaca_handler, disconnect as cmd_disconnect_alpaca
from stockbot.market.analyzer import analyze_universe, sl_tp_from_atr
from stockbot.services import trades as trade_svc, settings as settings_svc, watchlist as watchlist_svc
from stockbot.services import notifications as notify_svc
from stockbot.core.evaluator import (
    evaluate_trades, get_current_price, trade_pnl, liquidation_price, effective_leverage,
)
from stockbot.core.kill_switch import KillSwitchService
from stockbot.config import (
    TELEGRAM_TOKEN,
    SIGNAL_TIME_HOUR, SIGNAL_TIME_MIN,
    CLOSE_TIME_HOUR, CLOSE_TIME_MIN,
    BERLIN_TZ, DASHBOARD_BASE_URL, RUN_DASHBOARD_IN_BOT,
    UNIVERSES, REGION_LABELS, DEFAULT_REGION, DEFAULT_AUTO_UNIVERSE,
    SIGNAL_COUNT_CHOICES, TOP_N_SIGNALS, MAX_SIGNALS, TRADE_SIZE_EUR, TRADE_SIZE_CHOICES,
    SMARTMONEY_SCAN_HOUR, SMARTMONEY_SCAN_MIN,
    LAB_DAILY_OPTIMIZATION, LAB_DAILY_DAYS, LAB_DAILY_HOUR, LAB_DAILY_MIN,
    BROKER_RECONCILE_HOUR, BROKER_RECONCILE_MIN,
    SIGNAL_OPEN_OFFSET_MIN, CLOSE_AFTER_CLOSE_OFFSET_MIN, SESSION_TICK_INTERVAL_SEC,
    ENTRY_CUTOFF_BEFORE_CLOSE_MIN,
    SIGNAL_CLOSE_THRESHOLD, MONITOR_INTERVAL_SEC, INTRADAY_SCAN_INTERVAL_SEC,
    POST_TRADE_SCAN_INTERVAL_SEC, BROKER_POLL_INTERVAL_SEC, RECONCILE_PERIODIC_SEC,
    SL_TP_MODES, DEFAULT_SL_TP_MODE, DEFAULT_LEVERAGE, STRATEGY_EXITS_ENABLED,
    LLM_RANK_ENABLED, DEFAULT_EOD_CLOSE, HOLD_MAX_DAYS,
    EXTENDED_HOURS, ALPACA_ENABLED, ALPACA_PAPER, ADMIN_CHAT_ID,
    ALPACA_API_KEY, ALPACA_API_SECRET, ENCRYPTION_KEY, LOG_FILE, LOG_FORMAT,
    SHARE_ROUNDUP_FACTOR, MAX_LEVERAGE,
)

os.makedirs(os.path.dirname(LOG_FILE) or ".", exist_ok=True)   # Log-Ordner sicherstellen (fehlt bei frischem Klon)
configure_logging(
    log_format=LOG_FORMAT, log_file=LOG_FILE, service="stockbot-bot",
    pseudonym_key=ENCRYPTION_KEY,
)
log = logging.getLogger(__name__)

TRADE_ACTIVATION_WINDOW_MIN = 15  # Zeitfenster, in dem ein Signal per JA noch gestartet werden kann
BROKER_REPRICE_AFTER_SEC = 300    # 5 Minuten bis zum erneuten Repricing offener Broker-Sells
OUTBOX_DELIVERY_SEC = 60          # Zustell-Takt der Domain-Event-Outbox (W4.5)
_OUTBOX_CONSUMER = ObservabilityConsumer()
BROKER_POSITION_MISSING_AFTER_SEC = 300  # nach 5 Minuten fehlender Broker-Position wird der Trade als geschlossen markiert
BROKER_QUEUE_MAX_AGE_SEC = 24 * 3600  # vorgemerkte Bruchteil-Order verfällt nach 24 h (Signal veraltet)


def _load_oms_signal(signal_id: int) -> Signal | None:
    """Bridge vom bisherigen Trade-JSON zum Phase-4-Signalobjekt."""
    trade = db.get_trade_by_id(signal_id)
    if trade is None:
        return None
    sig = trade.get("signal") or {}
    return Signal(
        id=signal_id, strategy_version_id=int(sig.get("strategy_version_id") or 0),
        ticker=trade["ticker"], direction=trade["direction"], mode=Mode.PAPER,
        status=SignalStatus.ACCEPTED, expires_at=sig.get("expires_at"),
    )


kill_switch_service = KillSwitchService(persistence=db, load_on_init=False)

_oms = OrderManagementSystem(
    signal_loader=_load_oms_signal, context_loader=risk_context.signal_context,
    broker_adapter=broker, persistence=db, audit_sink=db.append_audit_event,
    kill_switch_checker=kill_switch_service.is_new_position_allowed,
)


def _reprice_limit_price(current: float, side: str, age_sec: float) -> float:
    """Stufenweise aggressiver: nach 5/10/15 Minuten wird das Limit enger."""
    step = max(1, min(3, int(age_sec // BROKER_REPRICE_AFTER_SEC)))
    if side == "BUY":
        factor = {1: 1.005, 2: 1.0075, 3: 1.01}[step]
    else:
        factor = {1: 0.995, 2: 0.9925, 3: 0.99}[step]
    return float(current) * factor

# ── Kandidaten-Cache (für das Nachrücken ohne Duplikate) ────────────────────

_candidates_cache: dict[str, dict] = {}   # key -> {"date": 'YYYY-MM-DD' (UTC), "ranked": [signal, ...]}


def _auto_uni(user: dict) -> bool:
    """Ob der Nutzer das Voll-Universum (automatisch geladene Vollliste) nutzt."""
    return user.get("auto_universe", DEFAULT_AUTO_UNIVERSE)


def _user_strategies(user: dict) -> list[str]:
    """Aktive Signal-Strategien des Nutzers (Liste von Schlüsseln aus strategies.REGISTRY)."""
    keys = user.get("strategies") or [s.strip() for s in (user.get("strategy") or "").split(",") if s.strip()]
    return [k for k in keys if k in strategies.REGISTRY] or [strategies.DEFAULT_STRATEGY]


def _user_watchlist(user: dict) -> list[str]:
    """Persönliche Watchlist-Symbole des Nutzers (kann leer sein, großgeschrieben)."""
    return [t.strip().upper() for t in (user.get("watchlist") or []) if t and t.strip()]


def _trade_strategy_key(trade: dict) -> str:
    """Strategie-Schlüssel eines Trades (aus dem gespeicherten Signal; Fallback 'standard')."""
    key = (trade.get("signal") or {}).get("strategy") or "standard"
    return key if key in strategies.REGISTRY else "standard"


def _user_regions(user: dict) -> list[str]:
    """Aktive Markt-Körbe des Nutzers (Liste von Schlüsseln aus UNIVERSES, mind. einer)."""
    keys = user.get("market_regions") or [user.get("market_region") or DEFAULT_REGION]
    return [k for k in keys if k in UNIVERSES] or [DEFAULT_REGION]


def _merge_ranked(lists: list) -> list[dict]:
    """Vereinigt Regionslisten *derselben* Strategie nach deren internem Rohscore.

    Diese Funktion darf nicht zum Zusammenführen verschiedener Strategien verwendet werden;
    dafür existiert :func:`_interleave_strategy_rankings` ohne Skalenvergleich.
    """
    best: dict[str, dict] = {}
    for lst in lists:
        for s in (lst or []):
            t = s.get("ticker")
            if not t:
                continue
            score = s.get("raw_score", s.get("strength", 0)) or 0
            best_score = best.get(t, {}).get("raw_score", best.get(t, {}).get("strength", 0)) or 0
            if t not in best or score > best_score:
                best[t] = s
    return sorted(best.values(),
                  key=lambda s: s.get("raw_score", s.get("strength", 0)) or 0,
                  reverse=True)


def _interleave_strategy_rankings(rankings: dict[str, list[dict]],
                                  limit: int | None = None) -> list[dict]:
    """Führt strategiespezifische Ranglisten deterministisch im Rundlauf zusammen.

    Die Listenreihenfolge ist der Rang *innerhalb* der jeweiligen Strategie. Kommt ein Ticker
    mehrfach vor, gewinnt der kleinere Rangindex; bei gleichem Rang der alphabetisch erste
    Strategie-Key. Rohscores verschiedener Strategien werden zu keinem Zeitpunkt verglichen.
    """
    clean = {key: [s for s in (rankings.get(key) or []) if s.get("ticker")]
             for key in sorted(rankings)}
    winner: dict[str, tuple[int, str]] = {}
    for key, signals in clean.items():
        for rank, signal in enumerate(signals):
            candidate = (rank, key)
            ticker = signal["ticker"]
            if ticker not in winner or candidate < winner[ticker]:
                winner[ticker] = candidate

    merged: list[dict] = []
    max_len = max((len(signals) for signals in clean.values()), default=0)
    for rank in range(max_len):
        for key, signals in clean.items():
            if rank >= len(signals):
                continue
            signal = signals[rank]
            if winner[signal["ticker"]] != (rank, key):
                continue
            merged.append(signal)
            if limit is not None and len(merged) >= limit:
                return merged
    return merged


def _strategy_label(signal_or_key) -> str:
    key = signal_or_key if isinstance(signal_or_key, str) else (signal_or_key or {}).get("strategy")
    key = key or strategies.DEFAULT_STRATEGY
    return strategies.get(key).label


def _llm_enabled(user: dict) -> bool:
    """Ob das LLM-Ranking (Claude Haiku) für diesen Nutzer aktiv ist (Schalter + globaler Key)."""
    return LLM_RANK_ENABLED and user.get("llm_rank", True)


def _alpaca_ready(user: dict) -> bool:
    """Ob für diesen Nutzer eine EIGENE Alpaca-Anbindung hinterlegt ist.

    Bewusst ohne globalen Fallback: die globalen Keys sind der Betreiber-Datenzugang für den
    Signal-Scan (Marktdaten sind nutzerunabhängig), kein Handelskonto für fremde Nutzer.
    """
    return bool(user and user.get("broker_platform") == "alpaca")


def _alpaca_client(user: dict):
    """Baut den Alpaca-Client aus den EIGENEN, verschlüsselt gespeicherten Keys — sonst None.

    Kein Rückfall auf die globalen Keys: sonst handelte ein Nutzer ohne eigene Anbindung über
    das Konto des Betreibers.
    """
    if user and user.get("broker_platform") == "alpaca":
        creds = db.get_decrypted_credentials(user["user_id"])
        if creds:
            return broker.make_client(creds[0], creds[1], paper=ALPACA_PAPER)
    return None


def _broker_reconcile_enabled(user: dict, *, full: bool = False) -> bool:
    """Ob ein User für Bot↔Alpaca-Abgleich berücksichtigt wird.

    Normaler 60s-Monitor bleibt konservativ: nur `broker_exec=true`.
    Der tägliche Vollabgleich (`full=True`) nimmt zusätzlich Nutzer mit eigener Alpaca-Verbindung,
    auch wenn Broker-Ausführung gerade deaktiviert ist. Bei globalen .env-Keys bleibt `broker_exec`
    Pflicht, damit ein globales Konto nicht versehentlich allen Usern zugeordnet wird.
    """
    if not user or not _alpaca_ready(user):
        return False
    if user.get("broker_exec"):
        return True
    return bool(full and user.get("broker_platform") == "alpaca")


def _alpaca_keys(user: dict) -> tuple[str | None, str | None]:
    """Roh-Keys für die **Options-Marktdaten**: eigene des Nutzers, sonst die globalen.

    Der globale Rückfall ist hier korrekt (Marktdaten sind nutzerunabhängig, dafür ist der
    Betreiber-Zugang da). Für die **Order-Ausführung** gilt das Gegenteil — siehe `_alpaca_client`.
    """
    if user and user.get("broker_platform") == "alpaca":
        creds = db.get_decrypted_credentials(user["user_id"])
        if creds:
            return creds[0], creds[1]
    return ALPACA_API_KEY, ALPACA_API_SECRET


def _make_option_selector(user: dict, client, ticker: str, price: float):
    """Closure für sizing.plan_order / attach_option_for_trade: wählt bei Hebel>1 einen passenden
    Long-Call fürs Budget. Kapselt Alpaca-Trading-Client (Kontrakte) + Roh-Keys (Snapshots/Greeks)."""
    key, sec = _alpaca_keys(user)

    def _selector(budget: float, target_leverage: float):
        return broker.select_option_for_leverage(
            ticker, price, target_leverage, budget,
            client=client, api_key=key, api_secret=sec)

    return _selector


def _attach_demo_option(user: dict, trade: dict) -> dict | None:
    """Wählt bei Hebel>1 (und verfügbaren Alpaca-Daten) einen Long-Call fürs Budget und schreibt
    ihn ins Signal des aktiven Trades — Grundlage der Demo-Options-Simulation und (bei broker_exec)
    der echten Order. Synchron (Alpaca-Calls) — über asyncio.to_thread aufrufen."""
    entry = trade.get("entry") or (trade.get("signal") or {}).get("price")
    if not entry:
        return None
    selector = _make_option_selector(user, _alpaca_client(user), trade["ticker"], float(entry))
    return trade_svc.attach_option_for_trade(user, trade, selector)


async def _reconcile_and_alert(bot: Bot, user: dict, client, *, context: str):
    """Gleicht nach einem Broker-Vorgang die Bot-Sicht (aktive Trades) gegen die echten
    Alpaca-Positionen ab. Bei Abweichung: ausführliches Error-Log + Telegram-Meldung an den
    Nutzer (und optional an ADMIN_CHAT_ID). Best-effort — wirft nie."""
    if not user or not user.get("broker_exec") or not _alpaca_ready(user) or client is None:
        return
    try:
        rep = await asyncio.to_thread(reconcile_mod.reconcile_user, user, client)
    except Exception as e:
        log.warning(f"[{user['user_id']}] Reconcile fehlgeschlagen: {e}")
        return
    if rep.get("ok"):
        return
    detail = rep.get("detail", "")
    log.error(f"[{user['user_id']}] Positions-Abweichung ({context}):\n{detail}")
    msg = (f"🛑 *Positions-Abweichung* ({context})\n"
           f"Bot-Trades und echte Alpaca-Positionen stimmen nicht überein:\n\n{detail}\n\n"
           f"Bitte prüfen — ggf. manuell ausgleichen oder zurücksetzen.")
    try:
        await bot.send_message(chat_id=user["user_id"], text=msg, parse_mode="Markdown")
        if ADMIN_CHAT_ID and ADMIN_CHAT_ID != user["user_id"]:
            await bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"[user {user['user_id']}] {msg}", parse_mode="Markdown")
    except Exception as e:
        log.warning(f"[{user['user_id']}] Abweichungs-Meldung nicht zustellbar: {e}")


def _universe_key(region: str, auto: bool, strategy: str = strategies.DEFAULT_STRATEGY) -> str:
    """Cache-/Analyse-Schlüssel: Region + Voll-Universum-Schalter + Strategie
    (alle drei können verschiedene Signal-Mengen ergeben)."""
    return f"{region}:{int(bool(auto))}:{strategy}"


def _cache_candidates(key: str, ranked: list[dict]):
    _candidates_cache[key] = {"date": db.today_utc(), "ranked": ranked}


def _get_candidates(key: str) -> list[dict]:
    e = _candidates_cache.get(key)
    return e["ranked"] if e and e["date"] == db.today_utc() else []


# ── Nachrichten senden ──────────────────────────────────────────────────────

# Sichere Callback-Tokens (W7): TTLs decken die legitimen Nutzungsfenster ab —
# Signale bleiben den ganzen Handelstag annehmbar, Verkaufen-Buttons auch über Nacht.
SIGNAL_CB_TTL_SEC = 16 * 3600
SELL_CB_TTL_SEC = 7 * 24 * 3600


def _secure_cb(user_id: int | None, action: str, ticker: str,
               ttl_seconds: int = SIGNAL_CB_TTL_SEC) -> str:
    """Opaques, nutzergebundenes Callback-Token (`t:<token>`) statt manipulierbarer
    Klartext-callback_data. Fail-open aufs Legacy-Format (`action:ticker`), wenn kein
    user_id vorliegt oder die Token-Ausgabe scheitert — ein Button darf nie am DB-Zustand
    scheitern; der Legacy-Parser bleibt als Fallback verdrahtet."""
    if user_id is not None:
        try:
            token = callback_security.issue(
                int(user_id), action, {"ticker": ticker}, ttl_seconds=ttl_seconds)
            return f"t:{token}"
        except Exception as e:
            log.warning(f"Callback-Token-Ausgabe fehlgeschlagen ({action}:{ticker}): {e}")
    return f"{action}:{ticker}"


def _signal_card(signal: dict, trade_size_eur: float, market_open: bool,
                 expiry_min: int | None = None,
                 user_id: int | None = None) -> tuple[str, InlineKeyboardMarkup]:
    """Baut Nachrichtentext + Tastatur eines Signals (inkl. SL/TP, Hebel, Liquidation)."""
    ticker = signal["ticker"]
    direction = signal["direction"]
    direction_emoji = "🟢 LONG" if direction == "long" else "🔴 SHORT"
    raw_score = signal.get("raw_score", signal.get("strength"))
    strategy_label = _strategy_label(signal)
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
        window_line = (f"⏰ Start nur innerhalb von {expiry_min} Minuten möglich"
                       if expiry_min else "✅ Jederzeit annehmbar (kein Zeitlimit)")
        footer = (
            f"{window_line}\n"
            f"⏱ Auswertung: {CLOSE_TIME_HOUR:02d}:{CLOSE_TIME_MIN:02d} Uhr (oder früher bei SL/TP)"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ JA — Demo-Trade starten",
                                  callback_data=_secure_cb(user_id, "accept", ticker)),
             InlineKeyboardButton("❌ NEIN",
                                  callback_data=_secure_cb(user_id, "reject", ticker))],
        ])
    else:
        footer = "🔒 US-Börse geschlossen — Start möglich, sobald der Markt wieder öffnet."
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔒 Börse geschlossen", callback_data=f"noop:{ticker}")]
        ])

    watch_badge = "  📋 Watchlist" if signal.get("watchlist") else ""
    text = (
        f"📊 *{ticker}* — {direction_emoji}{watch_badge}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 Kurs: *${signal['price']:.2f}*\n"
        f"📈 Strategie-Rohscore ({strategy_label}): {raw_score:.0f} — keine Gewinnwahrscheinlichkeit\n"
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


def _planned_order_cost(plan: dict, entry: float) -> float:
    if not plan or plan.get("kind") == "none":
        return 0.0
    if plan.get("kind") == "option":
        return float(plan.get("premium") or 0.0) * 100.0 * float(plan.get("qty") or 0.0)
    if plan.get("notional") is not None:
        return float(plan.get("notional") or 0.0)
    if plan.get("qty") is not None:
        return float(plan.get("qty") or 0.0) * float(entry or 0.0)
    return 0.0


async def _ensure_buying_power_for_bot(bot: Bot, chat_id: int, ticker: str, client, plan: dict, entry: float, mode: str) -> bool:
    needed = _planned_order_cost(plan, entry)
    if needed <= 0:
        return True
    acct = await asyncio.to_thread(broker.account_summary, client)
    if not acct.get("ok"):
        return True  # Account-Check nicht verfügbar: Alpaca entscheidet final.
    buying_power = float(acct.get("buying_power") or 0.0)
    if buying_power + 1e-9 >= needed:
        return True
    db.mark_broker_failed(chat_id, ticker, broker_status="insufficient_buying_power")
    await bot.send_message(
        chat_id=chat_id,
        text=(f"⚠️ Alpaca-{mode}: Buying Power reicht nicht für {ticker}.\n"
              f"Verfügbar: ${buying_power:.2f} · benötigt ca. ${needed:.2f}.\n"
              "Keine Order gesendet; der Trade wird als Broker-fehlgeschlagen markiert."),
    )
    return False


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
    mode = "PAPER" if ALPACA_PAPER else "LIVE"
    if leverage > MAX_LEVERAGE + 1e-9:
        await asyncio.to_thread(db.mark_broker_failed, chat_id, ticker, broker_status="leverage_blocked")
        await _tg_status(
            bot, user,
            (f"⛔ Alpaca-{mode}: Order für {ticker} abgelehnt — Hebel {leverage:g}× über "
             f"erlaubtem Maximum {MAX_LEVERAGE:g}× (TSAFE-002)."))
        return
    budget = float(user["trade_size_eur"])     # Trade-Wert = Budget; Hebel hebelt NICHT den Einsatz
    extended = EXTENDED_HOURS and not _us_market_open(extended=False)

    # Order-Plan: Hebel>1 (reguläre Zeit) → Option fürs Budget; sonst ganze Aktien fürs Budget.
    # Wurde bei der Aktivierung bereits ein Kontrakt für die Demo-Simulation gewählt, exakt den kaufen.
    if sig.get("option_symbol") and not extended:
        plan = {"kind": "option", "option_symbol": sig["option_symbol"], "qty": int(sig["contracts"]),
                "premium": float(sig["entry_premium"]), "delta": sig.get("delta"),
                "omega": sig.get("omega"), "strike": sig.get("strike"), "expiry": sig.get("expiry")}
    else:
        plan = await asyncio.to_thread(
            sizing.plan_order, float(entry), budget, leverage,
            option_selector=_make_option_selector(user, client, ticker, float(entry)),
            extended=extended, roundup_factor=1.0)   # TSAFE-004: nie über Budget aufrunden

    if plan["kind"] == "none":
        db.mark_broker_failed(chat_id, ticker, broker_status="not_submitted")
        await _tg_status(
            bot, user,
            (f"ℹ️ Alpaca-{mode}: Budget {budget:.0f}$ reicht nicht für {ticker} "
             f"(${float(entry):.2f}). Keine Order gesendet."))
        return

    if not await _ensure_buying_power_for_bot(bot, chat_id, ticker, client, plan, float(entry), mode):
        return

    # Hebel>1, aber als Aktien ausgeführt (kein Optionskontrakt bezahlbar) → Hebel NICHT realisiert.
    # effektiver Hebel = 1 ins Signal schreiben, damit P&L nicht überzeichnet (= echte Position).
    if plan["kind"] == "shares" and leverage > 1.0:
        await asyncio.to_thread(db.merge_active_trade_signal, chat_id, ticker, {"effective_leverage": 1.0})

    if plan["kind"] == "option":
        await _tg_status(
            bot, user,
            (f"🎯 Hebel {leverage:g}× → Option statt Aktien: {plan['option_symbol']} "
             f"(≈{plan['omega']:g}× Hebel, Strike {plan['strike']:g}, Verfall {plan['expiry']}), "
             f"{plan['qty']}× Kontrakt(e) ≈ ${plan['premium'] * 100 * plan['qty']:.2f}."))
        order_label = f"{plan['qty']}× {plan['option_symbol']}"
    elif plan.get("qty"):
        # Ganze Aktien fürs Budget (reguläre Zeit: Market-qty; erweiterte Zeit: Limit/Ext).
        order_label = f"{plan['qty']:g} {ticker}"
    else:
        # Aktie teurer als Budget → Bruchteil-Fall (Notional). Bruchteile gehen NUR in der regulären
        # US-Sitzung. Außerhalb → Order vormerken (Queue) und beim nächsten regulären Open senden.
        if extended:
            db.mark_broker_pending(chat_id, ticker, order_id=None, broker_status="queued_regular")
            await _tg_status(
                bot, user,
                (f"🕒 Alpaca-{mode}: {ticker} (${float(entry):.2f}) ist teurer als dein Budget "
                 f"{budget:.0f}$ → Bruchteil-Order. Bruchteile gehen außerhalb der regulären "
                 f"US-Sitzung nicht — die Order ist *vorgemerkt* und wird beim nächsten regulären "
                 f"Börsenstart automatisch gesendet."),
                parse_mode="Markdown")
            return
        order_label = f"{ticker} ${plan['notional']:.2f} (Bruchteil)"

    intent = TradeIntent(
        user_id=chat_id, signal_id=int(trade["id"]), requested_action="accept",
        accepted_exit_policy="strategy-default", source_channel="telegram",
        created_at=datetime.now(timezone.utc).isoformat(),
        idempotency_key=f"telegram:{chat_id}:{trade['id']}:accept",
    )
    oms_result = await asyncio.to_thread(
        _oms.submit_intent,
        intent,
        price=float(entry),
        trade_size=budget,
        leverage=leverage,
        risk_context={
            "is_live_account": broker._is_live_order(client),
            "is_option": plan["kind"] == "option",
            "extended": extended,
            "roundup_factor": 1.0,
            "entry_price": float(entry),
            "candidate_notional": budget,
            **risk_context.account_context(client, chat_id),
            **risk_context.quote_context(ticker),
        },
        broker_client=client,
    )
    res = {
        "ok": oms_result.ok and oms_result.order is not None,
        "id": oms_result.order.broker_order_id if oms_result.order else "",
        "detail": oms_result.reason or order_label,
    }

    if not res["ok"]:
        # OBS-001: den echten Ablehngrund (OMS-/Risk-Gate-Code) persistieren statt des
        # generischen „submit_failed" — sonst ist im Dashboard/Report nicht erkennbar, WARUM
        # nicht gekauft wurde (z. B. Positionslimit erreicht). Und IMMER loggen: bei Auto-Accept
        # unterdrückt `_tg_status` die Telegram-Meldung, der Journal-Eintrag darf nicht fehlen.
        reject_code = oms_result.code or "submit_failed"
        db.mark_broker_failed(chat_id, ticker, broker_status=reject_code)
        log.warning("[%s] Kauf %s abgelehnt: %s (%s)",
                    chat_id, ticker, oms_result.reason or "—", reject_code)
        await _tg_status(bot, user, f"⚠️ Alpaca-Order nicht angenommen: {res['detail']}")
        return

    db.mark_broker_pending(chat_id, ticker, order_id=res.get("id", ""), broker_status="accepted")
    await _tg_status(
        bot, user,
        f"📨 Alpaca-{mode}-Order angenommen: {res['detail']}. Warte auf Ausführung…")

    fill = await _await_fill(res.get("id", ""), client)
    status = fill.get("status", "unbekannt")
    if status == "filled":
        q = fill.get("filled_qty", 0.0)
        px = fill.get("filled_avg_price", 0.0)
        db.mark_broker_filled(chat_id, ticker, broker_status=status, filled_qty=q, filled_avg_price=px)
        await _tg_status(
            bot, user,
            (f"✅ Alpaca-{mode}-Order ausgeführt: {q:g} × @ ${px:.2f} (≈ ${q * px:.2f}) [{order_label}]\n"
             f"SL/TP überwacht der Bot und schließt die Position automatisch."))
        await _reconcile_and_alert(bot, user, client, context="nach Kauf")
    elif status in ("rejected", "canceled", "expired"):
        db.mark_broker_failed(chat_id, ticker, broker_status=status)
        await _tg_status(
            bot, user,
            f"⚠️ Alpaca-Order nicht ausgeführt (Status: {broker_status_label(status)}). "
            f"Es wurde nichts gekauft.")
    else:
        db.mark_broker_pending(chat_id, ticker, order_id=res.get("id", ""), broker_status=status)
        # angenommen, aber (noch) nicht gefüllt — z. B. Markt zu / Limit nicht erreicht
        await _tg_status(
            bot, user,
            (f"⏳ Alpaca-Order angenommen, aber noch nicht ausgeführt "
             f"(Status: {broker_status_label(status)}).\n"
             f"Es ist eine DAY-Order — sie füllt sich automatisch, sobald der Markt/Kurs passt, "
             f"sonst verfällt sie zum Handelsschluss. Du musst nichts tun."))


async def _tg_status(bot: Bot, user: dict, text: str, **kwargs):
    """Sendet eine Statusmeldung zu einem einzelnen Trade (Kauf/Verkauf/Broker etc.).

    Bei Auto-Accept werden diese Einzelmeldungen UNTERDRÜCKT — der Nutzer bekommt stattdessen
    nach Börsenschluss EINEN gebündelten Tagesreport (siehe close_and_evaluate)."""
    if user and user.get("auto_accept"):
        return None
    return await bot.send_message(chat_id=user["user_id"], text=text, **kwargs)


async def _maybe_broker_close(bot: Bot, user: dict, ticker: str, *, broker_symbol: str | None = None):
    """Schließt (falls Broker-Ausführung an) die echte Alpaca-Position. Best-effort.
    `broker_symbol` ist bei Options der Kontrakt (sonst = Ticker). Meldet nur bei tatsächlicher
    Schließung oder echtem Fehler; gleicht danach Bot- gegen Alpaca-Positionen ab."""
    if not user or not user.get("broker_exec") or not _alpaca_ready(user):
        return
    client = _alpaca_client(user)
    if client is None:
        return
    symbol = broker_symbol or ticker
    res = await asyncio.to_thread(broker.close_position, symbol, client=client)
    mode = "PAPER" if ALPACA_PAPER else "LIVE"
    if res.get("closed"):
        await _tg_status(bot, user, f"📉 Alpaca-{mode}: Position {symbol} geschlossen.")
    elif not res.get("ok"):
        await _tg_status(bot, user, f"⚠️ Alpaca: {symbol} konnte nicht geschlossen werden: {res['detail']}")
    await _reconcile_and_alert(bot, user, client, context="nach Schließung")


async def _maybe_broker_close_trade(bot: Bot, user: dict, trade: dict, *, broker_symbol: str | None = None) -> dict:
    """Broker-gestützter Sell-Flow: Order senden, Status poll-en und DB erst bei Fill schließen."""
    if not user or not user.get("broker_exec") or not _alpaca_ready(user):
        return {"ok": False, "reason": "broker_unavailable"}
    client = _alpaca_client(user)
    if client is None:
        return {"ok": False, "reason": "broker_unavailable"}

    symbol = broker_symbol or trade["ticker"]
    res = await asyncio.to_thread(broker.close_position, symbol, client=client)
    mode = "PAPER" if ALPACA_PAPER else "LIVE"

    if not res.get("ok"):
        db.mark_broker_close_failed(user["user_id"], trade["ticker"], broker_status=res.get("detail"))
        await _tg_status(
            bot, user,
            f"⚠️ Alpaca-{mode}: Verkauf von *{symbol}* konnte nicht gestartet werden: {res['detail']}",
            parse_mode="Markdown",
        )
        await _reconcile_and_alert(bot, user, client, context="nach Verkaufsfehler")
        return {"ok": False, "status": "submit_failed", "detail": res.get("detail")}

    order_id = res.get("id")
    if not order_id:
        db.mark_broker_close_failed(user["user_id"], trade["ticker"], broker_status="missing_order_id")
        await _tg_status(
            bot, user,
            f"⚠️ Alpaca-{mode}: Verkauf von *{symbol}* wurde ohne Order-ID angenommen.",
            parse_mode="Markdown",
        )
        await _reconcile_and_alert(bot, user, client, context="nach Verkaufsfehler")
        return {"ok": False, "status": "missing_order_id"}

    fill = await asyncio.to_thread(broker.get_order_status, order_id, client)
    status = fill.get("status", "unbekannt")
    if status == "filled":
        q = fill.get("filled_qty", 0.0)
        px = fill.get("filled_avg_price", 0.0) or fill.get("avg_fill_price", 0.0) or trade.get("entry", 0.0)
        pnl_pct, pnl_eur = trade_pnl(trade, float(px), user["trade_size_eur"])
        db.close_all(user["user_id"], [{"ticker": trade["ticker"], "exit": float(px),
                                        "pnl_eur": pnl_eur, "pnl_pct": pnl_pct}])
        await _tg_status(
            bot, user,
            (f"✅ Alpaca-{mode}: *{symbol}* verkauft ({q:g} @ ${float(px):.2f}).\n"
             f"Der Trade ist jetzt wirklich geschlossen."),
            parse_mode="Markdown",
        )
        await _reconcile_and_alert(bot, user, client, context="nach Verkauf")
        return {"ok": True, "status": "closed", "filled_qty": q, "filled_avg_price": float(px)}

    final_fail = {"rejected", "canceled", "expired", "done_for_day"}
    if status in final_fail:
        db.mark_broker_close_failed(user["user_id"], trade["ticker"], broker_status=status)
        await _tg_status(
            bot, user,
            f"⚠️ Alpaca-{mode}: Verkauf von *{symbol}* wurde nicht ausgeführt (Status: {status}). Der Trade bleibt aktiv.",
            parse_mode="Markdown",
        )
        await _reconcile_and_alert(bot, user, client, context="nach fehlgeschlagenem Verkauf")
        return {"ok": False, "status": "failed", "broker_status": status}

    db.mark_broker_closing(user["user_id"], trade["ticker"], order_id=order_id, broker_status=status)
    await _tg_status(
        bot, user,
        (f"⏳ Alpaca-{mode}: Verkaufsorder für *{symbol}* angenommen (Status: {status}).\n"
         f"Der Trade bleibt bis zur Fill-Bestätigung offen."),
        parse_mode="Markdown",
    )
    return {"ok": True, "status": "broker_closing", "broker_status": status, "order_id": order_id}


def _suppress_auto_accept_out_of_session(auto_accept: bool, regular_session_open: bool) -> bool:
    """True, wenn ein Auto-Accept-Signal außerhalb der regulären US-Sitzung NICHT als
    Telegram-Karte gesendet werden soll (Anti-Spam). Der Kauf wird ohnehin erst zur
    regulären Öffnung frisch geprüft/ausgeführt (siehe Auto-Accept-Zweig in send_signal)."""
    return auto_accept and not regular_session_open


async def send_signal(bot: Bot, chat_id: int, signal: dict, trade_size_eur: float,
                      job_queue=None, market_open: bool = True,
                      sl_tp_mode: str = DEFAULT_SL_TP_MODE, leverage: float = DEFAULT_LEVERAGE,
                      auto_accept: bool = False, expiry_min: int | None = None) -> bool:
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

    # Auto-Accept: sofort starten, keine Buttons — aber NUR in der regulären US-Sitzung.
    # Außerhalb (Pre-/After-Market) wird nicht automatisch angenommen; das Signal kommt mit
    # JA/NEIN-Buttons, damit dünn gehandelte Randzeiten nicht ungefragt gekauft werden.
    if market_open and auto_accept and _us_market_open(extended=False):
        # Auto-Accept: still & ohne Buttons starten — KEINE Einzelmeldung (Kauf erscheint
        # gebündelt im Tagesreport nach Börsenschluss, siehe close_and_evaluate).
        db.add_pending(chat_id, sig, 0)
        user = db.get_user(chat_id) or {}
        broker_will_execute = bool(user.get("broker_exec") and _alpaca_ready(user) and _alpaca_client(user) is not None)
        result = trade_svc.accept_trade(chat_id, ticker, status="broker_pending" if broker_will_execute else "active")
        if result["ok"]:
            trade = result["trade"]
            await asyncio.to_thread(_attach_demo_option, user, trade)
            await _maybe_broker_order(bot, chat_id, trade)
            log.info(f"[{chat_id}] Auto-Accept Signal gestartet: {ticker} ({leverage:g}×)")
        elif result["reason"] == "entry_cutoff":
            log.info(f"[{chat_id}] Auto-Accept übersprungen (Entry-Sperre kurz vor Handelsschluss): {ticker}")
        return True

    # Auto-Accept-Nutzer bekommen außerhalb der regulären US-Sitzung KEINE Karte in den Chat
    # (Anti-Spam). Der Kauf wird ohnehin erst in der regulären Sitzung geprüft/ausgeführt
    # (siehe Auto-Accept-Zweig oben); der Eröffnungs-/Intraday-Scan bewertet dann frisch neu.
    if _suppress_auto_accept_out_of_session(auto_accept, _us_market_open(extended=False)):
        log.info(f"[{chat_id}] Auto-Accept: {ticker} außerhalb der regulären Sitzung — nicht gesendet "
                 f"(Kaufprüfung erfolgt zur Öffnung).")
        return False

    text, keyboard = _signal_card(sig, trade_size_eur, market_open, expiry_min, user_id=chat_id)
    msg = await bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=keyboard)

    if market_open:
        db.add_pending(chat_id, sig, msg.message_id)
        log.info(f"[{chat_id}] Signal gesendet: {ticker} ({sig['direction']}, {leverage:g}×)")
        # Ablauf-Job NUR planen, wenn der Nutzer das 15-Min-Fenster aktiviert hat (sonst dauerhaft annehmbar)
        if expiry_min and job_queue is not None:
            job_queue.run_once(
                expire_pending_trade,
                when=timedelta(minutes=expiry_min),
                data={"chat_id": chat_id, "ticker": ticker, "message_id": msg.message_id},
                name=f"expire_{chat_id}_{ticker}_{db.today_utc()}"
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
    """Job: Eröffnungs-Scan (Börsenöffnung) — die besten top_n Signale je Nutzer mit Begrüßung."""
    # Zuerst liegengebliebene Pending-Signale vergangener Tage abräumen (Pending-Stau-Prävention).
    cleaned = await asyncio.to_thread(db.expire_stale_pending)
    if cleaned:
        log.info(f"Eröffnung: {cleaned} veraltete Pending-Signale bereinigt.")
    await _run_signal_scan(context, opening=True)


async def scan_intraday(context: ContextTypes.DEFAULT_TYPE):
    """Job: alle 30 Min während der US-Handelszeit — NEUE Signale (über die Eröffnung hinaus)
    leise nachschieben und per Telegram + Website (In-App-Mitteilung) pushen.
    Kein top_n-Deckel; der Duplikat-Schutz verhindert erneutes Senden bereits gemeldeter Aktien."""
    await _run_signal_scan(context, opening=False)


async def _run_signal_scan(context: ContextTypes.DEFAULT_TYPE, opening: bool):
    bot = context.bot
    now = datetime.now(BERLIN_TZ)
    market_open = _us_market_open()
    if not opening and not market_open:
        return  # Intraday-Scan nur während der US-Handelszeit
    log.info(f"{'Eröffnungs' if opening else 'Intraday'}-Scan um {now.strftime('%H:%M')}")

    users = db.list_active_users()
    if not users:
        if opening:
            log.info("Keine registrierten Nutzer — überspringe Tagessignale.")
        return

    # Analyse pro benötigter (Bereich, Voll-Universum, Strategie)-Kombination einmal berechnen
    ranked_by_key: dict[str, list] = {}
    needed = {(r, _auto_uni(u), s)
              for u in users for r in _user_regions(u) for s in _user_strategies(u)}
    for region, auto, strat_key in needed:
        key = _universe_key(region, auto, strat_key)
        tickers = universes.get_tickers(region, auto=auto)
        try:
            # In einen Thread auslagern: analyze_universe ist blockierend (yfinance),
            # sonst friert der Bot während des Scans ein (Ja/Nein-Buttons, 60s-Monitoring).
            ranked = await asyncio.to_thread(
                analyze_universe, tickers, generate=strategies.get(strat_key).generate)
            ranked_by_key[key] = ranked
            _cache_candidates(key, ranked)   # Kandidaten fürs Nachrücken merken
        except Exception as e:
            log.error(f"Analyse fehlgeschlagen ({key}): {e}")
            ranked_by_key[key] = None  # Fehler-Marker

    # Persönliche Watchlists: Vereinigung aller Nutzer einmal je genutzter Strategie analysieren.
    watch_union = sorted({t for u in users for t in _user_watchlist(u)})
    watch_by_strat: dict[str, dict] = {}
    if watch_union:
        for strat_key in {s for u in users for s in _user_strategies(u)}:
            try:
                wl_ranked = await asyncio.to_thread(
                    analyze_universe, watch_union, generate=strategies.get(strat_key).generate)
                watch_by_strat[strat_key] = {s["ticker"]: s for s in wl_ranked}
            except Exception as e:
                log.error(f"Watchlist-Analyse fehlgeschlagen ({strat_key}): {e}")
                watch_by_strat[strat_key] = {}

    for u in users:
        chat_id = u["user_id"]
        regions = _user_regions(u)
        user_strats = _user_strategies(u)
        # top_n gilt nur für den Eröffnungs-Scan; intraday ohne Deckel (Duplikat-Schutz filtert).
        cap = (u.get("top_n_signals") or TOP_N_SIGNALS) if opening else MAX_SIGNALS
        expiry_min = TRADE_ACTIVATION_WINDOW_MIN if u.get("signal_window") else None
        region_label = " + ".join(REGION_LABELS.get(r, r) for r in regions)

        if opening:
            await bot.send_message(
                chat_id=chat_id,
                text=(f"🌅 *Guten Morgen! Tagesanalyse {now.strftime('%d.%m.%Y')}*\n"
                      f"Körbe: {region_label} · {len(user_strats)} Strategie(n) ⏳"),
                parse_mode="Markdown")

        any_sent = False
        watchlist = set(_user_watchlist(u))
        by_strategy: dict[str, list[dict]] = {}
        watch_by_strategy: dict[str, list[dict]] = {}
        for strat_key in user_strats:
            strat = strategies.get(strat_key)
            ranked_lists = [ranked_by_key.get(_universe_key(r, _auto_uni(u), strat_key)) for r in regions]
            region_failed = all(rl is None for rl in ranked_lists)
            ranked = _merge_ranked([rl for rl in ranked_lists if rl is not None])
            signals = smartmoney.rank(ranked, min(len(ranked), MAX_SIGNALS))

            # Watchlist-Treffer dieser Strategie immer zusätzlich anhängen (nie gekürzt).
            extras = []
            if watchlist:
                extra = [sig for tkr, sig in watch_by_strat.get(strat_key, {}).items()
                         if tkr in watchlist]
                if extra:
                    extra = smartmoney.rank(extra, len(extra))   # anreichern, NICHT kürzen
                    for sig in extra:
                        sig["watchlist"] = True
                    extras = extra

            if not signals:
                if opening and region_failed:
                    await bot.send_message(chat_id=chat_id, text=f"⚠️ Analyse-Fehler ({strat.label}).")
            elif _llm_enabled(u):
                signals = await asyncio.to_thread(llm_ranker.rank_signals, signals)
            by_strategy[strat_key] = signals
            watch_by_strategy[strat_key] = extras

        # Der globale Deckel gilt nach dem Rundlauf. Watchlist-Treffer bleiben wie bisher
        # zusätzlich sichtbar, werden aber ebenfalls ohne Score-Quervergleich angeordnet.
        signals = _interleave_strategy_rankings(by_strategy, limit=cap)
        seen = {s["ticker"] for s in signals}
        for extra in _interleave_strategy_rankings(watch_by_strategy):
            if extra["ticker"] not in seen:
                signals.append(extra)
                seen.add(extra["ticker"])

        if opening and signals:
            await bot.send_message(
                chat_id=chat_id,
                text=f"🧠 *Strategie-Rundlauf* — {len(signals)} Signale, Rohscores nicht vergleichbar",
                parse_mode="Markdown")
        for signal in signals:
            if await send_signal(bot, chat_id, signal, u["trade_size_eur"],
                                 job_queue=context.job_queue, market_open=market_open,
                                 sl_tp_mode=u.get("sl_tp_mode", DEFAULT_SL_TP_MODE),
                                 leverage=u.get("leverage", DEFAULT_LEVERAGE),
                                 auto_accept=u.get("auto_accept", False),
                                 expiry_min=expiry_min):
                any_sent = True
            await asyncio.sleep(1.5)  # kurze Pause zwischen Nachrichten

        if opening and not any_sent:
            await bot.send_message(chat_id=chat_id, text="⚠️ Heute keine klaren Signale gefunden.")
        elif any_sent:
            # In-App-Mitteilung für die Website (Telegram-Push ist oben bereits erfolgt)
            title = "📊 Neue Tagessignale" if opening else "📊 Neue Signale"
            notify_svc.notify(chat_id, title, "Neue Signale verfügbar — jetzt ansehen.",
                              type="signal", user=u)
        await asyncio.sleep(0.5)  # kurze Pause zwischen Nutzern (Rate-Limit-Schutz)


def _trade_age_days(trade: dict) -> int:
    """Alter eines Trades in Kalendertagen seit Eröffnung (trade_date).

    `trade_date` wird in UTC geschrieben (`db.today_utc()`) — der Vergleich muss derselben
    Zeitbasis folgen, sonst zählt ein Trade nahe Mitternacht auf einem Server mit
    Zeitzonen-Offset einen Tag zu alt.
    """
    try:
        return (db.today_utc_date() - date.fromisoformat(trade["trade_date"])).days
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

        # Auto-Accept: keine Einzelmeldungen über den Tag — stattdessen EIN gebündelter
        # Tagesreport (gekauft / verkauft / fehlgeschlagene Verkäufe) nach Börsenschluss.
        if u.get("auto_accept"):
            results = []
            if to_close:
                results = evaluate_trades(to_close, u["trade_size_eur"])
                db.close_all(chat_id, results)
                _bsym = {t["ticker"]: reconcile_mod.bot_symbol(t) for t in to_close}
                for r in results:
                    await _maybe_broker_close(bot, u, r["ticker"], broker_symbol=_bsym.get(r["ticker"]))
            await _send_autoaccept_daily_report(bot, u, results)
            await asyncio.sleep(0.5)
            continue

        if not to_close:
            if kept:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"🌙 {len(kept)} Demo-Trade(s) werden über Nacht gehalten (Tagesende-Schließung aus).")
            else:
                await bot.send_message(chat_id=chat_id, text="📭 Heute keine aktiven Demo-Trades zum Auswerten.")
            await asyncio.sleep(0.5)
            continue

        # DATA-002 / Plan.md §10.1 "Reports separat in Europe/Berlin": der Job feuert seit
        # _session_scheduler_tick relativ zum tatsächlichen Handelsschluss (nicht mehr fix
        # CLOSE_TIME_HOUR/MIN) — der Report zeigt daher die echte Berlin-Uhrzeit "jetzt" statt
        # eines ggf. nicht mehr zutreffenden nominalen Anzeigewerts.
        now_berlin = datetime.now(BERLIN_TZ)
        head = (f"⏰ *{now_berlin:%H:%M} Uhr — Schließe alle Demo-Trades und werte aus...*"
                if eod else
                f"⏰ Schließe {len(to_close)} Trade(s) über der Höchsthaltedauer ({HOLD_MAX_DAYS} Tage); "
                f"{len(kept)} bleiben über Nacht offen.")
        await bot.send_message(chat_id=chat_id, text=head, parse_mode="Markdown")

        results = evaluate_trades(to_close, u["trade_size_eur"])
        db.close_all(chat_id, results)
        # echte Alpaca-Positionen (falls vorhanden) mitschließen — bei Options der Kontrakt
        _bsym = {t["ticker"]: reconcile_mod.bot_symbol(t) for t in to_close}
        for r in results:
            await _maybe_broker_close(bot, u, r["ticker"], broker_symbol=_bsym.get(r["ticker"]))

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
        notify_svc.notify(
            chat_id, "📋 Tagesauswertung",
            f"{winners} Gewinner, {losers} Verlierer · Gesamt {'+' if total_pnl >= 0 else ''}{total_pnl:.2f}€",
            type="evaluation", user=u)
        log.info(f"[{chat_id}] Auswertung abgeschlossen. Gesamt P&L: {total_pnl:.2f}€")
        await asyncio.sleep(0.5)


async def _send_autoaccept_daily_report(bot: Bot, user: dict, eod_results: list[dict]):
    """Gebündelter Tagesreport für Auto-Accept-Nutzer: was heute gekauft, verkauft und welche
    Verkäufe nicht funktioniert haben — in EINER Nachricht (alle Einzelmeldungen sind unterdrückt).
    Quelle: heutige Trades + heutiger Status-Event-Log.

    Der Abfragetag muss in **UTC** gebildet werden (`db.today_utc()`): `trade_date` und die
    Event-`ts` sind UTC-gestempelt. Mit `date.today()` lief der Report auf einem Server mit
    Zeitzonen-Offset zwischen Mitternacht und dem Offset einen Tag daneben und meldete
    „Gekauft (0)", obwohl gekauft wurde."""
    uid = user["user_id"]
    today = db.today_utc()
    today_trades = db.get_all_trades_between(uid, today, today)
    events = db.get_trade_events_between(uid, today, today)

    bought = [t for t in today_trades if t.get("entry")]
    eod_tickers = {r["ticker"] for r in eod_results}
    # Verkauft = heute eröffnete & geschlossene (z. B. SL/TP intraday) + die soeben am Tagesende
    # geschlossenen (auch über Nacht gehaltene, deren trade_date evtl. nicht heute ist).
    sold = [(t["ticker"], t.get("entry") or 0.0, t.get("exit") or 0.0, t.get("pnl_eur") or 0.0, t.get("pnl_pct") or 0.0)
            for t in today_trades if t.get("status") == "closed" and t["ticker"] not in eod_tickers]
    sold += [(r["ticker"], r.get("entry") or 0.0, r.get("exit") or 0.0, r.get("pnl_eur") or 0.0, r.get("pnl_pct") or 0.0)
             for r in eod_results]
    failed = [e for e in events if e.get("note") == "close_failed"]
    # OBS-001: heute abgelehnte Käufe sichtbar machen. Ein abgelehnter Kauf ist ein heutiger
    # Trade in Status 'broker_failed' ohne `entry` (nie gefüllt); der `broker_status` trägt den
    # echten Ablehngrund. `entry` gesetzt = fehlgeschlagener VERKAUF → gehört nicht hierher.
    rejected = [t for t in today_trades if t.get("status") == "broker_failed" and not t.get("entry")]

    if not bought and not sold and not failed and not rejected:
        await bot.send_message(chat_id=uid, text="📭 *Tagesreport* — heute keine Auto-Accept-Aktivität.",
                               parse_mode="Markdown")
        return

    lines = ["📋 *Tagesreport (Auto-Accept)*", "━━━━━━━━━━━━━━━━━━"]
    lines.append(f"🟢 *Gekauft ({len(bought)})*")
    for t in bought:
        lev = effective_leverage(t.get("signal", {}))
        lines.append(f"  • {t['ticker']} @ ${(t.get('entry') or 0.0):.2f} ({lev:g}×)")
    if not bought:
        lines.append("  — keine")

    total = sum(pe for _, _, _, pe, _ in sold)
    tsign = "+" if total >= 0 else ""
    lines.append(f"🔴 *Verkauft ({len(sold)})*" + (f" · Gesamt {tsign}{total:.2f}€" if sold else ""))
    for tk, en, ex, pe, pp in sold:
        s = "+" if pe >= 0 else ""
        lines.append(f"  • {tk}: ${en:.2f} → ${ex:.2f} ({s}{pp:.1f}%, {s}{pe:.2f}€)")
    if not sold:
        lines.append("  — keine")

    if rejected:
        lines.append(f"🚫 *Nicht gekauft ({len(rejected)})*")
        for t in rejected:
            lines.append(f"  • {t['ticker']} — {broker_status_label(t.get('broker_status'))}")

    if failed:
        lines.append(f"⚠️ *Verkäufe fehlgeschlagen ({len(failed)})*")
        for e in failed:
            lines.append(f"  • {e['ticker']} ({e.get('broker_status') or 'unbekannt'})")

    await bot.send_message(chat_id=uid, text="\n".join(lines), parse_mode="Markdown")
    notify_svc.notify(
        uid, "📋 Tagesreport",
        f"{len(bought)} gekauft, {len(sold)} verkauft, {len(rejected)} abgelehnt, "
        f"{len(failed)} fehlgeschlagen",
        type="evaluation", user=user)
    log.info(f"[{uid}] Auto-Accept Tagesreport gesendet: {len(bought)} gekauft, {len(sold)} verkauft, "
             f"{len(rejected)} nicht gekauft, {len(failed)} fehlgeschlagen.")


# ── 60s-Monitoring aktiver Trades (Auto-Close) ──────────────────────────────

def _fmt_strength(v) -> str:
    """Kompatiblen Strategie-Rohscore formatieren; '—' wenn unbekannt."""
    return f"{v:.0f}" if v is not None else "—"


# Einmalige „SL/TP aus"-Warnung pro Trade & Tag: (uid, ticker, datum)
_weak_warned: set = set()


async def _maybe_warn_sltp_off(bot: Bot, uid: int, trade: dict, price: float, strength):
    """Bei SL/TP-Modus 'aus': der Trade wird NICHT automatisch geschlossen. Damit man nicht
    blind fährt, gibt es eine *einmalige* Heads-up-Meldung (mit Verkaufen-Button), sobald die
    strategiespezifische Rohscore unter die Schwelle fällt."""
    sig = trade.get("signal", {})
    if sig.get("sl_tp_mode") != "aus":
        return
    if strength is None or strength >= SIGNAL_CLOSE_THRESHOLD:
        return
    key = (uid, trade["ticker"], db.today_utc())
    if key in _weak_warned:
        return
    _weak_warned.add(key)
    entry = trade.get("entry") or price
    strategy_label = _strategy_label(sig)
    await bot.send_message(
        chat_id=uid,
        text=(f"⚠️ *{trade['ticker']}* — Strategie-Rohscore ({strategy_label}) gesunken "
              f"(Einstieg: {_fmt_strength(sig.get('raw_score', sig.get('strength')))} → "
              f"jetzt: {_fmt_strength(strength)}; keine Gewinnwahrscheinlichkeit).\n"
              f"Dein SL/TP-Modus ist *aus* → der Trade wird *nicht* automatisch geschlossen.\n"
              f"Einstieg ${entry:.2f} → aktuell ${price:.2f}. Bei Bedarf manuell schließen:"),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("💰 Verkaufen",
                                   callback_data=_secure_cb(uid, "sell", trade["ticker"],
                                                            ttl_seconds=SELL_CB_TTL_SEC))]]),
    )


def _strategy_exit_reason(trade: dict, price: float | None) -> str | None:
    """Strategie-spezifischer Exit (STRAT-005, W3.6) — NUR aktiv, wenn STRATEGY_EXITS_ENABLED.

    Liefert einen Schließgrund oder None. Ergänzt Liquidation/SL/TP (die maßgeblich bleiben) um
    strategiebezogene Exits (Momentumbruch, Strukturbruch, Timeout, Marktende, Mean-Reversion).
    Fehlende Eingaben (z. B. keine Bars) überspringen die jeweilige Regel; die Funktion bricht nie
    den Monitor (Fehler → None)."""
    try:
        sig = trade.get("signal") or {}
        strategy_key = sig.get("strategy") or "standard"
        # Naive UTC, konsistent mit dem naiven opened_at (reconcile_mod._parse_ts) und dem restlichen
        # Code — sonst crasht `now - opened_at` in evaluate_strategy_exit (naiv vs. aware).
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        opened_at = reconcile_mod._parse_ts(trade.get("created_at"))
        # Bars des kürzesten konfigurierten Timeframes über den Prod-Signalprovider (nie yfinance).
        bars = None
        try:
            tf = analyzer.SIGNAL_TIMEFRAMES[0]
            batch = provider_factory.get_signal_provider().get_bars_batch(
                [trade["ticker"]], interval=tf["interval"], period=tf["period"])
            bars = batch.get(trade["ticker"])
        except Exception:
            bars = None
        decision = exit_policies.evaluate_strategy_exit(
            strategy_key,
            current_price=price,
            entry_price=trade.get("entry"),
            bars=bars,
            opened_at=opened_at,
            now=now,
            minutes_to_close=exchange_calendar.minutes_to_close(now),
        )
        return decision.reason if decision.close else None
    except Exception as e:
        log.warning(f"Strategie-Exit-Prüfung fehlgeschlagen ({trade.get('ticker')}): {e}")
        return None


def evaluate_active_trade(trade: dict, price: float | None, strength: float | None) -> str | None:
    """Entscheidet, ob ein aktiver Trade geschlossen werden soll.
    Gibt den Grund zurück (oder None, wenn er offen bleibt)."""
    sig = trade.get("signal", {})
    sl, tp = sig.get("stop_loss"), sig.get("take_profit")
    leverage = effective_leverage(sig)   # realisierter Hebel (Aktien-Fallback → keine Liquidation)
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
    # TSAFE-005: Das automatische Schließen bei niedrigem Signal-Score (< SIGNAL_CLOSE_THRESHOLD)
    # ist entfernt — ein generischer Score entscheidet nicht mehr über Exits. Es schließen nur noch
    # explizite Regeln: Liquidation, Stop-Loss, Take-Profit (hier) sowie die Höchsthaltedauer/EOD im
    # Tagesjob. Der schwache Score löst weiterhin nur eine Heads-up-Warnung aus
    # (_maybe_warn_sltp_off), schließt aber nicht.
    # W3.6: strategie-spezifische Exits (STRAT-005) — per Default AUS (ändert Live-Trade-Verhalten,
    # Tor T2). Greifen NUR, wenn keine der maßgeblichen Regeln (Liquidation/SL/TP) schon ausgelöst hat
    # und der SL/TP-Modus nicht "aus" ist.
    if STRATEGY_EXITS_ENABLED and not aus:
        return _strategy_exit_reason(trade, price)
    return None


async def _process_queued_order(bot: Bot, user: dict, trade: dict):
    """Behandelt eine vorgemerkte Bruchteil-Order (außerhalb regulärer Zeit aufgelaufen):
    bei regulärem Börsenstart senden, oder nach 24 h als veraltet verfallen lassen."""
    chat_id, ticker = user["user_id"], trade["ticker"]
    queued_at = reconcile_mod._parse_ts(trade.get("broker_updated_at") or trade.get("created_at"))
    age = (datetime.utcnow() - queued_at).total_seconds() if queued_at else 0.0
    if age > BROKER_QUEUE_MAX_AGE_SEC:
        db.mark_broker_failed(chat_id, ticker, broker_status="queue_expired")
        await _tg_status(
            bot, user,
            (f"⌛ Vorgemerkte Order für *{ticker}* ist verfallen (älter als 24 h, Signal veraltet). "
             f"Es wurde nichts gekauft."),
            parse_mode="Markdown")
        return
    if not _us_market_open(extended=False):
        return                                  # noch keine reguläre Sitzung → weiter warten
    await _tg_status(
        bot, user,
        f"🟢 Reguläre US-Sitzung offen — sende jetzt die vorgemerkte Order für *{ticker}*…",
        parse_mode="Markdown")
    await _maybe_broker_order(bot, chat_id, trade)   # plant neu (reguläre Zeit) und sendet


async def monitor_broker_pending(bot: Bot):
    """Pollt offene Broker-Orders und macht Trades erst bei tatsächlichem Fill aktiv."""
    final_fail = {"rejected", "canceled", "expired", "done_for_day"}
    for user in db.list_active_users():
        if not user.get("broker_exec") or not _alpaca_ready(user):
            continue
        client = _alpaca_client(user)
        if client is None:
            continue
        for trade in db.get_broker_pending_trades(user["user_id"]):
            order_id = trade.get("broker_order_id")
            ticker = trade["ticker"]
            if not order_id:
                # Vorgemerkte Order (Bruchteil, außerhalb regulärer Zeit) → bei regulärem Open senden.
                if trade.get("broker_status") == "queued_regular":
                    await _process_queued_order(bot, user, trade)
                continue
            st = await asyncio.to_thread(broker.get_order_status, order_id, client)
            status = st.get("status", "unbekannt")
            ticker = trade["ticker"]
            if status == "filled":
                q = st.get("filled_qty", 0.0)
                px = st.get("filled_avg_price", 0.0)
                db.mark_broker_filled(user["user_id"], ticker, broker_status=status,
                                      filled_qty=q, filled_avg_price=px)
                await _tg_status(
                    bot, user,
                    (f"✅ Broker-Order jetzt ausgeführt: *{ticker}* {q:g} × @ ${px:.2f}.\n"
                     f"Der Trade ist nun aktiv und wird überwacht."),
                    parse_mode="Markdown",
                )
            elif status in final_fail:
                db.mark_broker_failed(user["user_id"], ticker, broker_status=status)
                await _tg_status(
                    bot, user,
                    f"⚠️ Broker-Order für *{ticker}* wurde nicht ausgeführt (Status: {status}).",
                    parse_mode="Markdown",
                )
            else:
                db.mark_broker_pending(user["user_id"], ticker, order_id=order_id, broker_status=status)


def _broker_update_age_sec(updated_at: str | None) -> int:
    parsed = reconcile_mod._parse_ts(updated_at)
    if parsed is None:
        return 0
    return max(0, int((datetime.utcnow() - parsed).total_seconds()))


async def monitor_broker_closing(bot: Bot):
    """Pollt offene Broker-Schließungen und schließt Trades erst bei tatsächlichem Fill.

    Wenn eine Schließungsorder länger als BROKER_REPRICE_AFTER_SEC offen bleibt, wird sie
    storniert und mit einem aggressiveren Limit neu eingestellt (Repricing)."""
    final_fail = {"rejected", "canceled", "expired", "done_for_day"}
    for user in db.list_active_users():
        if not user.get("broker_exec") or not _alpaca_ready(user):
            continue
        client = _alpaca_client(user)
        if client is None:
            continue
        for trade in db.get_broker_closing_trades(user["user_id"]):
            order_id = trade.get("broker_order_id")
            if not order_id:
                continue
            st = await asyncio.to_thread(broker.get_order_status, order_id, client)
            status = st.get("status", "unbekannt")
            ticker = trade["ticker"]
            updated_at = trade.get("broker_updated_at") or ""
            age_sec = 0
            try:
                if updated_at:
                    age_sec = _broker_update_age_sec(updated_at)
            except Exception:
                age_sec = 0

            if status == "filled":
                q = st.get("filled_qty", 0.0)
                px_raw = st.get("filled_avg_price", 0.0) or st.get("avg_fill_price", 0.0) or trade.get("exit") or trade.get("entry") or 0.0
                px = float(px_raw)
                pnl_pct, pnl_eur = trade_pnl(trade, px, user["trade_size_eur"])
                db.close_all(user["user_id"], [{"ticker": ticker, "exit": px,
                                                "pnl_eur": pnl_eur, "pnl_pct": pnl_pct}])
                await _tg_status(
                    bot, user,
                    (f"✅ Broker-Verkauf jetzt ausgeführt: *{ticker}* {q:g} × @ ${px:.2f}.\n"
                     f"Der Trade ist jetzt geschlossen."),
                    parse_mode="Markdown",
                )
                continue

            if status in final_fail:
                db.mark_broker_close_failed(user["user_id"], ticker, broker_status=status)
                await _tg_status(
                    bot, user,
                    f"⚠️ Broker-Verkauf für *{ticker}* wurde nicht ausgeführt (Status: {status}). Der Trade bleibt aktiv.",
                    parse_mode="Markdown",
                )
                continue

            if age_sec >= BROKER_REPRICE_AFTER_SEC:
                cancel_res = await asyncio.to_thread(broker.cancel_order, order_id, client)
                pos = await asyncio.to_thread(broker.get_position, trade["ticker"], client)
                qty = float((pos or {}).get("qty") or trade.get("broker_filled_qty") or 1.0)
                current = get_current_price(ticker, trade.get("entry") or 0.0)
                if current is None:
                    current = float(trade.get("entry") or 0.0)
                side = "BUY" if str(trade.get("direction") or "long").lower() == "short" else "SELL"
                limit_price = _reprice_limit_price(float(current), side, age_sec)
                if not cancel_res.get("ok"):
                    db.mark_broker_closing(user["user_id"], ticker, order_id=order_id, broker_status=status)
                    continue
                resubmit = await asyncio.to_thread(
                    broker.submit_exit_order,
                    trade["ticker"],
                    side=side,
                    qty=qty,
                    limit_price=limit_price,
                    client=client,
                )
                if not resubmit.get("ok"):
                    db.mark_broker_close_failed(user["user_id"], ticker, broker_status=resubmit.get("detail") or "repriced_submit_failed")
                    await _tg_status(
                        bot, user,
                        (f"⚠️ Broker-Verkauf für *{ticker}* wurde neu bepreist, aber der neue Auftrag konnte nicht gesendet werden."),
                        parse_mode="Markdown",
                    )
                    continue
                new_order_id = resubmit.get("id", order_id)
                db.mark_broker_closing(user["user_id"], ticker, order_id=new_order_id, broker_status="repriced")
                await _tg_status(
                    bot, user,
                    (f"⏳ Broker-Verkauf für *{ticker}* lief länger als 5 Min.\n"
                     f"Alte Order storniert und neu eingestellt (Limit ${limit_price:.2f})."),
                    parse_mode="Markdown",
                )
                continue

            db.mark_broker_closing(user["user_id"], ticker, order_id=order_id, broker_status=status)


async def monitor_missing_broker_positions(bot: Bot, *, full: bool = False):
    """Schließt aktive Bot-Trades, deren Broker-Position verschwunden ist.

    Das fängt den Fall ab, dass ein User die Position manuell im Broker verkauft hat.
    Nach einer kurzen Grace-Phase wird der Trade automatisch als geschlossen markiert.
    """
    for user in db.list_active_users():
        if not _broker_reconcile_enabled(user, full=full):
            continue
        client = _alpaca_client(user)
        if client is None:
            continue
        result = await asyncio.to_thread(
            reconcile_mod.sweep_missing_positions,
            user,
            client,
            grace_sec=BROKER_POSITION_MISSING_AFTER_SEC,
        )
        if not result.get("closed"):
            continue
        for closed in result["closed"]:
            ticker = closed["ticker"]
            exit_price = float(closed.get("exit") or 0.0)
            await _tg_status(
                bot, user,
                (f"🔁 *{ticker}* wurde bei Alpaca nicht mehr gefunden und daher als verkauft markiert.\n"
                 f"Abgleich mit aktuellem Kurs: ${exit_price:.2f}."),
                parse_mode="Markdown",
            )
        await refill_pending(bot, user["user_id"], user, None)


async def monitor_orphan_broker_positions(bot: Bot, *, full: bool = False):
    """Übernimmt Broker-Positionen, die der Bot NICHT führt, als aktive Trades.

    Schließt die Lücke „im Broker offen, aber nicht im Bot" (z. B. Order ging beim Broker
    durch, der Bot verbuchte sie wegen eines Sende-/Antwortfehlers aber als fehlgeschlagen).
    Danach überwacht der Bot die Position wieder (SL/TP, Tagesende-Auswertung).
    """
    for user in db.list_active_users():
        if not _broker_reconcile_enabled(user, full=full):
            continue
        client = _alpaca_client(user)
        if client is None:
            continue
        result = await asyncio.to_thread(reconcile_mod.adopt_orphan_positions, user, client)
        for a in result.get("adopted", []):
            entry = float(a.get("entry") or 0.0)
            qty = a.get("qty")
            await _tg_status(
                bot, user,
                (f"♻️ *{a['ticker']}* war bei Alpaca offen, aber nicht im Bot — "
                 f"jetzt automatisch übernommen ({a['symbol']}"
                 f"{f', {qty:g}×' if qty is not None else ''} @ ${entry:.2f}).\n"
                 f"Der Trade wird wieder überwacht. SL/TP sind aus (nachträglich erkannt) — "
                 f"bei Bedarf manuell verkaufen."),
                parse_mode="Markdown",
            )
        # Frühere Fehlübernahmen heilen: Options-Trades, deren Einstieg fälschlich die Prämie war.
        healed = await asyncio.to_thread(reconcile_mod.heal_adopted_option_entries, user)
        if healed:
            await _tg_status(
                bot, user,
                ("🔧 Einstieg korrigiert für übernommene Options-Trades: "
                 f"*{', '.join(healed)}*.\nBei diesen war die Options-Prämie fälschlich als "
                 f"Aktienkurs hinterlegt (absurde Prozent-/€-Anzeige). Jetzt auf den echten "
                 f"Aktienkurs gesetzt."),
                parse_mode="Markdown",
            )


async def monitor_trades(context: ContextTypes.DEFAULT_TYPE):
    """Job (alle 60s): Broker-Pending pollt, aktive Trades prüft, Verlauf aufzeichnet,
    bei SL/TP oder Signal-Verfall automatisch schließt."""
    await monitor_broker_pending(context.bot)
    await monitor_broker_closing(context.bot)
    await monitor_missing_broker_positions(context.bot)
    await monitor_orphan_broker_positions(context.bot)
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
                # (bei Auto-Accept unterdrückt — dann nur der Tagesreport).
                if not user.get("auto_accept"):
                    await _maybe_warn_sltp_off(context.bot, uid, trade, price, strength)
                continue

            entry = trade.get("entry") or price
            leverage = effective_leverage(trade.get("signal", {}))
            is_option = bool(trade.get("signal", {}).get("option_symbol"))
            # Bei Liquidation zum Liquidationskurs schließen (Totalverlust), sonst zum aktuellen Kurs.
            # Long-Optionen werden nicht liquidiert (Verlust ist auf die Prämie begrenzt).
            exit_price = price
            if reason.startswith("Liquidation") and not is_option:
                liq = liquidation_price(entry, leverage, trade["direction"])
                if liq is not None:
                    exit_price = liq
            pnl_pct, pnl_eur = trade_pnl(trade, exit_price, user["trade_size_eur"])
            db.close_all(uid, [{"ticker": trade["ticker"], "exit": exit_price,
                                "pnl_eur": pnl_eur, "pnl_pct": pnl_pct}])

            sign = "+" if pnl_eur >= 0 else ""
            emoji = "🟢" if pnl_eur > 0 else ("🔴" if pnl_eur < 0 else "⚪")
            await _tg_status(
                context.bot, user,
                (f"{emoji} *{trade['ticker']} automatisch geschlossen* — {reason}\n"
                 f"Einstieg ${entry:.2f} → Ausstieg ${exit_price:.2f} (Hebel {leverage:g}×)\n"
                 f"Strategie-Rohscore ({_strategy_label(trade.get('signal', {}))}): "
                 f"Einstieg {_fmt_strength(trade.get('signal', {}).get('raw_score', trade.get('signal', {}).get('strength')))} → "
                 f"Ausstieg {_fmt_strength(strength)}  ·  "
                 f"Realisiert: {sign}{pnl_pct:.1f}% ({sign}{pnl_eur:.2f}€)"),
                parse_mode="Markdown",
            )
            # echte Alpaca-Position (falls vorhanden) mitschließen — bei Options der Kontrakt
            await _maybe_broker_close(context.bot, user, trade["ticker"],
                                      broker_symbol=reconcile_mod.bot_symbol(trade))
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


async def run_shadow_signals(context: ContextTypes.DEFAULT_TYPE):
    """Job: erzeugt Schatten-Signale aus der produktiven Analyse und persistiert Shadow-Snapshots
    (W3.5, RES-002). Läuft nur während der regulären US-Handelszeit; die blockierende Analyse
    (Alpaca-Signalprovider) wird in einen Thread ausgelagert und bricht nie den Bot."""
    if not _us_market_open(extended=False):
        return
    stored = await asyncio.to_thread(shadow_scheduler.generate_and_record)
    if stored:
        log.info(f"Shadow-Zyklus: {stored} Schatten-Signale persistiert.")


# ── Manuelle Befehle (für registrierte Nutzer jederzeit verfügbar) ──────────

def _registered_user(chat_id: int) -> dict | None:
    """Gibt das Profil zurück, falls der Nutzer fertig eingerichtet ist, sonst None."""
    user = db.get_user(chat_id)
    if not user or user["onboarding_state"] != "complete":
        return None
    return user


def _us_market_open(extended: bool | None = None) -> bool:
    """Prüft, ob die US-Börse gerade „offen" ist — Feiertage und Frühschluss-Tage werden über
    den NYSE/Nasdaq-Kalender (`exchange_calendar`, DATA-001/DATA-002) berücksichtigt, nicht nur
    Wochentag + festes ET-Zeitfenster.
    Regulär: tatsächliche Sitzungszeit des Handelstags (z. B. verkürzt an Frühschluss-Tagen).
    Extended Hours (Pre-/After-Market): 4:00–20:00 ET, aber nur an echten Handelstagen."""
    if extended is None:
        extended = EXTENDED_HOURS
    now = datetime.now(ZoneInfo("America/New_York"))
    if not exchange_calendar.is_trading_day(now.date()):
        return False
    if extended:
        minutes = now.hour * 60 + now.minute
        return 4 * 60 <= minutes <= 20 * 60
    open_dt = exchange_calendar.market_open(now.date())
    close_dt = exchange_calendar.market_close(now.date())
    if open_dt is None or close_dt is None:
        return False
    now_et = now if now.tzinfo else now.replace(tzinfo=ZoneInfo("America/New_York"))
    return open_dt.astimezone(ZoneInfo("America/New_York")) <= now_et <= close_dt.astimezone(ZoneInfo("America/New_York"))


async def refill_pending(bot: Bot, chat_id: int, user: dict, job_queue):
    """Füllt nach einer Entscheidung global bis ``top_n`` aus dem Strategie-Rundlauf nach.

    Die Kandidaten bleiben je Strategie intern gerankt; zwischen Strategien findet kein
    Rohscore-Vergleich statt. Bei Auto-Accept oder geschlossener Börse passiert nichts.
    """
    if user.get("auto_accept") or not _us_market_open():
        return
    regions = _user_regions(user)
    top_n = user.get("top_n_signals") or TOP_N_SIGNALS
    need = top_n - len(db.get_pending_trades(chat_id))
    if need <= 0:
        return

    rankings = {
        strat_key: _merge_ranked([
            _get_candidates(_universe_key(r, _auto_uni(user), strat_key)) for r in regions
        ])
        for strat_key in _user_strategies(user)
    }
    for signal in _interleave_strategy_rankings(rankings):
        if need <= 0:
            break
        if db.has_open_position(chat_id, signal["ticker"]):
            continue
        sent = await send_signal(bot, chat_id, signal, user["trade_size_eur"],
                                 job_queue=job_queue, market_open=True,
                                 sl_tp_mode=user.get("sl_tp_mode", DEFAULT_SL_TP_MODE),
                                 leverage=user.get("leverage", DEFAULT_LEVERAGE),
                                 auto_accept=False,
                                 expiry_min=TRADE_ACTIVATION_WINDOW_MIN if user.get("signal_window") else None)
        if sent:
            need -= 1


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/ping — prüft, ob der Bot dir Nachrichten senden kann."""
    chat_id = update.effective_chat.id
    if not _registered_user(chat_id):
        await update.message.reply_text("⚠️ Du bist noch nicht eingerichtet. Sende zuerst /start.")
        return
    await update.message.reply_text("🧪 Pong — Telegram-Verbindung funktioniert!")


async def cmd_killswitch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: globalen Kill-Switch anzeigen, aktivieren oder deaktivieren."""
    chat_id = update.effective_chat.id
    if ADMIN_CHAT_ID is None or chat_id != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ Nur der Admin darf den globalen Kill-Switch schalten.")
        return
    args = list(context.args or [])
    action = args[0].lower() if args else "status"
    actor = f"admin:{chat_id}"
    if action in {"on", "an"}:
        reason = " ".join(args[1:]).strip() or "Manuell durch Admin aktiviert"
        kill_switch_service.activate_global(reason=reason, activated_by=actor)
    elif action in {"off", "aus"}:
        kill_switch_service.deactivate_global(deactivated_by=actor)
    elif action != "status":
        await update.message.reply_text("Nutzung: /killswitch [status|on GRUND|off]")
        return
    status = kill_switch_service.global_status
    if status is not None and status.active:
        text = f"🛑 Globaler Kill-Switch: AN\nGrund: {status.reason}"
    else:
        text = "✅ Globaler Kill-Switch: AUS"
    await update.message.reply_text(text)


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

    # Jede Strategie rankt intern; erst danach folgt ein global gedeckelter Rundlauf.
    total_sent = 0
    by_strategy: dict[str, list[dict]] = {}
    for strat_key in user_strats:
        strat = strategies.get(strat_key)
        try:
            per_region = []
            for r in regions:
                rl = analyze_universe(tickers_by_region[r], generate=strat.generate)
                _cache_candidates(_universe_key(r, auto, strat.key), rl)   # Kandidaten fürs Nachrücken (je Korb)
                per_region.append(rl)
            ranked = _merge_ranked(per_region)
            signals = smartmoney.rank(ranked, min(len(ranked), MAX_SIGNALS))
            if _llm_enabled(user):
                signals = await asyncio.to_thread(llm_ranker.rank_signals, signals)
        except Exception as e:
            await update.message.reply_text(f"⚠️ Analyse-Fehler ({strat.label}): {e}")
            continue
        if not signals:
            await update.message.reply_text(f"🧠 *{strat.label}*: heute keine klaren Signale.",
                                            parse_mode="Markdown")
            continue
        by_strategy[strat_key] = signals

    signals = _interleave_strategy_rankings(by_strategy, limit=top_n)
    if signals:
        as_of = signals[0].get("as_of", "?")
        await update.message.reply_text(
            f"🧠 *Strategie-Rundlauf* — {len(signals)} Signale (Datenstand: {as_of}); "
            "Rohscores werden nicht verglichen.",
            parse_mode="Markdown")
    for signal in signals:
        if await send_signal(bot, chat_id, signal, user["trade_size_eur"],
                             job_queue=context.job_queue, market_open=market_open,
                             sl_tp_mode=user.get("sl_tp_mode", DEFAULT_SL_TP_MODE),
                             leverage=user.get("leverage", DEFAULT_LEVERAGE),
                             auto_accept=user.get("auto_accept", False),
                             expiry_min=TRADE_ACTIVATION_WINDOW_MIN if user.get("signal_window") else None):
            total_sent += 1
        await asyncio.sleep(1)

    if market_open and total_sent == 0:
        await update.message.reply_text(
            "ℹ️ Alle heutigen Signale hattest du bereits — pro Aktie gibt es nur ein Signal pro Tag."
        )


def _unrealized_pnl(trade: dict, trade_size_eur: float):
    """Aktuellen (unrealisierten) Stand eines aktiven Trades berechnen — echte Kurse, optionsbewusst.
    Bei Options-Trades wird über die Omega-Näherung gerechnet (kein Live-Prämien-Abruf im Monitor)."""
    entry = trade["entry"]
    current = get_current_price(trade["ticker"], entry)
    pnl_pct, pnl_eur = trade_pnl(trade, current, trade_size_eur)
    return current, pnl_pct, pnl_eur


def _trade_card(trade: dict, trade_size_eur: float, current_strength=None,
                user_id: int | None = None):
    """Baut Nachrichtentext + Verkaufen-Button für einen aktiven Demo-Trade.
    `current_strength` = aktueller Strategie-Rohscore (z. B. letzter 60s-Tick); None → unbekannt."""
    ticker = trade["ticker"]
    leverage = effective_leverage(trade.get("signal", {}))
    signal = trade.get("signal", {})
    entry_strength = signal.get("raw_score", signal.get("strength"))
    strategy_label = _strategy_label(signal)
    current, pnl_pct, pnl_eur = _unrealized_pnl(trade, trade_size_eur)
    emoji = "🟢" if pnl_eur > 0 else ("🔴" if pnl_eur < 0 else "⚪")
    sign = "+" if pnl_eur >= 0 else ""
    text = (
        f"📊 *{ticker}* — aktiver Demo-Trade ({trade['direction'].upper()}, {leverage:g}×)\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 Einstieg: ${trade['entry']:.2f}\n"
        f"📈 Aktuell: ${current:.2f}\n"
        f"📶 Strategie-Rohscore ({strategy_label}): Einstieg {_fmt_strength(entry_strength)} → "
        f"jetzt {_fmt_strength(current_strength)} (keine Gewinnwahrscheinlichkeit)\n"
        f"{emoji} Unrealisiert: {sign}{pnl_pct:.1f}% ({sign}{pnl_eur:.2f}€)"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Verkaufen",
                              callback_data=_secure_cb(user_id, "sell", ticker,
                                                       ttl_seconds=SELL_CB_TTL_SEC))]
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
        text, keyboard = _trade_card(trade, user["trade_size_eur"], current_strength=cur_strength,
                                     user_id=chat_id)
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
        f"📋 Watchlist: *{len(_user_watchlist(user))}* Symbol(e) — /watchlist\n"
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
    eod = user.get("eod_close", DEFAULT_EOD_CLOSE)
    broker_on = user.get("broker_exec")
    broker_ready = _alpaca_ready(user)
    broker_mode = "PAPER" if ALPACA_PAPER else "LIVE"

    text = (
        "⚙️ *Einstellungen* — zum Ändern unten antippen\n"
        f"💶 Trade-Größe *{size:.0f}€*  ·  ⚡ Hebel *{lev:g}×*\n"
        f"🌍 Körbe *{' + '.join(REGION_LABELS.get(k, k) for k in region_keys)}*\n"
        f"🔢 Signale/Tag *{top_n}*  ·  🎯 SL/TP *{mode}*\n"
        f"🧠 Strategien *{', '.join(strategies.get(k).label for k in strat_keys)}*\n"
        "\n_Schalter: ✅ an/aktiv · ▫️ aus_"
    )

    # Umschalt-Button: zeigt den aktuellen Zustand, ein Tipp kippt ihn (kompakter als an/aus-Paare)
    def _toggle(label, on, action):
        return InlineKeyboardButton(("✅ " if on else "▫️ ") + label,
                                    callback_data=f"{action}:{0 if on else 1}")

    size_row = [InlineKeyboardButton(("✅ " if float(v) == float(size) else "") + f"{v:g}€",
                                     callback_data=f"set_size:{v}")
                for v in TRADE_SIZE_CHOICES]
    region_row = [InlineKeyboardButton(("✅ " if k in region_keys else "") + lbl, callback_data=f"set_region:{k}")
                  for k, lbl in REGION_LABELS.items()]
    count_row = [InlineKeyboardButton(("✅ " if n == top_n else "") + str(n), callback_data=f"set_count:{n}")
                 for n in SIGNAL_COUNT_CHOICES]
    mode_row = [InlineKeyboardButton(("✅ " if k == mode else "") + k, callback_data=f"set_mode:{k}")
                for k in SL_TP_MODES]
    # Strategien: 2 pro Reihe (statt alle in einer überfüllten Zeile)
    strat_btns = [InlineKeyboardButton(("✅ " if s.key in strat_keys else "") + s.label,
                                       callback_data=f"set_strat:{s.key}")
                  for s in strategies.production_strategies()]
    strat_rows = [strat_btns[i:i + 2] for i in range(0, len(strat_btns), 2)]
    # Boolesche Schalter gesammelt, je 2 pro Reihe
    toggles = [_toggle("Auto-Accept", auto, "set_auto"),
               _toggle("Voll-Universum", auto_uni, "set_uni"),
               _toggle("Tagesende-Schließung", eod, "set_eod"),
               _toggle("15-Min-Fenster", user.get("signal_window"), "set_window")]
    if LLM_RANK_ENABLED:
        toggles.append(_toggle("KI-Ranking", llm_on, "set_llm"))
    if broker_ready:
        toggles.append(_toggle(f"Broker-Order ({broker_mode})", broker_on, "set_broker"))
    toggle_rows = [toggles[i:i + 2] for i in range(0, len(toggles), 2)]

    rows = [size_row, region_row, count_row, mode_row] + strat_rows + toggle_rows
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
    lines = ["🧠 *Produktive Strategien* (mehrere gleichzeitig möglich)", ""]
    for s in strategies.production_strategies():
        mark = "✅" if s.key in cur else "▫️"
        lines.append(f"{mark} *{s.label}*  (`{s.key}`)\n   {s.description}")
    legacy = [strategies.get(key) for key in cur if not strategies.get(key).production]
    if legacy:
        lines.append("\n🔬 *Bestehende Research-only-Auswahl* (bleibt aktiv):")
        lines.extend(f"✅ *{s.label}*  (`{s.key}`)" for s in legacy)
    lines.append("\nHinzufügen/Entfernen: `/addstrat <name>` oder über /settings.\nKennzahlen: /teststrat")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_addstrat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/addstrat <name> — fügt eine Strategie zur Auswahl hinzu (bzw. entfernt sie, wenn schon aktiv)."""
    chat_id = update.effective_chat.id
    user = _registered_user(chat_id)
    if not user:
        await update.message.reply_text("⚠️ Du bist noch nicht eingerichtet. Sende zuerst /start.")
        return
    keys = ", ".join(f"`{s.key}`" for s in strategies.production_strategies())
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
    if not settings_svc.toggle_strategy_selection(chat_id, key):
        await update.message.reply_text(
            f"⚠️ `{key}` ist Research-only und kann nicht neu aktiviert werden.\n"
            f"Produktiv verfügbar: {keys}", parse_mode="Markdown")
        return
    active = _user_strategies(db.get_user(chat_id) or {})
    log.info(f"[{chat_id}] Strategie-Auswahl geändert: {key} → {active}")
    labels = ", ".join(strategies.get(k).label for k in active)
    await update.message.reply_text(
        f"✅ Aktive Strategien: *{labels}*\nKennzahlen mit /teststrat.",
        parse_mode="Markdown")


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/watchlist — zeigt die persönliche Watchlist (immer zusätzlich analysierte Symbole)."""
    chat_id = update.effective_chat.id
    user = _registered_user(chat_id)
    if not user:
        await update.message.reply_text("⚠️ Du bist noch nicht eingerichtet. Sende zuerst /start.")
        return
    wl = _user_watchlist(user)
    body = "\n".join(f"  • `{t}`" for t in wl) if wl else "_(leer)_"
    await update.message.reply_text(
        f"📋 *Deine Watchlist* ({len(wl)})\n{body}\n\n"
        "Diese Symbole laufen täglich zusätzlich durch deine Strategie und werden — wenn sie "
        "auslösen — *immer* gesendet (nie vom Tageslimit weggeschnitten).\n\n"
        "Hinzufügen: `/watchadd AAPL` (auch ETFs, z. B. `SPY`)\n"
        "Entfernen: `/watchdel AAPL`",
        parse_mode="Markdown")


async def cmd_watchadd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/watchadd <symbol …> — prüft Symbol(e) und fügt sie der Watchlist hinzu.
    Unbekannte Eingaben → 'Meinten Sie?' (yfinance-Suche, sonst Claude Haiku)."""
    chat_id = update.effective_chat.id
    user = _registered_user(chat_id)
    if not user:
        await update.message.reply_text("⚠️ Du bist noch nicht eingerichtet. Sende zuerst /start.")
        return
    raw = [a.strip().upper() for a in " ".join(context.args).replace(",", " ").split() if a.strip()]
    if not raw:
        await update.message.reply_text(
            "Nutzung: `/watchadd AAPL` (mehrere möglich: `/watchadd AAPL MSFT`).\n"
            "Funktioniert auch für ETFs (z. B. `SPY`). Übersicht: /watchlist",
            parse_mode="Markdown")
        return

    alpaca_client = _alpaca_client(user) if _alpaca_ready(user) else None
    for sym in raw:
        res = await asyncio.to_thread(watchlist_svc.add_to_watchlist, chat_id, sym, alpaca_client)
        if res["status"] == "added":
            info, wl, asset = res["info"], res["watchlist"], res.get("asset")
            typ = "ETF" if info.get("quote_type") == "ETF" else "Aktie"
            # Firmennamen gegen Markdown-Sonderzeichen absichern (sonst "can't parse entities")
            safe_name = "".join(c for c in str(info.get("name", info["symbol"])) if c not in "_*`[]")
            line = f"✅ *{info['symbol']}* ({safe_name}, {typ}) — Kurs ${info['price']:.2f}"
            if asset is not None and asset.get("ok"):
                line += "\n   📈 Bei Alpaca handelbar ✓" if asset.get("tradable") else \
                        "\n   ⚠️ Bei Alpaca *nicht* handelbar — wird nur im Demo-Modus berücksichtigt."
            line += f"\n   📋 Watchlist ({len(wl)}): {', '.join(wl)}"
            await update.message.reply_text(line, parse_mode="Markdown")
            continue

        # Nicht gefunden → "Meinten Sie?" (Vorschläge kommen aus dem Service: yfinance, dann LLM)
        suggestions = res["suggestions"]
        if suggestions:
            await update.message.reply_text(
                f"❓ *{res['symbol']}* nicht gefunden. Meinten Sie: "
                + ", ".join(f"`{s}`" for s in suggestions)
                + "?\nHinzufügen z. B. mit `/watchadd " + suggestions[0] + "`.",
                parse_mode="Markdown")
        else:
            await update.message.reply_text(
                f"❌ *{res['symbol']}* nicht gefunden und keine Vorschläge verfügbar. "
                "Prüfe das Börsenkürzel (z. B. `AAPL`).", parse_mode="Markdown")


async def cmd_watchdel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/watchdel <symbol> — entfernt ein Symbol aus der Watchlist."""
    chat_id = update.effective_chat.id
    user = _registered_user(chat_id)
    if not user:
        await update.message.reply_text("⚠️ Du bist noch nicht eingerichtet. Sende zuerst /start.")
        return
    if not context.args:
        await update.message.reply_text("Nutzung: `/watchdel AAPL`. Übersicht: /watchlist",
                                        parse_mode="Markdown")
        return
    sym = context.args[0].strip().upper()
    wl = watchlist_svc.remove_from_watchlist(chat_id, sym)
    body = ", ".join(wl) if wl else "_(leer)_"
    await update.message.reply_text(f"🗑️ *{sym}* entfernt.\n📋 Watchlist ({len(wl)}): {body}",
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
    "Stimmen sie überein, entsteht ein Long-Signal mit einem Rohscore der gewählten Strategie.\n"
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
    "⭐ *Strategie-Rohscore*: strategiespezifische Ranghilfe; Werte verschiedener Strategien "
    "sind nicht vergleichbar und keine Gewinnwahrscheinlichkeit. Aktive Trades werden alle 60s "
    "neu bewertet; automatisch geschlossen wird nur nach den konfigurierten Risiko-/Exitregeln.\n"
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


async def cmd_website(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/website — sendet den persönlichen Ein-Klick-Login-Link zur Web-App."""
    chat_id = update.effective_chat.id
    if not _registered_user(chat_id):
        await update.message.reply_text("⚠️ Du bist noch nicht eingerichtet. Sende zuerst /start.")
        return

    token = db.get_or_create_dashboard_token(chat_id)
    base = DASHBOARD_BASE_URL.rstrip("/")
    url = f"{base}/auth/token?token={token}"
    # Kein Markdown: der Token enthält oft _ und -, was Markdown-Parsing bricht.
    await update.message.reply_text(
        "🌐 Deine Web-App (läuft parallel zu Telegram — gleicher Account)\n"
        f"{url}\n\n"
        "Damit bist du direkt eingeloggt: Signale annehmen/ablehnen, Einstellungen, Watchlist, "
        "Mitteilungen & Dashboard.\n"
        "🔒 Der Link ist privat — teile ihn nicht.",
        disable_web_page_preview=True,
    )


HELP_TEXT = (
    "🤖 *Verfügbare Befehle*\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "/start — Setup starten oder Status anzeigen\n"
    "/profile — Dein Profil ansehen (Trade-Größe, Markt, Broker, Status)\n"
    "/settings — Körbe, Trade-Größe & Anzahl Signale ändern\n"
    "/tradesize <betrag> — Demo-Trade-Größe in € setzen (z. B. /tradesize 250)\n"
    "/website — Ein-Klick-Login zur Web-App (Signale, Einstellungen, Watchlist)\n"
    "/dashboard — Link zu deinem Web-Dashboard\n"
    "/signals — Aktuelle Signale jetzt live abrufen\n"
    "/top5trade — Was große Trader (Insider + Institutionen) zuletzt gekauft haben\n"
    "/evaluate — Deine aktiven Demo-Trades jetzt auswerten\n"
    "/strategies — Verfügbare Signal-Strategien anzeigen\n"
    "/addstrat <name> — Strategie per Namen wählen (z. B. `adx_trend`)\n"
    "/watchlist — Persönliche Watchlist anzeigen (immer zusätzlich analysiert)\n"
    "/watchadd <symbol> — Aktie/ETF zur Watchlist hinzufügen (z. B. `AAPL`, `SPY`)\n"
    "/watchdel <symbol> — Symbol aus der Watchlist entfernen\n"
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
    f"  • {SIGNAL_TIME_HOUR:02d}:{SIGNAL_TIME_MIN:02d} Uhr — Eröffnungs-Signale (Top-N) mit JA/NEIN-Buttons\n"
    "  • alle 30 Min während der Handelszeit — neue Signale werden automatisch nachgeschoben\n"
    f"  • {CLOSE_TIME_HOUR:02d}:{CLOSE_TIME_MIN:02d} Uhr — automatische Auswertung aller Demo-Trades\n"
    "  • ✅ Signale bleiben den ganzen Handelstag annehmbar (optionales 15-Min-Fenster in /settings)"
)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/help — listet alle verfügbaren Befehle und den täglichen Ablauf auf."""
    await update.message.reply_text(
        HELP_TEXT, parse_mode="Markdown", reply_markup=menu.main_menu_keyboard()
    )


# ── Hauptmenü (persistente Buttons an der Chat-Leiste) ──────────────────────

MENU_BUTTON_HANDLERS = {
    menu.BTN_SIGNALS: cmd_signals,
    menu.BTN_EVALUATE: cmd_evaluate,
    menu.BTN_DASHBOARD: cmd_dashboard,
    menu.BTN_SETTINGS: cmd_settings,
    menu.BTN_PROFILE: cmd_profile,
    menu.BTN_HELP: cmd_help,
}


async def menu_button_dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mappt die Reply-Keyboard-Buttons auf die bestehenden Befehls-Handler.
    Unbekannte Texte werden ignoriert (kein Echo-Spam bei freiem Text)."""
    text = (update.message.text or "").strip() if update.message else ""
    handler = MENU_BUTTON_HANDLERS.get(text)
    if handler is not None:
        await handler(update, context)


# ── Button-Handler ──────────────────────────────────────────────────────────

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verarbeitet JA/NEIN/Verkaufen-Button-Klicks. Der klickende Nutzer wird über die Chat-ID aufgelöst."""
    query = update.callback_query
    chat_id = update.effective_chat.id  # == user_id, im privaten Chat eindeutig

    data = query.data or ""
    if data.startswith("t:"):
        # W7: opaque Callback-Tokens — serverseitig aufgelöst, nutzergebunden, einmalig.
        try:
            action, payload = callback_security.resolve(data[2:], chat_id)
        except callback_security.CallbackSecurityError as e:
            hints = {
                "used": "⚠️ Schon verarbeitet (Doppelklick).",
                "expired": "⏳ Button abgelaufen — fordere die Ansicht neu an (z. B. /signals oder /evaluate).",
                "wrong_user": "⚠️ Dieser Button gehört nicht zu deinem Konto.",
            }
            await query.answer(hints.get(e.reason, "⚠️ Ungültiger Button."), show_alert=True)
            return
        parts = [action, payload.get("ticker", "")]
        ticker = parts[1]
    else:
        # Legacy-Format (Karten von vor der Token-Umstellung + nicht-tokenisierte Buttons)
        parts = data.split(":")
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
        updated = trade_svc.set_pending_leverage(chat_id, ticker, value)
        if updated is None:
            await query.answer("⚠️ Hebel nicht mehr änderbar (Trade bereits bearbeitet).", show_alert=True)
            return
        user = db.get_user(chat_id)
        text, keyboard = _signal_card(updated["signal"], user["trade_size_eur"], market_open=True,
                                      user_id=chat_id)
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
        except Exception:
            pass
        log.info(f"[{chat_id}] Hebel geändert: {ticker} → {value:g}×")
        return

    if action == "accept":
        user = db.get_user(chat_id) or {}
        broker_will_execute = bool(user.get("broker_exec") and _alpaca_ready(user) and _alpaca_client(user) is not None)
        result = trade_svc.accept_trade(chat_id, ticker, status="broker_pending" if broker_will_execute else "active")
        if result["ok"]:
            trade = result["trade"]
            lev = trade.get("signal", {}).get("leverage", 1.0) or 1.0
            await query.edit_message_reply_markup(reply_markup=None)
            if broker_will_execute:
                await query.edit_message_text(
                    query.message.text + f"\n\n⏳ *Broker-Order wird gesendet…*\nDemo-Trade wird erst nach tatsächlicher Ausführung aktiv. Einstieg-Referenz: ${trade['entry']:.2f} · Hebel {lev:g}×",
                    parse_mode="Markdown"
                )
            else:
                await query.edit_message_text(
                    query.message.text + f"\n\n✅ *Demo-Trade gestartet!*\nEinstiegskurs: ${trade['entry']:.2f} · Hebel {lev:g}×",
                    parse_mode="Markdown"
                )
            log.info(f"[{chat_id}] Trade aktiviert: {ticker} @ ${trade['entry']:.2f} ({lev:g}×, {trade['status']})")
            await asyncio.to_thread(_attach_demo_option, user, trade)
            await _maybe_broker_order(context.bot, chat_id, trade)
        elif result["reason"] == "expired":
            await query.answer(
                f"⏰ Zeitfenster abgelaufen — Start ist nur innerhalb von "
                f"{TRADE_ACTIVATION_WINDOW_MIN} Minuten möglich.",
                show_alert=True
            )
        elif result["reason"] == "entry_cutoff":
            await query.answer(
                f"🔒 Kein neuer Einstieg mehr — weniger als {ENTRY_CUTOFF_BEFORE_CLOSE_MIN} Minuten "
                f"bis Handelsschluss.",
                show_alert=True
            )
        else:
            await query.answer("⚠️ Trade bereits aktiv oder nicht gefunden.", show_alert=True)

    elif action == "reject":
        if trade_svc.reject_trade(chat_id, ticker):
            await query.edit_message_reply_markup(reply_markup=None)
            await query.edit_message_text(
                query.message.text + "\n\n❌ *Abgelehnt*",
                parse_mode="Markdown"
            )
            log.info(f"[{chat_id}] Trade abgelehnt: {ticker}")
        else:
            await query.answer("⚠️ Trade bereits bearbeitet oder nicht gefunden.", show_alert=True)

    elif action == "sell":
        user = db.get_user(chat_id) or {}
        if user.get("broker_exec") and _alpaca_ready(user) and _alpaca_client(user) is not None:
            result = trade_svc.sell_trade(chat_id, ticker, broker_close=True)
            if not result["ok"]:
                await query.answer("⚠️ Trade ist nicht mehr aktiv.", show_alert=True)
                try:
                    await query.edit_message_reply_markup(reply_markup=None)
                except Exception:
                    pass
                return

            entry, current, leverage = result["entry"], result["current"], result["leverage"]
            pnl_pct, pnl_eur = result["pnl_pct"], result["pnl_eur"]
            sign = "+" if pnl_eur >= 0 else ""
            await query.edit_message_reply_markup(reply_markup=None)
            await query.edit_message_text(
                (query.message.text or "") +
                f"\n\n⏳ *Verkauf läuft* — Einstieg ${entry:.2f} → angefragt bei Alpaca (Hebel {leverage:g}×)\n"
                f"Strategie-Rohscore ({_strategy_label(result['trade'].get('signal', {}))}): "
                f"Einstieg {_fmt_strength(result['entry_strength'])} → "
                f"Ausstieg {_fmt_strength(result['exit_strength'])}  ·  "
                f"Vorläufig: {sign}{pnl_pct:.1f}% ({sign}{pnl_eur:.2f}€)",
                parse_mode="Markdown"
            )
            await _maybe_broker_close_trade(context.bot, user, result["trade"],
                                            broker_symbol=reconcile_mod.bot_symbol(result["trade"]))
            log.info(f"[{chat_id}] Broker-Verkauf angestoßen: {ticker}")
        else:
            result = trade_svc.sell_trade(chat_id, ticker)
            if not result["ok"]:
                await query.answer("⚠️ Trade ist nicht mehr aktiv.", show_alert=True)
                try:
                    await query.edit_message_reply_markup(reply_markup=None)
                except Exception:
                    pass
                return

            entry, current, leverage = result["entry"], result["current"], result["leverage"]
            pnl_pct, pnl_eur = result["pnl_pct"], result["pnl_eur"]
            sign = "+" if pnl_eur >= 0 else ""
            emoji = "🟢" if pnl_eur > 0 else ("🔴" if pnl_eur < 0 else "⚪")
            await query.edit_message_reply_markup(reply_markup=None)
            await query.edit_message_text(
                (query.message.text or "") +
                f"\n\n{emoji} *Verkauft* — Einstieg ${entry:.2f} → Ausstieg ${current:.2f} (Hebel {leverage:g}×)\n"
                f"Strategie-Rohscore ({_strategy_label(result['trade'].get('signal', {}))}): "
                f"Einstieg {_fmt_strength(result['entry_strength'])} → "
                f"Ausstieg {_fmt_strength(result['exit_strength'])}  ·  "
                f"Realisiert: {sign}{pnl_pct:.1f}% ({sign}{pnl_eur:.2f}€)",
                parse_mode="Markdown"
            )
            # echte Alpaca-Position (falls vorhanden) mitschließen — bei Options der Kontrakt
            await _maybe_broker_close(context.bot, user, ticker,
                                      broker_symbol=reconcile_mod.bot_symbol(result["trade"]))
            log.info(f"[{chat_id}] Trade verkauft: {ticker} @ ${current:.2f} ({sign}{pnl_eur:.2f}€)")

    elif action in settings_svc.SETTING_ACTIONS:
        # 'ticker' trägt hier den Wert der Aktion (Korb-Key / Zahl / "1"/"0" …)
        alpaca_ready = _alpaca_ready(db.get_user(chat_id) or {})
        user = settings_svc.apply_setting(chat_id, action, ticker, alpaca_ready=alpaca_ready)
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


async def run_daily_lab_optimization(context: ContextTypes.DEFAULT_TYPE):
    """Täglicher Laborlauf während US-Handelszeit: nur Pending erzeugen, niemals Live anwenden."""
    from stockbot.optimize import lab
    if not _us_market_open(extended=False):
        log.info("🧪 Labor-Optimierung übersprungen: US-Markt regulär geschlossen.")
        return
    if lab.is_running():
        log.info("🧪 Labor-Optimierung übersprungen: bereits ein Lauf aktiv.")
        return
    log.info("🧪 Starte täglichen Labor-Optimierungslauf (voller Lauf, pending-only).")
    started = await asyncio.to_thread(lab.start_background_cycle, None)
    if started:
        log.info("🧪 Labor-Optimierung im Hintergrund gestartet.")
    else:
        log.info("🧪 Labor-Optimierung nicht gestartet: Lock belegt.")


run_weekly_lab_optimization = run_daily_lab_optimization  # Legacy-Alias für alte Tests/Imports.


async def run_daily_broker_reconcile(context: ContextTypes.DEFAULT_TYPE):
    """Täglicher Vollabgleich Bot-DB ↔ Alpaca um Positions-Drift zu reparieren.

    Führt beide Richtungen aus:
    - Bot aktiv, Alpaca weg → als verkauft schließen (mit bestehender Sicherheitsprüfung)
    - Alpaca offen, Bot kennt es nicht → als aktiven Trade übernehmen
    """
    log.info("🔄 Starte täglichen Alpaca↔Bot-Positionsabgleich.")
    await monitor_missing_broker_positions(context.bot, full=True)
    await monitor_orphan_broker_positions(context.bot, full=True)
    log.info("🔄 Täglicher Alpaca↔Bot-Positionsabgleich beendet.")


_last_signals_fire_date: date | None = None
_last_close_fire_date: date | None = None
# Toleranzfenster nach dem Zielzeitpunkt: verhindert, dass ein Neustart Stunden nach dem
# eigentlichen Zeitpunkt (z. B. abends) den Job fälschlich sofort nachfeuert.
_SESSION_FIRE_WINDOW_MIN = 10


def _session_job_due(now_utc: datetime, target_utc: datetime, last_fired_date: date | None,
                      today: date, window_min: int = _SESSION_FIRE_WINDOW_MIN) -> bool:
    """Reine Entscheidungslogik (testbar ohne Job-Queue/Mocking): darf der Job jetzt feuern?"""
    if last_fired_date == today:
        return False
    return target_utc <= now_utc < target_utc + timedelta(minutes=window_min)


async def _session_scheduler_tick(context: ContextTypes.DEFAULT_TYPE):
    """DATA-002: feuert Eröffnungssignale (`SIGNAL_OPEN_OFFSET_MIN` nach Open) und Tagesauswertung
    (`CLOSE_AFTER_CLOSE_OFFSET_MIN` nach Close) relativ zum echten NYSE/Nasdaq-Handelstag statt zu
    einer festen Europe/Berlin-Uhrzeit — läuft alle `SESSION_TICK_INTERVAL_SEC` Sekunden und feuert
    jeden der beiden Jobs höchstens einmal pro Handelstag."""
    global _last_signals_fire_date, _last_close_fire_date
    metrics.heartbeat()
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.tzinfo is None:
        now_et = now_et.replace(tzinfo=ZoneInfo("America/New_York"))
    today = now_et.date()
    if not exchange_calendar.is_trading_day(today):
        return
    now_utc = now_et.astimezone(timezone.utc)

    open_dt = exchange_calendar.market_open(today)
    if open_dt is not None:
        signal_target = open_dt + timedelta(minutes=SIGNAL_OPEN_OFFSET_MIN)
        if _session_job_due(now_utc, signal_target, _last_signals_fire_date, today):
            _last_signals_fire_date = today
            await send_daily_signals(context)

    close_dt = exchange_calendar.market_close(today)
    if close_dt is not None:
        close_target = close_dt + timedelta(minutes=CLOSE_AFTER_CLOSE_OFFSET_MIN)
        if _session_job_due(now_utc, close_target, _last_close_fire_date, today):
            _last_close_fire_date = today
            await close_and_evaluate(context)


async def post_trade_risk_scan_job(context: ContextTypes.DEFAULT_TYPE):
    """Alarmiert den Admin gebündelt über offene Positionen ohne Schutzorder."""
    if ADMIN_CHAT_ID is None:
        return

    async def notify_admin(message: str) -> None:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=message)

    state = context.job.data["previous_findings"]
    await run_post_trade_scan(notifier=notify_admin, previous_findings=state)


async def poll_broker_orders_job(context: ContextTypes.DEFAULT_TYPE):
    """Speist spätere Alpaca-Orderstatus während der regulären Handelszeit ins OMS ein."""
    if not _us_market_open(extended=False):
        return
    orders_by_user: dict[int, list[dict]] = {}
    for order in db.get_open_oms_orders():
        orders_by_user.setdefault(int(order["user_id"]), []).append(order)
    for user_id, orders in orders_by_user.items():
        user = db.get_user(user_id)
        client = _alpaca_client(user) if user else None
        if client is None:
            log.warning("[%s] Broker-Poll ohne verfügbaren Alpaca-Client übersprungen", user_id)
            continue

        def fetch_status(order_id: str, *, _client=client):
            return broker.get_order_status(order_id, client=_client)

        loop = asyncio.get_running_loop()

        def notify_admin(message: str) -> None:
            if ADMIN_CHAT_ID is not None:
                asyncio.run_coroutine_threadsafe(
                    context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=message), loop,
                )

        def handle_partial_fill(result, *, _client=client):
            intent = db.get_oms_trade_intent(result.order.trade_intent_id)
            trade = db.get_trade_by_id(int(intent["signal_id"])) if intent else None
            signal_stop_loss = (trade.get("signal") or {}).get("stop_loss") if trade else None
            active_orders = tuple(
                _as_order(row) for row in orders
                if int(row["user_id"]) == result.order.user_id
                and str(row["ticker"]) == result.order.ticker
            ) + tuple(
                Order(
                    id=int(row["id"]), trade_intent_id=int(row["trade_intent_id"]),
                    user_id=int(row["user_id"]), ticker=str(row["ticker"]),
                    side=str(row["side"]), qty=float(row["qty"]),
                    status=OrderStatus(row["status"]), broker_order_id=row["broker_order_id"],
                    created_at=row["created_at"], updated_at=row["updated_at"],
                )
                for row in db.get_active_protective_orders(
                    result.order.user_id, result.order.ticker,
                )
            )
            submitted_at = datetime.fromisoformat(
                str(result.order.created_at).replace("Z", "+00:00")
            )
            if submitted_at.tzinfo is None:
                submitted_at = submitted_at.replace(tzinfo=timezone.utc)
            orchestrate_partial_fill(
                order=result.order, position=result.position,
                remaining_qty=result.remaining_qty, order_submitted_at=submitted_at,
                now=datetime.now(timezone.utc), active_orders=active_orders,
                signal_stop_loss=signal_stop_loss,
                submit_stop_sell=lambda symbol, **kwargs: broker.submit_stop_sell(
                    symbol, client=_client, **kwargs,
                ),
                cancel_order=lambda order_id: broker.cancel_order(order_id, client=_client),
                persist_protective=db.record_protective_order, notifier=notify_admin,
            )

        await asyncio.to_thread(
            poll_broker_orders, _oms, orders,
            status_fetcher=fetch_status, strategy_version_id=0,
            partial_fill_handler=handle_partial_fill,
        )


async def periodic_oms_reconciliation_job(context: ContextTypes.DEFAULT_TYPE):
    """Prüft Positionen, Orders und Konto je betroffenem Nutzer ohne Korrekturen."""
    if not _us_market_open(extended=False):
        return

    position_rows, _ = db.get_post_trade_risk_rows()
    user_ids = {int(row["user_id"]) for row in position_rows}
    user_ids.update(int(row["user_id"]) for row in db.get_open_oms_orders())

    reports = {}
    for user_id in sorted(user_ids):
        user = db.get_user(user_id)
        client = _alpaca_client(user) if user else None
        if client is None:
            log.warning("[%s] OMS-Reconciliation ohne verfügbaren Alpaca-Client übersprungen", user_id)
            continue
        reports[user_id] = await asyncio.to_thread(reconcile_user_oms, user, client)

    current = reconciliation_finding_keys(reports)
    previous = context.job.data["previous_findings"]
    message = format_reconciliation_admin_alarm(reports)
    if ADMIN_CHAT_ID is not None and message and current != previous:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=message)
    previous.clear()
    previous.update(current)


async def purge_callback_tokens_job(context):
    """Löscht abgelaufene Callback-Tokens (W7) — reine Hygiene, fail-open."""
    try:
        db.purge_expired_callback_tokens(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    except Exception as e:
        log.warning(f"Callback-Token-Purge fehlgeschlagen: {e}")


async def outbox_delivery_job(context):
    """Stellt fällige Domain-Events aus der Outbox zu (W4.5) — fail-open.

    Ohne diesen Job blieben die Events für immer liegen: die Outbox war gebaut, aber nie
    angeschlossen, sodass `burn_in.dead_letter_events` strukturell 0 meldete. Zugestellt wird
    an den `ObservabilityConsumer` — der Telegram-Versand läuft weiterhin direkt am
    Handelspfad und wird hier NICHT dupliziert.
    """
    try:
        result = outbox.deliver_due([_OUTBOX_CONSUMER])
        if result.dead:
            log.warning(f"Outbox: {result.dead} Event(s) im Dead-Letter — Zustellung prüfen.")
    except Exception as e:
        log.warning(f"Outbox-Zustellung fehlgeschlagen: {e}")


def _register_jobs(app):
    """Plant alle Hintergrund-Jobs: Tagessignale, Tagesauswertung, Smart-Money-Scan und
    den laufenden Trade-Monitor (Auto-Close alle MONITOR_INTERVAL_SEC, solange Markt offen)."""
    job_queue = app.job_queue
    # DATA-002: Tagessignale + Tagesauswertung feuern relativ zu Open/Close (exchange_calendar),
    # nicht mehr über run_daily zu einer festen Berlin-Uhrzeit.
    job_queue.run_repeating(
        _session_scheduler_tick, interval=SESSION_TICK_INTERVAL_SEC, first=10,
        name="session_scheduler_tick")
    job_queue.run_daily(
        scan_smart_money,
        time=datetime.now(BERLIN_TZ).replace(
            hour=SMARTMONEY_SCAN_HOUR, minute=SMARTMONEY_SCAN_MIN, second=0, microsecond=0).timetz(),
        name="smartmoney_scan")
    job_queue.run_daily(
        run_daily_broker_reconcile,
        time=datetime.now(BERLIN_TZ).replace(
            hour=BROKER_RECONCILE_HOUR, minute=BROKER_RECONCILE_MIN, second=0, microsecond=0).timetz(),
        name="daily_broker_reconcile")
    if LAB_DAILY_OPTIMIZATION:
        job_queue.run_daily(
            run_daily_lab_optimization,
            time=datetime.now(BERLIN_TZ).replace(
                hour=LAB_DAILY_HOUR, minute=LAB_DAILY_MIN, second=0, microsecond=0).timetz(),
            days=LAB_DAILY_DAYS,
            name="daily_lab_optimization")
    # Shadow-Signalerzeugung (RES-002): regelmäßig Schatten-Signale generieren + persistieren
    # (getrennter Shadow-Report im Dashboard). Marktzeit-gated im Job selbst.
    job_queue.run_repeating(run_shadow_signals, interval=INTRADAY_SCAN_INTERVAL_SEC,
                            first=INTRADAY_SCAN_INTERVAL_SEC, name="shadow_signals")
    # Intraday: alle 30 Min während der Handelszeit nach NEUEN Signalen suchen und pushen.
    job_queue.run_repeating(scan_intraday, interval=INTRADAY_SCAN_INTERVAL_SEC,
                            first=INTRADAY_SCAN_INTERVAL_SEC, name="intraday_signals")
    # Aktive Trades laufend überwachen (Auto-Close bei SL/TP oder Signal-Verfall)
    job_queue.run_repeating(monitor_trades, interval=MONITOR_INTERVAL_SEC, first=30, name="monitor_trades")
    job_queue.run_repeating(
        post_trade_risk_scan_job, interval=POST_TRADE_SCAN_INTERVAL_SEC,
        first=POST_TRADE_SCAN_INTERVAL_SEC, name="post_trade_risk_scan",
        data={"previous_findings": set()},
    )
    job_queue.run_repeating(
        poll_broker_orders_job, interval=BROKER_POLL_INTERVAL_SEC,
        first=BROKER_POLL_INTERVAL_SEC, name="broker_order_poll",
    )
    job_queue.run_repeating(
        periodic_oms_reconciliation_job, interval=RECONCILE_PERIODIC_SEC,
        first=RECONCILE_PERIODIC_SEC, name="periodic_oms_reconciliation",
        data={"previous_findings": set()},
    )
    job_queue.run_repeating(
        purge_callback_tokens_job, interval=24 * 3600, first=3600,
        name="purge_callback_tokens",
    )
    job_queue.run_repeating(
        outbox_delivery_job, interval=OUTBOX_DELIVERY_SEC, first=OUTBOX_DELIVERY_SEC,
        name="outbox_delivery",
    )


async def _post_init(app):
    """Nach dem Bot-Start das Telegram-Befehlsmenü setzen (fail-open: rein kosmetisch)."""
    try:
        await app.bot.set_my_commands(menu.BOT_COMMANDS)
    except Exception as e:
        log.warning(f"set_my_commands fehlgeschlagen (Menü bleibt alt): {e}")


def main():
    validate_config()
    assert_postgres_backend()   # Prod läuft Postgres-only (Scheibe 9); SQLite nur Dev/Test
    db.init_db()
    db.ensure_strategy_versions_published()   # Gate P5: produktive Strategieversionen persistent verfügbar
    kill_switch_service.reload()

    if RUN_DASHBOARD_IN_BOT:
        _start_dashboard_thread()

    app = Application.builder().token(TELEGRAM_TOKEN).post_init(_post_init).build()

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
    app.add_handler(CommandHandler("website", cmd_website))               # Ein-Klick-Login zur Web-App
    app.add_handler(CommandHandler("dashboard", cmd_dashboard))           # Link zum Web-Dashboard
    app.add_handler(CommandHandler("ping", cmd_ping))                     # Verbindungstest
    app.add_handler(CommandHandler("killswitch", cmd_killswitch))         # globaler Admin-Kill-Switch
    app.add_handler(CommandHandler("signals", cmd_signals))               # echte Live-Analyse jetzt sofort
    app.add_handler(CommandHandler("top5trade", cmd_top5trade))           # was große Trader handeln
    app.add_handler(CommandHandler("evaluate", cmd_evaluate))             # aktive Demo-Trades jetzt sofort auswerten
    app.add_handler(CommandHandler("strategies", cmd_strategies))         # verfügbare Strategien
    app.add_handler(CommandHandler("addstrat", cmd_addstrat))             # Strategie per Namen wählen
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))           # persönliche Watchlist anzeigen
    app.add_handler(CommandHandler("watchadd", cmd_watchadd))             # Symbol zur Watchlist hinzufügen
    app.add_handler(CommandHandler("watchdel", cmd_watchdel))             # Symbol aus der Watchlist entfernen
    app.add_handler(CommandHandler("teststrat", cmd_teststrat))           # Backtest-Kennzahlen der aktiven Strategie
    app.add_handler(CommandHandler("kicheck", cmd_kicheck))               # Selbsttest des KI-Rankings (Claude Haiku)
    app.add_handler(CommandHandler("brokercheck", cmd_brokercheck))       # Selbsttest der Alpaca-Anbindung
    app.add_handler(CommandHandler("disconnectalpaca", cmd_disconnect_alpaca))   # Alpaca-Keys löschen
    # Hauptmenü-Buttons (Reply-Keyboard): NACH den ConversationHandlern registriert,
    # damit deren Text-States (Setup-Dialog, Alpaca-Keys) Vorrang behalten.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_button_dispatch))
    # Button-Handler registrieren
    app.add_handler(CallbackQueryHandler(button_handler))
    # Globaler Error-Handler (saubere Logs statt Tracebacks)
    app.add_error_handler(error_handler)

    # Einmalige Datenreparatur beim Start: abgeschlossene Trades mit unplausiblem Einstieg
    # (Glitch-Fills wie KHC @ 0,26 → +53.810 € Fake-P&L) korrigieren. Idempotent.
    for _u in db.list_active_users():
        try:
            db.heal_absurd_closed_pnl(_u["user_id"])
        except Exception as _e:
            log.warning(f"heal_absurd_closed_pnl fehlgeschlagen für {_u.get('user_id')}: {_e}")

    # Jobs planen (täglich zur fixen Uhrzeit + laufender Trade-Monitor)
    _register_jobs(app)

    log.info("🤖 Bot gestartet. Warte auf Jobs...")
    log.info(f"  → Signale: {SIGNAL_TIME_HOUR:02d}:{SIGNAL_TIME_MIN:02d} Uhr")
    log.info(f"  → Auswertung: {CLOSE_TIME_HOUR:02d}:{CLOSE_TIME_MIN:02d} Uhr")
    log.info(f"  → Smart-Money-Scan: {SMARTMONEY_SCAN_HOUR:02d}:{SMARTMONEY_SCAN_MIN:02d} Uhr")
    log.info(f"  → Trade-Monitor: alle {MONITOR_INTERVAL_SEC}s")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
