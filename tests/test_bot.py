"""
🛠️ Dev-Script — manuell echte Signale senden & Demo-Trades auswerten.
Verwendung: python test_bot.py [telegram|signals|evaluate|analyze]

Alle Modi arbeiten mit ECHTEN Marktdaten (keine erfundenen Testdaten).
Demo-Trades verwenden echte Kurse, aber kein echtes Geld.

Hinweis: Der Bot ist multi-user (Registrierung per /start). Diese Modi
richten sich an den ersten registrierten, aktiven Nutzer aus der DB —
führe also zuerst einmal /start mit deinem Account aus.
"""

import asyncio
import sys
from telegram import Bot
from stockbot.market.analyzer import get_top_signals
from stockbot.core.evaluator import evaluate_trades
from stockbot.config import TELEGRAM_TOKEN

from stockbot.core import db


def _get_test_user() -> dict | None:
    """Holt den ersten registrierten, aktiven Nutzer aus der DB (zum manuellen Testen)."""
    db.init_db()
    users = db.list_active_users()
    if not users:
        print("⚠️ Kein registrierter Nutzer gefunden. Sende zuerst /start an den Bot (siehe bot.py).")
        return None
    return users[0]


async def test_telegram():
    """Prüft, ob der Token korrekt ist und sendet eine Testnachricht an den ersten registrierten Nutzer."""
    user = _get_test_user()
    if not user:
        return

    print("🔌 Verbinde mit Telegram...")
    bot = Bot(TELEGRAM_TOKEN)

    me = await bot.get_me()
    print(f"✅ Bot erreichbar: @{me.username} ({me.first_name})")
    await bot.send_message(
        chat_id=user["user_id"],
        text="🧪 Testnachricht — Telegram-Verbindung funktioniert!"
    )
    print(f"✅ Testnachricht an user_id={user['user_id']} gesendet. Bitte in Telegram prüfen.")


async def test_signals():
    """Analysiert live und sendet die echten aktuellen Signale an den ersten registrierten Nutzer."""
    user = _get_test_user()
    if not user:
        return

    print("🔍 Analysiere Aktien...")
    signals = get_top_signals()

    bot = Bot(TELEGRAM_TOKEN)

    if not signals:
        print("⚠️ Keine Signale gefunden.")
        await bot.send_message(chat_id=user["user_id"], text="⚠️ Heute keine klaren Signale gefunden.")
        return

    print(f"📤 Sende {len(signals)} Signale an user_id={user['user_id']}...")
    for s in signals:
        print(f"  → {s['ticker']}: {s['direction'].upper()} | Stärke {s['strength']}/5 | RSI {s['rsi']:.1f}")

    # Import hier um zirkuläre Importe zu vermeiden
    from stockbot.tgbot.bot import send_signal
    for signal in signals:
        await send_signal(bot, user["user_id"], signal, user["trade_size_eur"])
        await asyncio.sleep(1)

    print("✅ Signale gesendet!")


async def test_evaluate():
    """Wertet aktuelle Demo-Trades des ersten registrierten Nutzers sofort aus (echte Kurse)."""
    user = _get_test_user()
    if not user:
        return

    chat_id = user["user_id"]
    active = db.get_active_trades(chat_id)

    if not active:
        print("⚠️ Keine aktiven Trades heute.")
        return

    print(f"📊 Werte {len(active)} aktive Trades aus...")
    results = evaluate_trades(active, user["trade_size_eur"])
    db.close_all(chat_id, results)

    total = sum(r["pnl_eur"] for r in results)
    print(f"\n{'='*40}")
    for r in results:
        sign = "+" if r["pnl_eur"] >= 0 else ""
        print(f"  {r['ticker']:6s}: ${r['entry']:.2f} → ${r['exit']:.2f} | {sign}{r['pnl_pct']:.1f}% | {sign}{r['pnl_eur']:.2f}€  ({r.get('exit_reason', 'Schlusskurs')})")
    print(f"{'='*40}")
    print(f"  GESAMT: {'+' if total >= 0 else ''}{total:.2f}€")

    bot = Bot(TELEGRAM_TOKEN)
    await bot.send_message(
        chat_id=chat_id,
        text=f"📊 *Auswertung abgeschlossen*\nGesamt P&L: {'+' if total >= 0 else ''}{total:.2f}€",
        parse_mode="Markdown"
    )


async def test_analyze_only():
    """Nur Analyse ausgeben, nichts senden."""
    print("🔍 Analysiere Aktien (kein Telegram)...")
    signals = get_top_signals()

    if not signals:
        print("⚠️ Keine Signale gefunden.")
        return

    print(f"\n{'='*60}")
    print(f"{'Ticker':<8} {'Richtung':<8} {'Stärke':<8} {'Kurs':<10} {'RSI':<8}")
    print(f"{'='*60}")
    for s in signals:
        print(
            f"{s['ticker']:<8} {s['direction'].upper():<8} "
            f"{'★'*s['strength']+'☆'*(5-s['strength']):<8} "
            f"${s['price']:<9.2f} {s['rsi']:<8.1f}"
        )
        print(f"  MACD: {s['macd_comment']}")
        print(f"  Trend: {s['trend_comment']}")
        print(f"  Volumen: {s['volume_comment']}")
        print()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "analyze"

    if mode == "telegram":
        asyncio.run(test_telegram())
    elif mode == "signals":
        asyncio.run(test_signals())
    elif mode == "evaluate":
        asyncio.run(test_evaluate())
    elif mode == "analyze":
        asyncio.run(test_analyze_only())
    else:
        print("Verwendung: python test_bot.py [telegram|signals|evaluate|analyze]")
