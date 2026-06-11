"""
Strategie-Abstraktion + Registry.

Jede Strategie ist ein benanntes Objekt mit einer `generate(ticker, tf_data)`-Funktion, die
ein Signal-Dict im selben Format wie `analyzer.analyze_ticker` zurückgibt (oder None). So
funktionieren Live-Bot UND Backtest mit derselben Schnittstelle.

Strategien (erste Version):
- "standard"  : die bisherige Multi-Timeframe-Momentum-Confluence (analyzer.analyze_ticker).
- "adx_trend" : nach Python portierte ADX-Trendfolge mit Volatilitäts-Expansions-Trigger,
                Quelle: trader-dev „F40d C104 — ADX 14" (Pine). Krypto-/Hebel-spezifisches
                Position-Sizing wurde bewusst entfernt — Größe/Hebel regelt der Bot selbst.
"""

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

import analyzer
from config import RSI_PERIOD, ATR_PERIOD


# ── ADX-Trendfolge (Port) ────────────────────────────────────────────────────

# Parameter aus der Pine-Vorlage (frei anpassbar):
ADX_PARAMS = {
    "adx_len":     14,
    "adx_thresh":  10,     # darunter = seitwärts → kein Einstieg
    # SL/TP volatilitätsadaptiv per ATR (statt fix %): Tageskerzen brauchen mehr Raum,
    # der ursprüngliche 2%-Fixstop wurde auf Tagesaktien zu oft ausgestoppt (PF 0,57).
    "atr_sl_mult": 2.5,    # Stop-Loss  = Einstieg − 2.5 × ATR
    "atr_tp_mult": 4.0,    # Take-Profit = Einstieg + 4.0 × ATR  (CRV 1.6)
    "detrend":     50,     # Detrend-SMA-Länge für die Hüllkurve
    "env_base":    150,    # Fenster für Z-Scores
    "exp_z":       1.0,    # Expansions-Z-Schwelle (Crossover)
    "min_vel_z":   0.5,    # Mindest-|Velocity-Z|
    "ema_regime":  200,    # Regime-Filter (Kurs > EMA200 = bullish)
}


def _clean_1d(tf_data: dict):
    """Holt das saubere 1d-OHLCV-DataFrame aus tf_data (oder None)."""
    df = (tf_data or {}).get("1d")
    if df is None or len(df) == 0:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df = df.droplevel(1, axis=1)
    df = df.dropna(subset=["Close"])
    return df if len(df) else None


def adx_trend_signal(ticker: str, tf_data: dict, p: dict | None = None) -> dict | None:
    """ADX-Trendfolge + Volatilitäts-Expansion (long-only). Entscheidung für die LETZTE Bar."""
    p = {**ADX_PARAMS, **(p or {})}
    df = _clean_1d(tf_data)
    need = p["detrend"] + p["env_base"] + 60
    if df is None or len(df) < need:
        return None

    c = df["Close"].astype(float)
    h = df["High"].astype(float)
    l = df["Low"].astype(float)
    n = p["adx_len"]

    # Wilder-Glättung = ewm(alpha=1/n) (entspricht Pine ta.rma); EMA = ewm(span) (ta.ema)
    rma = lambda s: s.ewm(alpha=1.0 / n, adjust=False).mean()
    ema_regime = c.ewm(span=p["ema_regime"], adjust=False).mean()

    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    up = h.diff()
    dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr = rma(tr)
    plus_di = 100 * rma(pd.Series(plus_dm, index=c.index)) / (atr + 1e-10)
    minus_di = 100 * rma(pd.Series(minus_dm, index=c.index)) / (atr + 1e-10)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
    adx = rma(dx)

    # Volatilitäts-Hüllkurve (Detrend → Quadratur-Filter → Amplitude → Z-Score der Steigung)
    trend = c.rolling(p["detrend"]).mean()
    osc = c - trend
    quad = 0.0962 * osc + 0.5769 * osc.shift(2) - 0.5769 * osc.shift(4) - 0.0962 * osc.shift(6)
    env = np.sqrt(osc ** 2 + quad ** 2).ewm(span=10, adjust=False).mean()
    env_slope = env - env.shift(10)
    exp_z = (env_slope - env_slope.rolling(p["env_base"]).mean()) / \
            env_slope.rolling(p["env_base"]).std().replace(0, np.nan)

    price_vel = c - c.shift(5)
    vel_z = (price_vel - price_vel.rolling(p["env_base"]).mean()) / \
            price_vel.rolling(p["env_base"]).std().replace(0, np.nan)

    # Entscheidung für die letzte Bar
    if pd.isna(exp_z.iloc[-1]) or pd.isna(exp_z.iloc[-2]) or pd.isna(adx.iloc[-1]) or pd.isna(vel_z.iloc[-1]):
        return None

    expansion_edge = exp_z.iloc[-2] <= p["exp_z"] < exp_z.iloc[-1]   # Crossover nach oben
    regime_bull = c.iloc[-1] > ema_regime.iloc[-1]
    not_sideways = adx.iloc[-1] >= p["adx_thresh"]
    pv = float(price_vel.iloc[-1])
    pvz = float(vel_z.iloc[-1])
    go_long = expansion_edge and pv > 0 and abs(pvz) > p["min_vel_z"] and regime_bull and not_sideways
    if not go_long:
        return None

    price = float(c.iloc[-1])
    adx_val = float(adx.iloc[-1])

    # Stärke 0–100 aus Trendstärke (ADX) + Velocity-Z (für Ranking/Top-N)
    adx_score = max(0.0, min(1.0, (adx_val - 10) / 30))
    vel_score = max(0.0, min(1.0, abs(pvz) / 2.0))
    strength = round((0.6 * adx_score + 0.4 * vel_score) * 100, 1)

    closes = c.values.astype(float)
    rsi = analyzer.calc_rsi(closes, RSI_PERIOD)
    macd_line, macd_signal, macd_hist = analyzer.calc_macd(closes)
    atr_abs = analyzer.calc_atr(h.values.astype(float), l.values.astype(float), closes, ATR_PERIOD)

    # SL/TP volatilitätsadaptiv per ATR; ohne gültiges ATR kein Signal (kein blinder Fix-Stop)
    if not atr_abs or atr_abs <= 0:
        return None
    stop_loss = price - p["atr_sl_mult"] * atr_abs
    take_profit = price + p["atr_tp_mult"] * atr_abs
    sl_pct = (stop_loss - price) / price * 100
    tp_pct = (take_profit - price) / price * 100
    risk_reward = p["atr_tp_mult"] / p["atr_sl_mult"]

    return {
        "ticker":         ticker,
        "price":          price,
        "as_of":          str(df.index[-1].date()),
        "direction":      "long",
        "strength":       strength,
        "strategy":       "adx_trend",
        "adx":            adx_val,
        "rsi":            rsi,
        "rsi_comment":    f"{rsi:.1f}",
        "macd_comment":   "Bullish" if (macd_hist > 0 and macd_line > macd_signal) else "Neutral",
        "trend_comment":  f"ADX {adx_val:.0f} — Trendfolge über EMA{p['ema_regime']} 📈",
        "weekly_comment": "—",
        "volume_comment": "—",
        "sr_comment":     "—",
        "macd_hist":      float(macd_hist),
        "atr":            atr_abs,
        "stop_loss":      stop_loss,
        "take_profit":    take_profit,
        "sl_pct":         sl_pct,
        "tp_pct":         tp_pct,
        "risk_reward":    risk_reward,
        "support": None, "support_touches": None, "support_dist_pct": None,
        "resistance": None, "resistance_touches": None, "resistance_dist_pct": None,
    }


# ── Weitere Strategien (verschiedene Archetypen) ─────────────────────────────

def _make_signal(ticker: str, df, key: str, strength: float,
                 sl_mult: float, tp_mult: float) -> dict | None:
    """Baut ein Signal-Dict (Long) mit ATR-basierten SL/TP + Anzeige-Feldern.
    Ohne gültiges ATR kein Signal (kein blinder Stop)."""
    closes = df["Close"].astype(float).values
    highs = df["High"].astype(float).values
    lows = df["Low"].astype(float).values
    vols = df["Volume"].astype(float).values
    price = float(closes[-1])
    atr = analyzer.calc_atr(highs, lows, closes, ATR_PERIOD)
    if not atr or atr <= 0:
        return None

    rsi = analyzer.calc_rsi(closes, RSI_PERIOD)
    macd_line, macd_signal, macd_hist = analyzer.calc_macd(closes)
    ma50 = float(np.mean(closes[-50:])) if len(closes) >= 50 else price
    ma200 = float(np.mean(closes[-200:])) if len(closes) >= 200 else price
    avg_vol = float(np.mean(vols[-20:])) if len(vols) >= 20 else float(np.mean(vols))
    vol_ratio = (float(vols[-1]) / avg_vol) if avg_vol > 0 else 1.0

    stop_loss = price - sl_mult * atr
    take_profit = price + tp_mult * atr
    trend = "Aufwärtstrend 📈" if price > ma50 > ma200 else (
        "Aufwärts 📈" if price > ma50 else "Abwärts/Seitwärts ↔")
    return {
        "ticker": ticker, "price": price, "as_of": str(df.index[-1].date()),
        "direction": "long", "strength": round(strength, 1), "strategy": key,
        "rsi": rsi, "rsi_comment": f"{rsi:.1f}",
        "macd_comment": "Bullish ✅" if (macd_hist > 0 and macd_line > macd_signal) else "Neutral",
        "trend_comment": trend, "weekly_comment": "—",
        "volume_comment": f"{vol_ratio:.1f}x Durchschnitt", "sr_comment": "—",
        "macd_hist": float(macd_hist), "atr": atr,
        "stop_loss": stop_loss, "take_profit": take_profit,
        "sl_pct": (stop_loss - price) / price * 100, "tp_pct": (take_profit - price) / price * 100,
        "risk_reward": tp_mult / sl_mult,
        "support": None, "support_touches": None, "support_dist_pct": None,
        "resistance": None, "resistance_touches": None, "resistance_dist_pct": None,
    }


def rsi_revert_signal(ticker: str, tf_data: dict) -> dict | None:
    """Mean-Reversion: kaufe den Rücksetzer (RSI<30) im langfristigen Aufwärtstrend (Kurs>MA200)."""
    df = _clean_1d(tf_data)
    if df is None or len(df) < 210:
        return None
    closes = df["Close"].astype(float).values
    price = float(closes[-1])
    rsi = analyzer.calc_rsi(closes, RSI_PERIOD)
    ma200 = float(np.mean(closes[-200:]))
    if not (rsi < 30 and price > ma200):
        return None
    strength = 50 + 50 * max(0.0, min(1.0, (35 - rsi) / 25))   # tiefer überverkauft → stärker
    return _make_signal(ticker, df, "rsi_revert", strength, sl_mult=1.5, tp_mult=2.5)


def breakout_signal(ticker: str, tf_data: dict) -> dict | None:
    """Donchian-Breakout: kaufe den Ausbruch über das 20-Tage-Hoch, bestätigt durch Trend (>MA50)."""
    df = _clean_1d(tf_data)
    if df is None or len(df) < 210:
        return None
    closes = df["Close"].astype(float).values
    highs = df["High"].astype(float).values
    vols = df["Volume"].astype(float).values
    price = float(closes[-1])
    prior_high = float(np.max(highs[-21:-1]))                  # 20-Tage-Hoch ohne heute
    ma50 = float(np.mean(closes[-50:]))
    if not (price >= prior_high and price > ma50):
        return None
    avg_vol = float(np.mean(vols[-20:]))
    vol_score = max(0.0, min(1.0, (vols[-1] / avg_vol - 0.8) / 0.7)) if avg_vol > 0 else 0.0
    strength = 60 + 40 * vol_score                             # Ausbruch + Volumen
    return _make_signal(ticker, df, "breakout", strength, sl_mult=2.0, tp_mult=4.0)


def ma_trend_signal(ticker: str, tf_data: dict) -> dict | None:
    """Trend-Ausrichtung: kaufe nur bei voll gestapeltem Aufwärtstrend (Kurs>MA20>MA50>MA200) + bullishem MACD."""
    df = _clean_1d(tf_data)
    if df is None or len(df) < 210:
        return None
    closes = df["Close"].astype(float).values
    price = float(closes[-1])
    ma20 = float(np.mean(closes[-20:]))
    ma50 = float(np.mean(closes[-50:]))
    ma200 = float(np.mean(closes[-200:]))
    macd_line, macd_signal, macd_hist = analyzer.calc_macd(closes)
    if not (price > ma20 > ma50 > ma200 and macd_hist > 0 and macd_line > macd_signal):
        return None
    # Stärke aus dem Abstand MA50↔MA200 (Trendkraft), gedeckelt
    spread = (ma50 - ma200) / ma200 * 100 if ma200 > 0 else 0.0
    strength = 60 + 40 * max(0.0, min(1.0, spread / 15))
    return _make_signal(ticker, df, "ma_trend", strength, sl_mult=1.5, tp_mult=3.0)


# ── Registry ─────────────────────────────────────────────────────────────────

@dataclass
class Strategy:
    key: str
    label: str
    generate: Callable          # (ticker, tf_data) -> signal dict | None
    description: str = ""


def _standard_generate(ticker: str, tf_data: dict) -> dict | None:
    sig = analyzer.analyze_ticker(ticker, tf_data)
    if sig is not None:
        sig.setdefault("strategy", "standard")
    return sig


REGISTRY: dict[str, Strategy] = {
    "standard": Strategy(
        "standard", "Standard (Multi-Timeframe)",
        _standard_generate,
        "Multi-Timeframe-Momentum: RSI/MACD/Trend/Volumen über 5m–1d, Stärke 0–100, ATR-SL/TP.",
    ),
    "adx_trend": Strategy(
        "adx_trend", "ADX-Trendfolge (trader-dev Port)",
        adx_trend_signal,
        "Trendfolge: Kurs>EMA200 + ADX(14)-Trend + Volatilitäts-Expansion & Velocity. ATR-SL/TP.",
    ),
    "rsi_revert": Strategy(
        "rsi_revert", "Mean-Reversion (RSI-Dip)",
        rsi_revert_signal,
        "Kauft Rücksetzer: RSI(14)<30 im langfristigen Aufwärtstrend (Kurs>MA200). ATR-SL/TP.",
    ),
    "breakout": Strategy(
        "breakout", "Donchian-Ausbruch (20T)",
        breakout_signal,
        "Kauft den Ausbruch über das 20-Tage-Hoch (Trendfilter >MA50, Volumen). Weite ATR-SL/TP.",
    ),
    "ma_trend": Strategy(
        "ma_trend", "Trend-Ausrichtung (MA20>50>200)",
        ma_trend_signal,
        "Kauft nur bei voll gestapeltem Aufwärtstrend (Kurs>MA20>MA50>MA200) + bullishem MACD.",
    ),
}

DEFAULT_STRATEGY = "standard"


def get(key: str | None) -> Strategy:
    return REGISTRY.get(key or "", REGISTRY[DEFAULT_STRATEGY])


def all_strategies() -> list[Strategy]:
    return list(REGISTRY.values())
