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


def effective_leverage(signal: dict) -> float:
    """Der TATSÄCHLICH realisierte Hebel eines Trades für P&L/Liquidation.

    Fällt ein gehebelter Trade auf Bruchteil-Aktien zurück (kein Optionskontrakt bezahlbar),
    wird beim Order-Versand `effective_leverage = 1.0` ins Signal geschrieben — dann darf das P&L
    NICHT mehr mit dem Wunsch-Hebel gerechnet werden (sonst überzeichnet vs. echter Position).
    Ohne `effective_leverage` (reine Demo) gilt der gewünschte `leverage`."""
    eff = signal.get("effective_leverage")
    if eff is None:
        eff = signal.get("leverage", 1.0)
    return float(eff or 1.0)


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


def option_pnl(signal: dict, entry_underlying: float, current_underlying: float,
               trade_size_eur: float, *, current_premium: float | None = None) -> tuple[float, float]:
    """P&L eines Long-Call-Demo-Trades. Rückgabe (underlying_pct, pnl_eur).

    - Mit `current_premium` (live): echte, nichtlineare Bewertung
      `(premium_jetzt − premium_einstieg) × 100 × Kontrakte`.
    - Ohne Live-Prämie: Omega-Näherung `trade_size × underlying_pct% × omega`.
    Verlust ist auf die gezahlte Prämie (≈ Budget) begrenzt."""
    entry_premium = signal.get("entry_premium")
    contracts = int(signal.get("contracts") or 0)
    omega = float(signal.get("omega") or 1.0)
    underlying_pct = ((current_underlying - entry_underlying) / entry_underlying * 100
                      if entry_underlying else 0.0)
    if current_premium is not None and entry_premium and contracts:
        pnl_eur = (current_premium - entry_premium) * 100 * contracts
        pnl_eur = max(pnl_eur, -entry_premium * 100 * contracts)   # Prämie ist der Maximalverlust
    else:
        pnl_eur = trade_size_eur * (underlying_pct / 100) * omega
        pnl_eur = max(pnl_eur, -trade_size_eur)
    return underlying_pct, pnl_eur


def trade_pnl(trade: dict, current_underlying: float, trade_size_eur: float,
              *, current_premium: float | None = None) -> tuple[float, float]:
    """Einheitliche P&L für aktive Trades: optionsbewusst. Gibt (pct, eur) zurück.
    Bei Options-Trades (Signal hat `option_symbol`) wird die Optionslogik genutzt, sonst
    das lineare Aktien-/Hebelmodell (`realized_pnl`)."""
    sig = trade.get("signal") or {}
    if sig.get("option_symbol"):
        return option_pnl(sig, trade["entry"], current_underlying, trade_size_eur,
                          current_premium=current_premium)
    return realized_pnl(trade["entry"], current_underlying, trade["direction"],
                        trade_size_eur, effective_leverage(sig))


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
        leverage    = effective_leverage(signal)   # realisierter Hebel (Aktien-Fallback → 1×)
        is_option   = bool(signal.get("option_symbol"))

        close_price = get_current_price(ticker, entry)
        exit_price  = close_price
        exit_reason = "Schlusskurs"

        if direction == "long" and entry:
            day_high, day_low = get_day_high_low(ticker, close_price)
            # Liquidation gilt nur für gehebelte Aktien — Long-Optionen können maximal die Prämie verlieren.
            liq = None if is_option else liquidation_price(entry, leverage, direction)
            # Reihenfolge: Liquidation (am schlimmsten) → Stop-Loss → Take-Profit
            if liq is not None and day_low <= liq:
                exit_price, exit_reason = liq, "Liquidation 💥"
            elif stop_loss and day_low <= stop_loss:
                exit_price, exit_reason = stop_loss, "Stop-Loss 🛑"
            elif take_profit and day_high >= take_profit:
                exit_price, exit_reason = take_profit, "Take-Profit 🎯"

        # P&L optionsbewusst (EOD ohne Live-Prämie → Omega-Näherung für Optionen).
        pnl_pct, pnl_eur = trade_pnl(trade, exit_price, trade_size_eur)

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
