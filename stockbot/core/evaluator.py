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


# ── Zentrale Geld-Mathematik (Hebel + Liquidation) ──────────────────────────

def liquidation_price(entry: float, leverage: float, direction: str = "long") -> float | None:
    """Liquidationskurs: Long wird bei einem Kursverlust von 1/Hebel liquidiert.
    Bei Hebel 1 gibt es keine Liquidation (None)."""
    if not entry or not leverage or leverage <= 1.0:
        return None
    if direction == "long":
        return entry * (1 - 1.0 / leverage)
    return entry * (1 + 1.0 / leverage)   # short


def realized_pnl(entry: float, exit_price: float, direction: str,
                 trade_size_eur: float, leverage: float = 1.0) -> tuple[float, float]:
    """Gibt (pnl_pct, pnl_eur) zurück. pnl_pct = reine Kursbewegung in %,
    pnl_eur = mit Hebel skaliert (auf Totalverlust der Margin begrenzt)."""
    if not entry:
        return 0.0, 0.0
    if direction == "long":
        pnl_pct = (exit_price - entry) / entry * 100
    else:
        pnl_pct = (entry - exit_price) / entry * 100
    pnl_eur = trade_size_eur * (pnl_pct / 100) * (leverage or 1.0)
    pnl_eur = max(pnl_eur, -trade_size_eur)   # mehr als die Margin kann man nicht verlieren
    return pnl_pct, pnl_eur


def evaluate_trades(active_trades: list[dict], trade_size_eur: float) -> list[dict]:
    """
    Schließt aktive Trades zum Tagesende und berechnet P&L (inkl. individuellem Hebel je Trade).
    Prüft je Trade Liquidation, Stop-Loss und Take-Profit anhand der Tagesspanne.
    """
    results = []

    for trade in active_trades:
        ticker    = trade["ticker"]
        entry     = trade["entry"]
        direction = trade["direction"]

        signal = trade.get("signal", {})
        stop_loss   = signal.get("stop_loss")
        take_profit = signal.get("take_profit")
        leverage    = signal.get("leverage", 1.0) or 1.0

        close_price = get_current_price(ticker, entry)
        exit_price  = close_price
        exit_reason = "Schlusskurs"

        if direction == "long" and entry:
            day_high, day_low = get_day_high_low(ticker, close_price)
            liq = liquidation_price(entry, leverage, direction)
            # Reihenfolge: Liquidation (am schlimmsten) → Stop-Loss → Take-Profit
            if liq is not None and day_low <= liq:
                exit_price, exit_reason = liq, "Liquidation 💥"
            elif stop_loss and day_low <= stop_loss:
                exit_price, exit_reason = stop_loss, "Stop-Loss 🛑"
            elif take_profit and day_high >= take_profit:
                exit_price, exit_reason = take_profit, "Take-Profit 🎯"

        pnl_pct, pnl_eur = realized_pnl(entry, exit_price, direction, trade_size_eur, leverage)

        results.append({
            "ticker":  ticker,
            "entry":   entry,
            "exit":    exit_price,
            "pnl_pct": pnl_pct,
            "pnl_eur": pnl_eur,
            "direction": direction,
            "leverage": leverage,
            "exit_reason": exit_reason,
        })

        log.info(
            f"{ticker}: {entry:.2f} → {exit_price:.2f} ({exit_reason}, {leverage:g}×) | "
            f"{'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}% | "
            f"{'+' if pnl_eur >= 0 else ''}{pnl_eur:.2f}€"
        )

    return results
