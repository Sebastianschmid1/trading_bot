"""
Technische Analyse der S&P 500 Aktien
Indikatoren: RSI, MACD, Moving Averages, Volumen
"""

import logging
import numpy as np
import pandas as pd
import yfinance as yf

from stockbot.config import (
    WATCHLIST, TOP_N_SIGNALS, MIN_SIGNAL_STRENGTH,
    RSI_PERIOD, RSI_OVERSOLD, RSI_OVERBOUGHT,
    MA_SHORT, MA_LONG,
    ATR_PERIOD, ATR_SL_MULT, ATR_TP_MULT,
    BLOCK_WEEKLY_DOWNTREND, SIGNAL_TIMEFRAMES, STRENGTH_WEIGHTS,
    SL_TP_MODES, DEFAULT_SL_TP_MODE
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


def sl_tp_from_atr(price: float, atr: float | None, mode: str = DEFAULT_SL_TP_MODE) -> dict:
    """
    Berechnet Stop-Loss/Take-Profit aus dem ATR je Modus (long-only, Demo).
    Modus 'aus', unbekannter Modus oder fehlendes ATR → keine Grenzen (alle None).
    Gibt {stop_loss, take_profit, sl_pct, tp_pct, risk_reward} zurück.
    """
    none = {"stop_loss": None, "take_profit": None,
            "sl_pct": None, "tp_pct": None, "risk_reward": None}
    sl_mult, tp_mult = SL_TP_MODES.get(mode, SL_TP_MODES[DEFAULT_SL_TP_MODE])
    if mode == "aus" or sl_mult is None or tp_mult is None \
            or not atr or atr <= 0 or not price:
        return none

    stop_loss   = price - sl_mult * atr
    take_profit = price + tp_mult * atr
    return {
        "stop_loss":   stop_loss,
        "take_profit": take_profit,
        "sl_pct":      (stop_loss - price) / price * 100,    # negativ
        "tp_pct":      (take_profit - price) / price * 100,  # positiv
        "risk_reward": tp_mult / sl_mult,
    }


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


# ── Multi-Timeframe-Signalstärke (0–100) ────────────────────────────────────

def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _timeframe_components(closes: np.ndarray, volumes: np.ndarray) -> dict | None:
    """
    Aufschlüsselung der Modell-Einflussfaktoren eines EINZELNEN Zeitraums (für Long).
    Liefert die Rohwerte (RSI, MACD-Histogramm, Volumen-Verhältnis, Trend-Lage) sowie die
    daraus abgeleiteten 0..1-Teil-Scores und den gewichteten Gesamt-Score (0–100).
    None, wenn zu wenige Bars (genug für MA50 + MACD(26/9)).
    """
    n = len(closes)
    if n < 60:
        return None

    rsi = calc_rsi(closes, RSI_PERIOD)
    macd_line, macd_signal, macd_hist = calc_macd(closes)
    price = float(closes[-1])
    ma20 = float(np.mean(closes[-20:]))
    ma50 = float(np.mean(closes[-50:]))
    avg_vol = float(np.mean(volumes[-20:])) if len(volumes) >= 20 else float(np.mean(volumes))
    vol_ratio = (float(volumes[-1]) / avg_vol) if avg_vol > 0 else 1.0

    # Einzel-Komponenten je 0..1 (bullish = hoch)
    rsi_score = _clamp((65 - rsi) / 35)                       # überverkauft → stark
    if macd_line > macd_signal and macd_hist > 0:
        macd_score = 1.0
    elif macd_line > macd_signal or macd_hist > 0:
        macd_score = 0.5
    else:
        macd_score = 0.0
    if price > ma20 > ma50:
        trend_score, trend_label = 1.0, "Starker Aufwärtstrend"
    elif price > ma20:
        trend_score, trend_label = 0.66, "Aufwärtstrend"
    elif price > ma50:
        trend_score, trend_label = 0.33, "Seitwärts"
    else:
        trend_score, trend_label = 0.0, "Abwärtstrend"
    vol_score = _clamp((vol_ratio - 0.8) / 0.7)              # ab 1.5× voll bestätigt

    # Gewichtetes Mittel der Komponenten (Gewichte aus config, intern normiert).
    w = STRENGTH_WEIGHTS
    total_w = w["rsi"] + w["macd"] + w["trend"] + w["volume"]
    sub = (w["rsi"] * rsi_score + w["macd"] * macd_score
           + w["trend"] * trend_score + w["volume"] * vol_score) / total_w

    return {
        "rsi": rsi, "macd_hist": float(macd_hist), "vol_ratio": vol_ratio,
        "trend_label": trend_label,
        "rsi_score": rsi_score, "macd_score": macd_score,
        "trend_score": trend_score, "vol_score": vol_score,
        "score": round(sub * 100, 1),
    }


def compute_timeframe_score(closes: np.ndarray, volumes: np.ndarray) -> float | None:
    """Bullishness eines EINZELNEN Zeitraums als 0–100 (für Long). None bei zu wenigen Bars."""
    comp = _timeframe_components(closes, volumes)
    return comp["score"] if comp else None


def factor_history(ticker: str, days: int = 7) -> dict:
    """
    Verlauf der Modell-Einflussfaktoren (RSI/MACD/Trend/Volumen + Gesamt-Score) der letzten
    `days` Handelstage einer Aktie (Tages-Timeframe). Für die Detail-Analyse im Dashboard:
    pro Tag werden die Faktoren NUR aus den Daten bis zu diesem Tag berechnet (kein Look-ahead).
    Gibt {ticker, points:[{date, price, rsi, macd_hist, vol_ratio, trend, *_score, score}]} zurück.
    """
    try:
        data = yf.download(ticker, period="1y", interval="1d",
                           progress=False, auto_adjust=True)
    except Exception as e:
        log.warning(f"factor_history Download {ticker} fehlgeschlagen: {e}")
        return {"ticker": ticker, "points": [], "error": str(e)}

    if data is None or len(data) == 0:
        return {"ticker": ticker, "points": []}
    if isinstance(data.columns, pd.MultiIndex):
        data = data.droplevel(1, axis=1)
    data = data.dropna(subset=["Close"])
    closes = data["Close"].values.flatten().astype(float)
    volumes = data["Volume"].values.flatten().astype(float)
    n = len(closes)

    points = []
    for i in range(max(60, n - days), n):
        comp = _timeframe_components(closes[:i + 1], volumes[:i + 1])
        if comp is None:
            continue
        points.append({
            "date":        str(data.index[i].date()),
            "price":       round(float(closes[i]), 2),
            "rsi":         round(comp["rsi"], 1),
            "macd_hist":   round(comp["macd_hist"], 4),
            "vol_ratio":   round(comp["vol_ratio"], 2),
            "trend":       comp["trend_label"],
            "rsi_score":   round(comp["rsi_score"] * 100),
            "macd_score":  round(comp["macd_score"] * 100),
            "trend_score": round(comp["trend_score"] * 100),
            "vol_score":   round(comp["vol_score"] * 100),
            "score":       comp["score"],
        })
    return {"ticker": ticker, "points": points}


def _tf_scores(tf_data: dict) -> dict:
    """Berechnet je konfiguriertem Timeframe den 0–100-Subscore (oder None)."""
    out = {}
    for tf in SIGNAL_TIMEFRAMES:
        df = tf_data.get(tf["interval"])
        if df is None or len(df) == 0:
            out[tf["interval"]] = None
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df = df.droplevel(1, axis=1)
        df = df.dropna(subset=["Close"])
        closes  = df["Close"].values.flatten().astype(float)
        volumes = df["Volume"].values.flatten().astype(float)
        out[tf["interval"]] = compute_timeframe_score(closes, volumes)
    return out


def compute_strength(tf_scores: dict) -> float:
    """Gewichtetes Mittel der Timeframe-Subscores → Gesamt-Stärke 0–100 (Float).
    Gewichte aus SIGNAL_TIMEFRAMES; über die tatsächlich verfügbaren TF normiert."""
    acc = total_w = 0.0
    for tf in SIGNAL_TIMEFRAMES:
        s = tf_scores.get(tf["interval"])
        if s is None:
            continue
        acc += s * tf["weight"]
        total_w += tf["weight"]
    return round(acc / total_w, 1) if total_w > 0 else 0.0


def strength_from_tf_data(tf_data: dict) -> float:
    """Bequemer Helfer: TF-Rohdaten → Gesamtstärke (für das 60s-Monitoring)."""
    return compute_strength(_tf_scores(tf_data))


# ── Einzelne Aktie analysieren ──────────────────────────────────────────────

def analyze_ticker(ticker: str, tf_data: dict | None = None) -> dict | None:
    """
    Berechnet ein Signal für eine Aktie aus mehreren Zeiträumen.
    `tf_data` ist ein Dict {interval -> DataFrame} (Batch-Download je Timeframe).
    Kontext (Eintritts-Gate, SL/TP, S/R, Wochentrend, Kommentare) kommt aus dem 1d-TF,
    die Stärke (0–100) aus der gewichteten Multi-Timeframe-Analyse.
    Gibt None zurück, wenn kein klares Signal.
    """
    try:
        tf_data = tf_data or {}
        df = tf_data.get("1d")
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

        # Indikatoren (Tages-TF) — für Gate, SL/TP und Kommentare
        rsi = calc_rsi(closes, RSI_PERIOD)
        macd_line, macd_signal, macd_hist = calc_macd(closes)
        atr = calc_atr(highs, lows, closes, ATR_PERIOD)

        ma50  = float(np.mean(closes[-MA_SHORT:])) if len(closes) >= MA_SHORT else None
        ma200 = float(np.mean(closes[-MA_LONG:]))  if len(closes) >= MA_LONG  else None

        # Volumen-Verhältnis NUR aus abgeschlossenen Handelstagen. Während der US-Sitzung
        # ist die heutige Tageskerze unvollständig (nur das bisher gehandelte Volumen). Ein
        # Vergleich dieses Teil-Volumens mit dem Schnitt voller Tage fiele künstlich niedrig
        # aus (z. B. 0.3x), obwohl der Tag normal verläuft. Daher die heutige Kerze ausklammern.
        last_date = df.index[-1].date()
        vols_done = volumes[:-1] if last_date >= pd.Timestamp.now().date() else volumes
        if len(vols_done) >= 21:
            last_vol, avg_vol = float(vols_done[-1]), float(np.mean(vols_done[-21:-1]))
        elif len(vols_done) >= 1:
            last_vol, avg_vol = float(vols_done[-1]), float(np.mean(vols_done[-20:]))
        else:
            last_vol, avg_vol = float(volumes[-1]), float(np.mean(volumes[-20:]))
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

        # Multi-Timeframe-Stärke (0–100)
        tf_scores = _tf_scores(tf_data)
        strength = compute_strength(tf_scores)

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
            "tf_scores":      tf_scores,
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


# ── Batch-Download über mehrere Timeframes ──────────────────────────────────

def _extract(data, ticker: str):
    """Holt das OHLCV-Sub-DataFrame eines Tickers aus einem (ggf. gruppierten) Batch-Download."""
    if data is None:
        return None
    try:
        return data[ticker]
    except (KeyError, TypeError):
        # Einzel-Ticker-Download ist nicht nach Ticker gruppiert
        cols = getattr(data, "columns", None)
        if cols is None:
            return None
        level0 = cols.get_level_values(0) if isinstance(cols, pd.MultiIndex) else cols
        return data if "Close" in level0 else None


def _download_all_timeframes(tickers: list[str]) -> dict:
    """Lädt je konfiguriertem Timeframe einen Batch-Download. Gibt {interval -> data} zurück."""
    downloads = {}
    for tf in SIGNAL_TIMEFRAMES:
        try:
            downloads[tf["interval"]] = yf.download(
                tickers, period=tf["period"], interval=tf["interval"],
                progress=False, auto_adjust=True, group_by="ticker",
            )
        except Exception as e:
            log.warning(f"Download {tf['interval']} fehlgeschlagen: {e}")
            downloads[tf["interval"]] = None
    return downloads


def _tf_data_for(downloads: dict, ticker: str) -> dict:
    """Baut aus den TF-Downloads das {interval -> df}-Dict eines einzelnen Tickers."""
    tf_data = {}
    for interval, data in downloads.items():
        sub = _extract(data, ticker)
        if sub is not None:
            tf_data[interval] = sub
    return tf_data


# ── Top-Signale auswählen ───────────────────────────────────────────────────

def analyze_universe(tickers: list[str], generate=None) -> list[dict]:
    """
    Analysiert eine Ticker-Liste über ALLE konfigurierten Timeframes und gibt alle
    gefundenen Signale absteigend nach Stärke zurück (Aufrufer schneidet auf top_n).
    Pro Timeframe ein Batch-Download (yfinance fügt intern threadsicher zusammen).

    `generate(ticker, tf_data) -> signal|None` erlaubt eine andere Strategie als die
    Standard-Analyse (Default: analyze_ticker). Beide nutzen dieselben Timeframe-Daten.
    """
    if not tickers:
        return []
    gen = generate or analyze_ticker

    log.info(f"Analysiere {len(tickers)} Aktien über {len(SIGNAL_TIMEFRAMES)} Timeframes...")
    downloads = _download_all_timeframes(tickers)

    results = []
    for ticker in tickers:
        tf_data = _tf_data_for(downloads, ticker)
        if not tf_data:
            continue
        result = gen(ticker, tf_data)
        if result:
            results.append(result)

    results.sort(key=lambda x: (x["strength"], abs(x["rsi"] - 50)), reverse=True)
    log.info(f"{len(results)} Signale gefunden: {[s['ticker'] for s in results]}")
    return results


def get_top_signals(tickers: list[str] | None = None, top_n: int = TOP_N_SIGNALS) -> list[dict]:
    """Analysiert das Universum (Default: WATCHLIST) und gibt die top_n stärksten Signale zurück."""
    ranked = analyze_universe(tickers if tickers is not None else WATCHLIST)
    return ranked[:top_n]


def scan_strengths(tickers: list[str]) -> dict:
    """
    Für das 60s-Monitoring: aktueller Kurs + Live-Stärke (0–100) je Ticker.
    Gibt { ticker: {"price": float|None, "strength": float} } zurück.
    """
    if not tickers:
        return {}
    downloads = _download_all_timeframes(tickers)
    out = {}
    for ticker in tickers:
        tf_data = _tf_data_for(downloads, ticker)
        if not tf_data:
            continue
        strength = compute_strength(_tf_scores(tf_data))
        # aktueller Kurs = letzter Close des kürzesten verfügbaren Timeframes
        price = None
        for tf in SIGNAL_TIMEFRAMES:
            df = tf_data.get(tf["interval"])
            if df is None:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df = df.droplevel(1, axis=1)
            df = df.dropna(subset=["Close"])
            if len(df):
                price = float(df["Close"].values.flatten()[-1])
                break
        out[ticker] = {"price": price, "strength": strength}
    return out
