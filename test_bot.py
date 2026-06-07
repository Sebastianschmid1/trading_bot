"""
🧪 Test-Script — manuell Signale senden & auswerten
Verwendung: python test_bot.py [signals|evaluate|analyze]
"""

import asyncio
import sys
from telegram import Bot
from analyzer import get_top_signals
from tracker import TradeTracker
from evaluator import evaluate_trades
from config import TELEGRAM_TOKEN, CHAT_ID


async def test_signals():
    """Sendet sofort 5 Demo-Signale (zum Testen)."""
    print("🔍 Analysiere Aktien...")
    signals = get_top_signals()

    if not signals:
        print("⚠️ Keine Signale gefunden.")
        return

    bot = Bot(TELEGRAM_TOKEN)
    tracker = TradeTracker()

    print(f"📤 Sende {len(signals)} Signale...")
    for s in signals:
        print(f"  → {s['ticker']}: {s['direction'].upper()} | Stärke {s['strength']}/5 | RSI {s['rsi']:.1f}")

    # Import hier um zirkuläre Importe zu vermeiden
    from bot import send_signal
    for signal in signals:
        await send_signal(bot, signal)
        await asyncio.sleep(1)

    print("✅ Signale gesendet!")


async def test_evaluate():
    """Wertet aktuelle Demo-Trades sofort aus."""
    tracker = TradeTracker()
    active = tracker.get_active_trades()

    if not active:
        print("⚠️ Keine aktiven Trades heute.")
        return

    print(f"📊 Werte {len(active)} aktive Trades aus...")
    results = evaluate_trades(active)
    tracker.close_all(results)

    total = sum(r["pnl_eur"] for r in results)
    print(f"\n{'='*40}")
    for r in results:
        sign = "+" if r["pnl_eur"] >= 0 else ""
        print(f"  {r['ticker']:6s}: ${r['entry']:.2f} → ${r['exit']:.2f} | {sign}{r['pnl_pct']:.1f}% | {sign}{r['pnl_eur']:.2f}€")
    print(f"{'='*40}")
    print(f"  GESAMT: {'+' if total >= 0 else ''}{total:.2f}€")

    bot = Bot(TELEGRAM_TOKEN)
    await bot.send_message(
        chat_id=CHAT_ID,
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

    if mode == "signals":
        asyncio.run(test_signals())
    elif mode == "evaluate":
        asyncio.run(test_evaluate())
    elif mode == "analyze":
        asyncio.run(test_analyze_only())
    else:
        print("Verwendung: python test_bot.py [signals|evaluate|analyze]")
