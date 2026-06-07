"""
🧪 Test-Script — manuell Signale senden & auswerten
Verwendung: python test_bot.py [telegram|example|signals|evaluate|analyze]

Hinweis: Der Bot ist jetzt multi-user (Registrierung per /start). Diese Tests
schicken Nachrichten an den ersten registrierten, aktiven Nutzer aus der DB —
führe also zuerst einmal /start mit deinem Account aus.
"""

import asyncio
import sys
from telegram import Bot
from analyzer import get_top_signals
from evaluator import evaluate_trades
from config import TELEGRAM_TOKEN

import db


def _get_test_user() -> dict | None:
    """Holt den ersten registrierten, aktiven Nutzer aus der DB (zum manuellen Testen)."""
    db.init_db()
    users = db.list_active_users()
    if not users:
        print("⚠️ Kein registrierter Nutzer gefunden. Sende zuerst /start an den Bot (siehe bot.py).")
        return None
    return users[0]


EXAMPLE_SIGNALS = [
    {
        "ticker": "NVDA",
        "direction": "long",
        "strength": 4,
        "price": 875.40,
        "rsi": 32.1,
        "rsi_comment": "Überverkauft 📉",
        "macd_comment": "Bullish Crossover ✅",
        "trend_comment": "Starker Aufwärtstrend 📈",
        "volume_comment": "1.8x Durchschnitt — Hohes Interesse 🔥",
    },
    {
        "ticker": "TSLA",
        "direction": "short",
        "strength": 3,
        "price": 245.60,
        "rsi": 68.4,
        "rsi_comment": "Überkauft 📈",
        "macd_comment": "Bearish Crossover ❌",
        "trend_comment": "Schwächelnder Trend 📉",
        "volume_comment": "0.9x Durchschnitt — Normales Interesse",
    },
]


async def test_example_signals():
    """Sendet feste Beispiel-Signale (ohne echte Analyse) — zum schnellen Testen von Format & Versand."""
    user = _get_test_user()
    if not user:
        return

    bot = Bot(TELEGRAM_TOKEN)

    print(f"📤 Sende {len(EXAMPLE_SIGNALS)} Beispiel-Signale an user_id={user['user_id']}...")
    for s in EXAMPLE_SIGNALS:
        print(f"  → {s['ticker']}: {s['direction'].upper()} | Stärke {s['strength']}/5 | RSI {s['rsi']:.1f}")

    # Import hier um zirkuläre Importe zu vermeiden
    from bot import send_signal
    for signal in EXAMPLE_SIGNALS:
        await send_signal(bot, user["user_id"], signal, user["trade_size_eur"])
        await asyncio.sleep(1)

    print("✅ Beispiel-Signale gesendet!")


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
    """Sendet sofort die aktuellen Signale an den ersten registrierten Nutzer (zum Testen)."""
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
    from bot import send_signal
    for signal in signals:
        await send_signal(bot, user["user_id"], signal, user["trade_size_eur"])
        await asyncio.sleep(1)

    print("✅ Signale gesendet!")


async def test_evaluate():
    """Wertet aktuelle Demo-Trades des ersten registrierten Nutzers sofort aus."""
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
        print(f"  {r['ticker']:6s}: ${r['entry']:.2f} → ${r['exit']:.2f} | {sign}{r['pnl_pct']:.1f}% | {sign}{r['pnl_eur']:.2f}€")
    print(f"{'='*40}")
    print(f"  GESAMT: {'+' if total >= 0 else ''}{total:.2f}€")

    bot = Bot(TELEGRAM_TOKEN)
    await bot.send_message(
        chat_id=chat_id,
        text=f"🧪 *Test-Auswertung abgeschlossen*\nGesamt P&L: {'+' if total >= 0 else ''}{total:.2f}€",
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
    elif mode == "example":
        asyncio.run(test_example_signals())
    elif mode == "signals":
        asyncio.run(test_signals())
    elif mode == "evaluate":
        asyncio.run(test_evaluate())
    elif mode == "analyze":
        asyncio.run(test_analyze_only())
    else:
        print("Verwendung: python test_bot.py [telegram|example|signals|evaluate|analyze]")
