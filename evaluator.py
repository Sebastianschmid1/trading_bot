"""
Auswertung der Demo-Trades um 15:30 Uhr
Holt Tagesschlusskurs und berechnet P&L
"""

import logging
import yfinance as yf
from config import TRADE_SIZE_EUR

log = logging.getLogger(__name__)


def get_current_price(ticker: str, fallback: float) -> float:
    """Holt den aktuellen Kurs einer Aktie."""
    try:
        info = yf.Ticker(ticker).fast_info
        return float(info.last_price)
    except Exception as e:
        log.warning(f"Kurs für {ticker} nicht abrufbar: {e}")
        return fallback


def evaluate_trades(active_trades: list[dict]) -> list[dict]:
    """
    Berechnet P&L für alle aktiven Trades.
    Gibt Liste mit Ergebnissen zurück.
    """
    results = []

    for trade in active_trades:
        ticker    = trade["ticker"]
        entry     = trade["entry"]
        direction = trade["direction"]

        exit_price = get_current_price(ticker, entry)

        # P&L berechnen
        if direction == "long":
            pnl_pct = (exit_price - entry) / entry * 100
        else:  # short
            pnl_pct = (entry - exit_price) / entry * 100

        pnl_eur = TRADE_SIZE_EUR * (pnl_pct / 100)

        results.append({
            "ticker":  ticker,
            "entry":   entry,
            "exit":    exit_price,
            "pnl_pct": pnl_pct,
            "pnl_eur": pnl_eur,
            "direction": direction,
        })

        log.info(
            f"{ticker}: {entry:.2f} → {exit_price:.2f} | "
            f"{'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}% | "
            f"{'+' if pnl_eur >= 0 else ''}{pnl_eur:.2f}€"
        )

    return results
