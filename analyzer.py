"""
Technische Analyse der S&P 500 Aktien
Indikatoren: RSI, MACD, Moving Averages, Volumen
"""

import logging
import numpy as np
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import (
    WATCHLIST, TOP_N_SIGNALS, MIN_SIGNAL_STRENGTH,
    RSI_PERIOD, RSI_OVERSOLD, RSI_OVERBOUGHT,
    MA_SHORT, MA_LONG
)

log = logging.getLogger(__name__)


# ── Technische Indikatoren ──────────────────────────────────────────────────

def calc_rsi(closes: np.ndarray, period: int = 14) -> float:
    """Relative Strength Index berechnen."""
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calc_macd(closes: np.ndarray):
    """MACD Linie und Signal berechnen."""
    def ema(data, span):
        alpha = 2 / (span + 1)
        result = [data[0]]
        for p in data[1:]:
            result.append(alpha * p + (1 - alpha) * result[-1])
        return np.array(result)

    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd_line = ema12 - ema26
    signal_line = ema(macd_line, 9)
    histogram = macd_line - signal_line
    return macd_line[-1], signal_line[-1], histogram[-1]


def calc_signal_strength(rsi, macd_line, macd_signal, macd_hist,
                          price, ma50, ma200, vol_ratio, direction) -> int:
    """
    Berechnet Signal-Stärke 1-5 basierend auf Indikator-Übereinstimmung.
    Alle Punkte müssen in die gleiche Richtung zeigen.
    """
    score = 0

    if direction == "long":
        if rsi < RSI_OVERSOLD:          score += 1
        if macd_line > macd_signal:     score += 1
        if macd_hist > 0:               score += 1
        if ma50 and price > ma50:       score += 1
        if vol_ratio > 1.2:             score += 1
    else:  # short (für spätere Verwendung)
        if rsi > RSI_OVERBOUGHT:        score += 1
        if macd_line < macd_signal:     score += 1
        if macd_hist < 0:               score += 1
        if ma50 and price < ma50:       score += 1
        if vol_ratio > 1.2:             score += 1

    return score


# ── Einzelne Aktie analysieren ──────────────────────────────────────────────

def analyze_ticker(ticker: str) -> dict | None:
    """
    Lädt historische Daten und berechnet technische Signale.
    Gibt None zurück wenn kein klares Signal.
    """
    try:
        df = yf.download(ticker, period="1y", interval="1d",
                         progress=False, auto_adjust=True)
        if df is None or len(df) < MA_LONG + 10:
            return None

        closes  = df["Close"].values.flatten().astype(float)
        volumes = df["Volume"].values.flatten().astype(float)
        price   = float(closes[-1])

        # Indikatoren
        rsi = calc_rsi(closes, RSI_PERIOD)
        macd_line, macd_signal, macd_hist = calc_macd(closes)

        ma50  = float(np.mean(closes[-MA_SHORT:])) if len(closes) >= MA_SHORT else None
        ma200 = float(np.mean(closes[-MA_LONG:]))  if len(closes) >= MA_LONG  else None

        avg_vol   = float(np.mean(volumes[-20:]))
        last_vol  = float(volumes[-1])
        vol_ratio = last_vol / avg_vol if avg_vol > 0 else 1.0

        # Richtung bestimmen
        if rsi < RSI_OVERSOLD and macd_hist > 0:
            direction = "long"
        elif rsi > RSI_OVERBOUGHT and macd_hist < 0:
            direction = "long"  # Demo: nur longs
        else:
            return None  # Kein klares Signal

        direction = "long"  # Demo-Modus: immer long

        strength = calc_signal_strength(
            rsi, macd_line, macd_signal, macd_hist,
            price, ma50, ma200, vol_ratio, direction
        )

        if strength < MIN_SIGNAL_STRENGTH:
            return None

        # Kommentare für die Telegram-Nachricht
        rsi_comment = (
            f"{rsi:.1f} — Überverkauft 📉" if rsi < 35 else
            f"{rsi:.1f} — Neutral ↔" if rsi < 55 else
            f"{rsi:.1f} — Überkauft 📈"
        )

        macd_comment = (
            "Bullish Crossover ✅" if macd_hist > 0 and macd_line > macd_signal else
            "Bearish Crossover ⚠️" if macd_hist < 0 else
            "Neutral"
        )

        if ma50 and ma200:
            if price > ma50 > ma200:
                trend_comment = "Starker Aufwärtstrend 📈"
            elif price > ma50:
                trend_comment = "Aufwärtstrend 📈"
            elif price < ma50 < ma200:
                trend_comment = "Abwärtstrend 📉"
            else:
                trend_comment = "Seitwärts ↔"
        else:
            trend_comment = "Daten unvollständig"

        volume_comment = (
            f"{vol_ratio:.1f}x Durchschnitt — Hohes Interesse 🔥" if vol_ratio > 1.5 else
            f"{vol_ratio:.1f}x Durchschnitt — Normal" if vol_ratio > 0.8 else
            f"{vol_ratio:.1f}x Durchschnitt — Gering"
        )

        return {
            "ticker":         ticker,
            "price":          price,
            "direction":      direction,
            "strength":       strength,
            "rsi":            rsi,
            "rsi_comment":    rsi_comment,
            "macd_comment":   macd_comment,
            "trend_comment":  trend_comment,
            "volume_comment": volume_comment,
            "macd_hist":      float(macd_hist),
            "vol_ratio":      vol_ratio,
        }

    except Exception as e:
        log.warning(f"Fehler bei {ticker}: {e}")
        return None


# ── Top-Signale auswählen ───────────────────────────────────────────────────

def get_top_signals() -> list[dict]:
    """
    Analysiert alle Watchlist-Aktien parallel und gibt
    die TOP_N_SIGNALS stärksten Signale zurück.
    """
    log.info(f"Analysiere {len(WATCHLIST)} Aktien...")
    results = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(analyze_ticker, t): t for t in WATCHLIST}
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)

    # Sortieren: erst nach Stärke, dann nach RSI-Abstand von 50
    results.sort(key=lambda x: (x["strength"], abs(x["rsi"] - 50)), reverse=True)

    top = results[:TOP_N_SIGNALS]
    log.info(f"Top {len(top)} Signale gefunden: {[s['ticker'] for s in top]}")
    return top
