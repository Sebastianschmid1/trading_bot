"""
Technische Analyse der S&P 500 Aktien
Indikatoren: RSI, MACD, Moving Averages, Volumen
"""

import logging
import numpy as np
import pandas as pd
import yfinance as yf

from config import (
    WATCHLIST, TOP_N_SIGNALS, MIN_SIGNAL_STRENGTH,
    RSI_PERIOD, RSI_OVERSOLD, RSI_OVERBOUGHT,
    MA_SHORT, MA_LONG,
    ATR_PERIOD, ATR_SL_MULT, ATR_TP_MULT,
    BLOCK_WEEKLY_DOWNTREND
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


def calc_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float | None:
    """
    Average True Range (Wilder-Glättung) — typische Tagesschwankung in Kurspunkten.
    Basis für ATR-basierte Stop-Loss/Take-Profit-Abstände.
    """
    if len(closes) < period + 1:
        return None

    prev_closes = closes[:-1]
    h, l = highs[1:], lows[1:]
    true_range = np.maximum.reduce([
        h - l,                      # heutige Spanne
        np.abs(h - prev_closes),    # Lücke nach oben vom Vortagesschluss
        np.abs(l - prev_closes),    # Lücke nach unten vom Vortagesschluss
    ])

    atr = np.mean(true_range[:period])  # Seed: einfacher Mittelwert
    for tr in true_range[period:]:      # danach Wilder-Glättung
        atr = (atr * (period - 1) + tr) / period
    return float(atr)


def calc_weekly_trend(df) -> str:
    """
    Höheres Zeitfenster: ermittelt den Wochentrend aus täglichen Schlusskursen
    (Resampling auf Wochenbasis). Gibt 'up', 'down', 'flat' oder 'unknown' zurück.
    """
    try:
        close = df["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        weekly = close.resample("W").last().dropna()
        vals = weekly.values.astype(float)
    except Exception:
        return "unknown"

    if len(vals) < 14:
        return "unknown"

    sma_now  = float(vals[-10:].mean())     # 10-Wochen-Schnitt jetzt
    sma_prev = float(vals[-14:-4].mean())   # 10-Wochen-Schnitt vor 4 Wochen
    last = float(vals[-1])

    if last > sma_now and sma_now >= sma_prev:
        return "up"
    if last < sma_now and sma_now <= sma_prev:
        return "down"
    return "flat"


def _find_pivots(values: np.ndarray, window: int, kind: str) -> list[float]:
    """Findet lokale Extrema (Swing-Hochs bzw. -Tiefs) — ein Bar ist Pivot,
    wenn er das Max/Min in einem Fenster von ±window Bars ist."""
    pivots = []
    n = len(values)
    for i in range(window, n - window):
        seg = values[i - window:i + window + 1]
        if kind == "high" and values[i] >= seg.max():
            pivots.append(float(values[i]))
        elif kind == "low" and values[i] <= seg.min():
            pivots.append(float(values[i]))
    return pivots


def _cluster_levels(levels: list[float], tol: float) -> list[dict]:
    """Fasst nahe beieinanderliegende Pivot-Preise zu einem Level zusammen
    und zählt, wie oft es getestet wurde (touches)."""
    if not levels:
        return []
    levels = sorted(levels)
    clusters = [[levels[0]]]
    for lv in levels[1:]:
        if (lv - clusters[-1][-1]) / clusters[-1][-1] <= tol:
            clusters[-1].append(lv)
        else:
            clusters.append([lv])
    return [{"price": sum(c) / len(c), "touches": len(c)} for c in clusters]


def calc_support_resistance(highs, lows, price, window=5, tol=0.02, lookback=180):
    """
    Ermittelt aus Swing-Hochs/-Tiefs der letzten `lookback` Bars die
    nächste Unterstützung unter und den nächsten Widerstand über dem Kurs.
    Gibt (support, resistance) zurück — je ein dict {price, touches} oder None.
    """
    h, l = highs[-lookback:], lows[-lookback:]
    if len(h) < 2 * window + 1:
        return None, None

    resistances = _cluster_levels(_find_pivots(h, window, "high"), tol)
    supports    = _cluster_levels(_find_pivots(l, window, "low"), tol)

    below = [c for c in supports if c["price"] < price]
    above = [c for c in resistances if c["price"] > price]

    support    = max(below, key=lambda c: c["price"]) if below else None
    resistance = min(above, key=lambda c: c["price"]) if above else None
    return support, resistance


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

def analyze_ticker(ticker: str, df=None) -> dict | None:
    """
    Berechnet technische Signale für eine Aktie.
    `df` kann vorab geladen übergeben werden (Batch-Download); sonst wird einzeln geladen.
    Gibt None zurück wenn kein klares Signal.
    """
    try:
        if df is None:
            df = yf.download(ticker, period="1y", interval="1d",
                             progress=False, auto_adjust=True)
        if df is None or len(df) == 0:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df = df.droplevel(1, axis=1)       # Ticker-Ebene entfernen -> einfache OHLCV-Spalten
        df = df.dropna(subset=["Close"])       # Tage ohne Kurs (z. B. aus Batch-Slice) entfernen
        if len(df) < MA_LONG + 10:
            return None

        closes  = df["Close"].values.flatten().astype(float)
        highs   = df["High"].values.flatten().astype(float)
        lows    = df["Low"].values.flatten().astype(float)
        volumes = df["Volume"].values.flatten().astype(float)
        price   = float(closes[-1])
        as_of   = str(df.index[-1].date())   # Datum des letzten Handelstags in den Daten

        # Indikatoren
        rsi = calc_rsi(closes, RSI_PERIOD)
        macd_line, macd_signal, macd_hist = calc_macd(closes)
        atr = calc_atr(highs, lows, closes, ATR_PERIOD)

        ma50  = float(np.mean(closes[-MA_SHORT:])) if len(closes) >= MA_SHORT else None
        ma200 = float(np.mean(closes[-MA_LONG:]))  if len(closes) >= MA_LONG  else None

        avg_vol   = float(np.mean(volumes[-20:]))
        last_vol  = float(volumes[-1])
        vol_ratio = last_vol / avg_vol if avg_vol > 0 else 1.0

        # Long-Setup bestimmen (Demo-Modus ist long-only):
        # bullishes MACD-Momentum ODER überverkaufter Rücksetzer — aber NICHT
        # in einen überkauften Markt hineinkaufen.
        bullish_macd = macd_hist > 0 and macd_line > macd_signal
        oversold     = rsi < RSI_OVERSOLD
        if rsi >= RSI_OVERBOUGHT or not (bullish_macd or oversold):
            return None  # kein klares Long-Signal
        direction = "long"

        # Höheres Zeitfenster: keine Longs in einem klaren Wochen-Abwärtstrend
        weekly_trend = calc_weekly_trend(df)
        if BLOCK_WEEKLY_DOWNTREND and weekly_trend == "down":
            return None

        strength = calc_signal_strength(
            rsi, macd_line, macd_signal, macd_hist,
            price, ma50, ma200, vol_ratio, direction
        )

        if strength < MIN_SIGNAL_STRENGTH:
            return None

        # Kommentare für die Telegram-Nachricht
        weekly_comment = {
            "up": "Aufwärts 📈", "down": "Abwärts 📉",
            "flat": "Seitwärts ↔", "unknown": "unklar",
        }[weekly_trend]

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

        # ATR-basierte Stop-Loss / Take-Profit (nur long im Demo-Modus)
        if atr and atr > 0:
            stop_loss   = price - ATR_SL_MULT * atr
            take_profit = price + ATR_TP_MULT * atr
            sl_pct      = (stop_loss - price) / price * 100   # negativ
            tp_pct      = (take_profit - price) / price * 100  # positiv
            risk_reward = ATR_TP_MULT / ATR_SL_MULT
        else:
            stop_loss = take_profit = sl_pct = tp_pct = risk_reward = None

        # Support/Resistance: nächster getesteter Level unter / über dem Kurs
        support, resistance = calc_support_resistance(highs, lows, price)
        support_price    = support["price"] if support else None
        support_dist_pct = (support_price - price) / price * 100 if support else None
        resistance_price    = resistance["price"] if resistance else None
        resistance_dist_pct = (resistance_price - price) / price * 100 if resistance else None

        sr_parts = []
        if support:
            sr_parts.append(f"Unterstützung ${support_price:.2f} ({support_dist_pct:+.1f}%, {support['touches']}×)")
        if resistance:
            sr_parts.append(f"Widerstand ${resistance_price:.2f} ({resistance_dist_pct:+.1f}%, {resistance['touches']}×)")
        sr_comment = " · ".join(sr_parts) if sr_parts else "Keine klaren Level"

        return {
            "ticker":         ticker,
            "price":          price,
            "as_of":          as_of,
            "direction":      direction,
            "strength":       strength,
            "rsi":            rsi,
            "rsi_comment":    rsi_comment,
            "macd_comment":   macd_comment,
            "trend_comment":  trend_comment,
            "volume_comment": volume_comment,
            "weekly_trend":   weekly_trend,
            "weekly_comment": weekly_comment,
            "macd_hist":      float(macd_hist),
            "vol_ratio":      vol_ratio,
            "atr":            atr,
            "stop_loss":      stop_loss,
            "take_profit":    take_profit,
            "sl_pct":         sl_pct,
            "tp_pct":         tp_pct,
            "risk_reward":    risk_reward,
            "support":              support_price,
            "support_touches":      support["touches"] if support else None,
            "support_dist_pct":     support_dist_pct,
            "resistance":           resistance_price,
            "resistance_touches":   resistance["touches"] if resistance else None,
            "resistance_dist_pct":  resistance_dist_pct,
            "sr_comment":           sr_comment,
        }

    except Exception as e:
        log.warning(f"Fehler bei {ticker}: {e}")
        return None


# ── Top-Signale auswählen ───────────────────────────────────────────────────

def analyze_universe(tickers: list[str]) -> list[dict]:
    """
    Analysiert eine Ticker-Liste und gibt ALLE gefundenen Signale absteigend nach
    Stärke sortiert zurück (ohne Begrenzung — der Aufrufer schneidet auf top_n).

    Die Kursdaten werden in EINEM Batch-Download geholt (yfinance fügt sie intern
    threadsicher zusammen) — separate parallele yf.download-Aufrufe würden sich
    gegenseitig die Daten überschreiben.
    """
    if not tickers:
        return []

    log.info(f"Analysiere {len(tickers)} Aktien...")
    data = yf.download(
        tickers, period="1y", interval="1d",
        progress=False, auto_adjust=True, group_by="ticker",
    )

    results = []
    for ticker in tickers:
        try:
            df_t = data[ticker]
        except (KeyError, TypeError):
            log.warning(f"Keine Daten für {ticker} im Batch-Download.")
            continue
        result = analyze_ticker(ticker, df_t)
        if result:
            results.append(result)

    # Sortieren: erst nach Stärke, dann nach RSI-Abstand von 50
    results.sort(key=lambda x: (x["strength"], abs(x["rsi"] - 50)), reverse=True)
    log.info(f"{len(results)} Signale gefunden: {[s['ticker'] for s in results]}")
    return results


def get_top_signals(tickers: list[str] | None = None, top_n: int = TOP_N_SIGNALS) -> list[dict]:
    """Analysiert das Universum (Default: WATCHLIST) und gibt die top_n stärksten Signale zurück."""
    ranked = analyze_universe(tickers if tickers is not None else WATCHLIST)
    return ranked[:top_n]
