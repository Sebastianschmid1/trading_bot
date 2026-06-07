"""
Auswertung der Demo-Trades um 15:30 Uhr
Holt Tagesschlusskurs und berechnet P&L
"""

import logging
import yfinance as yf

log = logging.getLogger(__name__)


def get_current_price(ticker: str, fallback: float) -> float:
    """Holt den aktuellen Kurs einer Aktie."""
    try:
        info = yf.Ticker(ticker).fast_info
        return float(info.last_price)
    except Exception as e:
        log.warning(f"Kurs für {ticker} nicht abrufbar: {e}")
        return fallback


def get_day_high_low(ticker: str, fallback: float) -> tuple[float, float]:
    """Holt Tages-Hoch und -Tief der laufenden/letzten Session (für SL/TP-Prüfung)."""
    try:
        hist = yf.Ticker(ticker).history(period="1d")
        if hist is not None and len(hist) > 0:
            return float(hist["High"].iloc[-1]), float(hist["Low"].iloc[-1])
    except Exception as e:
        log.warning(f"Tagesspanne für {ticker} nicht abrufbar: {e}")
    return fallback, fallback


def evaluate_trades(active_trades: list[dict], trade_size_eur: float) -> list[dict]:
    """
    Berechnet P&L für alle aktiven Trades anhand der individuellen Trade-Größe des Nutzers.
    Gibt Liste mit Ergebnissen zurück.
    """
    results = []

    for trade in active_trades:
        ticker    = trade["ticker"]
        entry     = trade["entry"]
        direction = trade["direction"]

        signal = trade.get("signal", {})
        stop_loss   = signal.get("stop_loss")
        take_profit = signal.get("take_profit")

        close_price = get_current_price(ticker, entry)
        exit_price  = close_price
        exit_reason = "Schlusskurs"

        # SL/TP-Prüfung anhand der Tagesspanne (nur long im Demo-Modus).
        # Konservativ: bei beidseitigem Treffer am selben Tag zählt der Stop zuerst,
        # da aus Tagesdaten die Reihenfolge nicht ableitbar ist.
        if direction == "long" and stop_loss and take_profit:
            day_high, day_low = get_day_high_low(ticker, close_price)
            if day_low <= stop_loss:
                exit_price  = stop_loss
                exit_reason = "Stop-Loss 🛑"
            elif day_high >= take_profit:
                exit_price  = take_profit
                exit_reason = "Take-Profit 🎯"

        # P&L berechnen
        if direction == "long":
            pnl_pct = (exit_price - entry) / entry * 100
        else:  # short
            pnl_pct = (entry - exit_price) / entry * 100

        pnl_eur = trade_size_eur * (pnl_pct / 100)

        results.append({
            "ticker":  ticker,
            "entry":   entry,
            "exit":    exit_price,
            "pnl_pct": pnl_pct,
            "pnl_eur": pnl_eur,
            "direction": direction,
            "exit_reason": exit_reason,
        })

        log.info(
            f"{ticker}: {entry:.2f} → {exit_price:.2f} ({exit_reason}) | "
            f"{'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}% | "
            f"{'+' if pnl_eur >= 0 else ''}{pnl_eur:.2f}€"
        )

    return results
