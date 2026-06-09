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
        "Trendfolge: Kurs>EMA200 + ADX(14)-Trend + Volatilitäts-Expansion & Velocity. Fix SL 2% / TP 5%.",
    ),
}

DEFAULT_STRATEGY = "standard"


def get(key: str | None) -> Strategy:
    return REGISTRY.get(key or "", REGISTRY[DEFAULT_STRATEGY])


def all_strategies() -> list[Strategy]:
    return list(REGISTRY.values())
